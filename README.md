# IG Outreach Suite

A safety-first Instagram lead → outreach system. The **browser extension is the
hands** (scrape, enrich, and — later — send, all in the real logged-in session);
a **backend is the brain** (campaigns, personalization, persistent follow-ups,
analytics). Optimized for **reply-rate per unit of ban-risk**, not raw volume.

> ⚠️ Automating Instagram violates its ToS and risks action-blocks/bans. This is
> for low-volume, consent-aware outreach on accounts you can afford to lose, and
> users are responsible for ToS / anti-spam / DPDP compliance.

## Locked decisions

| Decision | Choice |
|---|---|
| Architecture | Extension = hands, backend = brain, one funnel |
| Send engine | **Hybrid** behind one `SendChannel` interface — `ServerChannel` (instagrapi) first, `BrowserChannel` (extension session) later |
| Positioning | Safety + reply-rate (low volume, qualified, approve-before-send) |
| Personalization | `TemplateGenerator` (spintax/rules) now; LLM later behind the same `MessageGenerator` interface |
| AI cost | **$0** — no paid API; LLM tier will be local (Ollama/Qwen) or a free hosted tier |

## Layout

```
ig-outreach-suite/
├── shared/generator/      # ✅ BUILT: personalized-opener generators (zero-dep)
│   ├── base.py            #   MessageGenerator interface + result types
│   ├── schema.py          #   LeadEnrichment — the extension⇄backend contract
│   ├── template_generator.py
│   └── spintax.py
├── samples/               # sample enriched leads (as the extension emits)
├── tests/                 # unittest suite (stdlib only)
├── run_demo.py            # see real openers, no backend/browser/model needed
├── extension/             # ▢ TODO: igscrapper, adapted (scrape + later send)
└── backend/               # ▢ TODO: cleaned Flask brain (campaigns/scheduler)
```

## Try the generator now

```bash
python3 run_demo.py
python3 run_demo.py --regen 3 --tail "I run a creator collab program — would love to chat."
python3 -m unittest discover -s tests
```

The generator **only references fields that were actually scraped** (no model =
nothing to hallucinate), varies wording via spintax so sends aren't identical,
and reports which fields grounded each message for the approve-before-send UI.

## Roadmap

- **Phase 0** — plumbing: one DB as source of truth, `requirements.txt`, dashboard
  auth, one-click extension→backend handoff, `SendChannel` interface + contact
  state-machine schema.
- **Phase 1** — ✅ personalization (`TemplateGenerator`) · ServerChannel send.
- **Phase 2** — true reply-aware follow-up scheduler (durable, survives restarts).
- **Phase 3** — BrowserChannel (safe-mode sending) + adaptive account-health scoring.
- **Phase 4** — lead scoring/suppression · AI reply inbox · analytics.

Phase 1's generator is built first because it's self-contained and the most
motivating piece to see working.
