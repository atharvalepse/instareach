# Extension (hands)

The browser extension is the **execution layer** — it acts inside your real,
logged-in instagram.com session (real IP, cookies, device fingerprint), so
Instagram sees ordinary activity, not a server logging in.

## `browser_channel.js` — the send agent (BrowserChannel)

Polls the backend outbox, resolves each `@username` → user id, sends the DM from
your session, and reports the result. This is the safe delivery path (no
password on a server, no datacenter IP).

Flow:
```
backend scheduler → outbox (pending)
   GET  /api/agent/next     → extension pulls work
   (resolve id, send DM from your IG session, throttle, stop-on-block)
   POST /api/agent/result   → backend advances contact state / schedules follow-up
```

### Wiring into the igscrapper MV3 extension
1. `manifest.json` `host_permissions` must include **both**:
   `"https://www.instagram.com/*"` and your backend origin
   `"http://127.0.0.1/*"` (or the deployed URL).
2. Add `browser_channel.js` to the MV3 service worker (import in `background.js`
   or list it alongside it), and set `AGENT.BACKEND` to your backend URL.
3. Call `startAgent()` to begin delivering, `stopAgent()` to halt. `pollOnce()`
   sends the current batch once (good for a manual "send now" button).

### Volatile layer
The only fragile part is the Instagram DM endpoint in `sendDM()`
(`direct_v2/threads/broadcast/text/`). If every send fails, that endpoint/params
changed — fix it there, like the scraper's `ENDPOINTS` block.

## Still TODO
Fold in the full igscrapper scraper (scrape → enrich) and add a "Send to
campaign →" button that POSTs enriched leads to
`POST /api/campaigns/<id>/leads`, so scrape → outreach is one flow.

⚠️ Automating DMs violates Instagram's ToS. Use a burner account, keep the
throttle high (default 45–90s between DMs), and stop at the first block.
