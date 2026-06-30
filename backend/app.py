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

sys.path.insert(0, os.path.dirname(__file__))                   # backend/
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))  # repo root
from shared.generator import GenerationContext, LeadEnrichment, TemplateGenerator  # noqa: E402

from db import connect  # noqa: E402
from ingest import ingest_leads  # noqa: E402
from csv_import import rows_from_csv  # noqa: E402
from channels import DryRunChannel  # noqa: E402
import scheduler  # noqa: E402

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


def _build_sequence(d):
    """Normalize a sequence: [{body, wait_hours}]. Step 0 wait is always 0.
    Falls back to a single step derived from `tail` for back-compat."""
    import json
    seq = d.get("sequence")
    if not seq:
        seq = [{"body": d.get("tail", ""), "wait_hours": 0}]
    clean = []
    for i, step in enumerate(seq):
        clean.append({
            "body": str(step.get("body", "")).strip(),
            "wait_hours": 0 if i == 0 else float(step.get("wait_hours", 48)),
        })
    return json.dumps(clean)


@app.post("/api/campaigns")
def create_campaign():
    d = request.get_json(force=True) or {}
    if not d.get("name"):
        return jsonify(error="name required"), 400
    cid = f"camp_{int(time.time() * 1000)}"
    c = conn()
    c.execute(
        "INSERT INTO campaigns (id, name, tone, tail, sequence_json) VALUES (?,?,?,?,?)",
        (cid, d["name"], d.get("tone", "casual"), d.get("tail", ""), _build_sequence(d)),
    )
    c.commit()
    return jsonify(campaign_id=cid), 201


@app.post("/api/campaigns/<cid>/start")
def start_campaign(cid):
    c = conn()
    if not c.execute("SELECT 1 FROM campaigns WHERE id=?", (cid,)).fetchone():
        return jsonify(error="campaign not found"), 404
    c.execute("UPDATE campaigns SET status='running' WHERE id=?", (cid,))
    c.commit()
    return jsonify(status="running")


@app.post("/api/campaigns/<cid>/tick")
def tick(cid):
    """Run one pass of the follow-up loop (DryRunChannel — nothing is sent to IG).
    Optional advance_hours fast-forwards `now` so follow-ups can be demoed."""
    from datetime import datetime, timedelta
    body = request.get_json(silent=True) or {}
    now = datetime.utcnow() + timedelta(hours=float(body.get("advance_hours", 0)))
    summary = scheduler.run_due(conn(), DryRunChannel(), GEN, now=now)
    return jsonify(summary)


@app.post("/api/campaigns/<cid>/contacts/<username>/event")
def contact_event(cid, username):
    d = request.get_json(force=True) or {}
    ok = scheduler.mark_event(conn(), cid, username, d.get("type", ""))
    return (jsonify(ok=True) if ok else (jsonify(error="contact not found or bad type"), 400))


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


@app.post("/api/campaigns/<cid>/leads/csv")
def add_leads_csv(cid):
    """Accept a CSV upload (raw text/csv body or multipart 'file')."""
    if "file" in request.files:
        text = request.files["file"].read().decode("utf-8", errors="replace")
    else:
        text = request.get_data(as_text=True)
    if not text.strip():
        return jsonify(error="empty CSV"), 400
    rows = rows_from_csv(text)
    summary = ingest_leads(conn(), cid, rows)
    return jsonify(summary.as_dict())


@app.get("/api/campaigns/<cid>/contacts")
def list_contacts(cid):
    rows = conn().execute(
        """SELECT id, username, state, message_number, next_action_at, last_message
           FROM contacts WHERE campaign_id = ? ORDER BY id""", (cid,)
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/campaigns/<cid>/preview")
def preview(cid):
    import json
    n = int((request.get_json(silent=True) or {}).get("n", 5))
    c = conn()
    camp = c.execute("SELECT tone, tail, sequence_json FROM campaigns WHERE id = ?", (cid,)).fetchone()
    if not camp:
        return jsonify(error="campaign not found"), 404
    seq = json.loads(camp["sequence_json"] or "[]")
    step0_body = seq[0]["body"] if seq else camp["tail"]
    ctx = GenerationContext(tone=camp["tone"])
    rows = c.execute(
        "SELECT username, enrichment_json FROM contacts WHERE campaign_id=? AND state='queued' LIMIT ?",
        (cid, n),
    ).fetchall()
    out = []
    for r in rows:
        lead = LeadEnrichment.from_dict(json.loads(r["enrichment_json"]))
        # show exactly what message 1 will be (reuses the scheduler's composer)
        text = scheduler.compose_message(GEN, lead, ctx, 0, step0_body)
        g = GEN.generate(lead, GenerationContext(tone=camp["tone"], tail=step0_body))
        out.append({"username": r["username"], "message": text,
                    "grounded": g.grounded, "used_fields": g.used_fields})
    return jsonify(out)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="127.0.0.1", port=port, debug=False)
