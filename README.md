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
├── shared/generator/      # ✅ personalized-opener generators (zero-dep)
│   ├── base.py            #   MessageGenerator interface + GenerationContext (tone, tail)
│   ├── schema.py          #   LeadEnrichment — the extension⇄backend contract
│   ├── template_generator.py   #   3 voice presets (casual/professional/warm)
│   └── spintax.py
├── backend/               # ✅ Phase-0 brain
│   ├── db.py              #   Postgres/Supabase = single source of truth (campaigns/contacts/events)
│   ├── models.py          #   contact state machine + cross-campaign suppression states
│   ├── ingest.py          #   extension→backend handoff (dedup + suppression)
│   ├── channels/          #   SendChannel: DryRunChannel + ServerChannel (instagrapi, lazy)
│   ├── app.py             #   thin Flask API wiring it all together
│   └── tests/
├── samples/ · tests/ · run_demo.py
└── extension/             # ▢ TODO: igscrapper, adapted (scrape + later BrowserChannel send)
```

## Try it now

```bash
# generator (no deps)
python3 run_demo.py --tone professional --regen 3 --tail "Would love to chat."
python3 -m unittest discover -s tests           # 16 tests

# backend (needs Postgres — set DATABASE_URL to a Postgres/Supabase connection)
pip install -r backend/requirements.txt
export DATABASE_URL="postgresql://user:pass@host:5432/dbname"
python3 -m unittest discover -s backend/tests    # backend suite
python3 backend/app.py                            # run the API
```

The generator **only references fields that were actually scraped** (no model =
nothing to hallucinate), varies wording via spintax so sends aren't identical,
picks a **voice preset** per campaign, and reports which fields grounded each
message for the approve-before-send UI.

## Roadmap

- **Phase 0** — ✅ one DB as source of truth · `SendChannel` interface · contact
  state-machine · one-click extension→backend ingest (dedup + suppression) · thin API.
- **Phase 1** — ✅ personalization (`TemplateGenerator`, 3 tones). ▢ wire ServerChannel into a send loop.
- **Phase 2** — true reply-aware follow-up scheduler (durable, survives restarts).
- **Phase 3** — BrowserChannel (safe-mode sending) + adaptive account-health scoring.
- **Phase 4** — lead scoring · AI reply inbox · analytics · LLM generator tier (local/free).

Still TODO from Phase 0: dashboard auth and migrating the old Flask campaign/
variable logic onto this DB.
