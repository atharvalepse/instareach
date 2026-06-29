#!/usr/bin/env python3
"""Demo: generate personalized openers from sample scraped leads.

    python3 run_demo.py
    python3 run_demo.py --tail "I run a small studio and would love to collab."
    python3 run_demo.py --regen 3      # show 3 variants per lead

No backend, no browser, no model, no network — pure template tier.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from shared.generator import GenerationContext, LeadEnrichment, TemplateGenerator  # noqa: E402

SAMPLES = os.path.join(os.path.dirname(__file__), "samples", "enriched_leads.json")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tail", default="", help="campaign ask appended after the hook")
    ap.add_argument("--regen", type=int, default=1, help="variants to show per lead")
    args = ap.parse_args()

    rows = json.load(open(SAMPLES, encoding="utf-8"))
    gen = TemplateGenerator()
    ctx = GenerationContext(tail=args.tail)

    print(f"\nTemplateGenerator demo — {len(rows)} leads, {args.regen} variant(s) each\n" + "=" * 64)
    for row in rows:
        lead = LeadEnrichment.from_dict(row)
        flags = []
        if lead.is_business:
            flags.append("business")
        if lead.activity_kind:
            flags.append(lead.activity_kind)
        print(f"\n@{lead.username}  ({lead.full_name or 'no name'})  [{', '.join(flags) or 'thin'}]")
        for attempt in range(args.regen):
            msg = gen.generate(lead, ctx, attempt=attempt)
            mark = "✓ grounded" if msg.grounded else "· generic"
            print(f"   {mark:12} {msg.text}")
            print(f"   {'':12} \033[2m{msg.explain()}\033[0m")
    print()


if __name__ == "__main__":
    main()
