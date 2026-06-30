"""CSV → list[dict] for ingestion.

Accepts the igscrapper full-enrichment CSV (its headers already match
LeadEnrichment fields) AND arbitrary contact lists: it finds the Instagram
handle column by name or by sniffing values, extracts the username from a
profile URL / @mention, and keeps every other column so name/company/etc. ride
into enrichment_json for future variable substitution.
"""

import csv
import io
import re

USERNAME_COLS = ["username", "instagram", "ig", "handle", "profile",
                 "profile_url", "url", "account"]
_URL = re.compile(r"instagram\.com/([^/?#\s]+)", re.I)


def _extract(value: str) -> str:
    v = str(value or "").strip()
    if not v:
        return ""
    m = _URL.search(v)
    if m:
        return m.group(1).lstrip("@").strip()
    return v.lstrip("@").split("/")[0].strip()


def _looks_like_handle(value) -> bool:
    v = str(value or "")
    return "instagram.com" in v.lower() or v.strip().startswith("@")


def rows_from_csv(text: str) -> list:
    """Parse CSV text into row dicts, each with a normalized 'username' key."""
    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames or []
    lower = {f.lower().strip(): f for f in fields if f}
    ucol = next((lower[c] for c in USERNAME_COLS if c in lower), None)

    out = []
    for row in reader:
        d = {k: v for k, v in row.items() if k is not None}
        handle = _extract(d.get(ucol, "")) if ucol else ""
        if not handle:  # fall back to sniffing any cell that looks like a handle/URL
            for v in d.values():
                if _looks_like_handle(v):
                    handle = _extract(v)
                    break
        d["username"] = handle
        out.append(d)
    return out
