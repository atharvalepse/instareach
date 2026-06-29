"""Thin Flask brain — wires DB + ingest + generator behind a small API.

The real logic lives in db.py / ingest.py / shared.generator; these routes are
deliberately thin so everything stays unit-testable without a running server.

    pip install -r backend/requirements.txt
    python3 backend/app.py            # http://localhost:5000

Endpoints:
    POST /api/campaigns                       {name, tone?, tail?}
    GET  /api/campaigns
    POST /api/campaigns/<id>/leads            [ {scraped row}, ... ]   <- extension handoff
    GET  /api/campaigns/<id>/contacts
    POST /api/campaigns/<id>/preview          {n?}                      <- generated openers
"""

import os
import sys
import time

from flask import Flask, jsonify, request, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))  # repo root
from shared.generator import GenerationContext, LeadEnrichment, TemplateGenerator  # noqa: E402

from db import connect  # noqa: E402
from ingest import ingest_leads  # noqa: E402

DB_PATH = os.environ.get("OUTREACH_DB", os.path.join(os.path.dirname(__file__), "outreach.db"))
app = Flask(__name__)
GEN = TemplateGenerator()


def conn():
    return connect(DB_PATH)


@app.get("/")
def home():
    return send_from_directory(os.path.join(os.path.dirname(__file__), "static"), "index.html")


@app.get("/api/sample-leads")
def sample_leads():
    import json
    p = os.path.join(os.path.dirname(os.path.dirname(__file__)), "samples", "enriched_leads.json")
    with open(p, encoding="utf-8") as f:
        return jsonify(json.load(f))


@app.post("/api/campaigns")
def create_campaign():
    d = request.get_json(force=True) or {}
    if not d.get("name"):
        return jsonify(error="name required"), 400
    cid = f"camp_{int(time.time() * 1000)}"
    c = conn()
    c.execute(
        "INSERT INTO campaigns (id, name, tone, tail) VALUES (?,?,?,?)",
        (cid, d["name"], d.get("tone", "casual"), d.get("tail", "")),
    )
    c.commit()
    return jsonify(campaign_id=cid), 201


@app.get("/api/campaigns")
def list_campaigns():
    rows = conn().execute("SELECT * FROM campaigns ORDER BY created_at DESC").fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/campaigns/<cid>/leads")
def add_leads(cid):
    body = request.get_json(force=True) or []
    rows = body.get("leads") if isinstance(body, dict) else body
    if not isinstance(rows, list):
        return jsonify(error="expected a JSON array of leads"), 400
    summary = ingest_leads(conn(), cid, rows)
    return jsonify(summary.as_dict())


@app.get("/api/campaigns/<cid>/contacts")
def list_contacts(cid):
    rows = conn().execute(
        "SELECT id, username, state, message_number FROM contacts WHERE campaign_id = ?", (cid,)
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/campaigns/<cid>/preview")
def preview(cid):
    import json
    n = int((request.get_json(silent=True) or {}).get("n", 5))
    c = conn()
    camp = c.execute("SELECT tone, tail FROM campaigns WHERE id = ?", (cid,)).fetchone()
    if not camp:
        return jsonify(error="campaign not found"), 404
    ctx = GenerationContext(tone=camp["tone"], tail=camp["tail"])
    rows = c.execute(
        "SELECT username, enrichment_json FROM contacts WHERE campaign_id=? AND state='queued' LIMIT ?",
        (cid, n),
    ).fetchall()
    out = []
    for r in rows:
        lead = LeadEnrichment.from_dict(json.loads(r["enrichment_json"]))
        msg = GEN.generate(lead, ctx)
        out.append({"username": r["username"], "message": msg.text,
                    "grounded": msg.grounded, "used_fields": msg.used_fields})
    return jsonify(out)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="127.0.0.1", port=port, debug=False)
