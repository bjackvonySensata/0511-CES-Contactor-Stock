from flask import Flask, render_template, request, redirect, url_for, flash
from supabase import create_client, Client
import os
from datetime import datetime

app = Flask(__name__)
app.secret_key = "supersecret"  # needed for flash messages

# --- Supabase setup ---
url: str = os.getenv("SUPABASE_URL", "https://YOUR-PROJECT.supabase.co")
key: str = os.getenv("SUPABASE_KEY", "YOUR-ANON-KEY")
supabase: Client = create_client(url, key)

# --- Helpers ---
def get_request_progress(request_id):
    items = supabase.table("request_items").select("*").eq("request_id", request_id).execute().data
    scanned = sum(i["scanned_qty"] for i in items)
    needed = sum(i["qty_needed"] for i in items)
    return scanned, needed

# --- Routes ---
@app.route("/")
def home():
    total_parts = supabase.table("parts").select("*").execute().data or []
    total_requests = supabase.table("bom_requests").select("*").execute().data or []
    return render_template("index.html", parts=total_parts, requests=total_requests)

@app.route("/dashboard")
def dashboard():
    parts = supabase.table("parts").select("*").execute().data or []
    return render_template("dashboard.html", parts=parts)

@app.route("/bom_dashboard")
def bom_dashboard():
    q = supabase.table("bom_requests").select("*").order("created_at", desc=True).execute().data or []
    for req in q:
        scanned, needed = get_request_progress(req["request_id"])
        req["scanned"] = scanned
        req["needed"] = needed
    return render_template("bom_dashboard.html", requests=q)

@app.route("/create_request", methods=["POST"])
def create_request():
    product_id = request.form["product_id"].strip()
    requested_by = request.form["requested_by"].strip()

    # check if BOM exists
    bom = supabase.table("bom_parts").select("*").eq("product_id", product_id).execute().data
    if not bom:
        flash(f"⚠️ No BOM found for product {product_id}")
        return redirect(url_for("home"))

    # create BOM request
    req = supabase.table("bom_requests").insert({
        "product_id": product_id,
        "requested_by": requested_by,
        "status": "open"
    }).execute().data[0]

    # insert request items
    for item in bom:
        supabase.table("request_items").insert({
            "request_id": req["request_id"],
            "part_id": item["part_id"],
            "qty_needed": item["qty_needed"],
            "scanned_qty": 0
        }).execute()

    flash("✅ Request created successfully")
    return redirect(url_for("bom_dashboard"))

@app.route("/request/<int:request_id>", methods=["GET", "POST"])
def handle_request(request_id):
    r = supabase.table("bom_requests").select("*").eq("request_id", request_id).execute().data
    if not r:
        return "Request not found", 404
    req = r[0]

    items = supabase.table("request_items").select("*").eq("request_id", request_id).execute().data or []

    if request.method == "POST":
        if "cancel" in request.form:
            supabase.table("bom_requests").update({"status": "cancelled"}).eq("request_id", request_id).execute()
            flash("❌ Request cancelled")
            return redirect(url_for("bom_dashboard"))

        part_id = request.form["part_id"].strip()

        # check part exists
        part_data = supabase.table("parts").select("*").eq("part_id", part_id).execute().data
        if not part_data:
            flash(f"⚠️ Part {part_id} does not exist")
            return redirect(url_for("handle_request", request_id=request_id))

        # update scanned qty
        for item in items:
            if item["part_id"] == part_id and item["scanned_qty"] < item["qty_needed"]:
                supabase.table("request_items").update({
                    "scanned_qty": item["scanned_qty"] + 1
                }).eq("id", item["id"]).execute()

                # decrement inventory
                part = part_data[0]
                supabase.table("parts").update({
                    "quantity": part["quantity"] - 1
                }).eq("part_id", part_id).execute()

                # add transaction log
                supabase.table("transactions").insert({
                    "part_id": part_id,
                    "change_qty": -1,
                    "reason": f"Request {request_id} scan",
                    "timestamp": datetime.utcnow().isoformat()
                }).execute()
                break

        flash(f"✅ Scanned part {part_id}")
        return redirect(url_for("handle_request", request_id=request_id))

    scanned, needed = get_request_progress(request_id)
    return render_template("request.html", req=req, items=items, scanned=scanned, needed=needed)

@app.route("/restock", methods=["POST"])
def restock():
    part_id = request.form["part_id"].strip()
    qty = int(request.form["qty"])

    # check if part exists
    part = supabase.table("parts").select("*").eq("part_id", part_id).execute().data
    if not part:
        flash(f"⚠️ Part {part_id} not found")
        return redirect(url_for("dashboard"))

    current_qty = part[0]["quantity"]
    supabase.table("parts").update({"quantity": current_qty + qty}).eq("part_id", part_id).execute()

    supabase.table("transactions").insert({
        "part_id": part_id,
        "change_qty": qty,
        "reason": "Restock",
        "timestamp": datetime.utcnow().isoformat()
    }).execute()

    flash(f"✅ Restocked {qty} of {part_id}")
    return redirect(url_for("dashboard"))

if __name__ == "__main__":
    app.run(debug=True)
