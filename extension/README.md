# IG Outreach Sender (Chrome extension)

The **hands**: a loadable MV3 Chrome extension that delivers the DMs your backend
queues, sending each from **your own logged-in instagram.com session** (real IP,
cookies, device fingerprint — no password on any server). It also watches your
inbox and auto-tells the backend who replied, so follow-ups stop.

## Install (Load unpacked)
1. Open **chrome://extensions**, turn on **Developer mode** (top-right).
2. **Load unpacked** → select this `extension/` folder.
3. Stay **logged into instagram.com** in this browser — use a **burner** account.
4. Click the extension icon → paste your **Railway backend URL**
   (e.g. `https://web-production-xxxx.up.railway.app`) → **Save**.
5. Click **Start sending**. That's it — no console commands.

## What it does
- Every ~30s it asks the backend for the next queued DM (`/api/agent/next`),
  sends it, and reports the result (`/api/agent/result`) so the backend advances
  the sequence.
- **Throttle:** 60–180s between DMs (randomized). Combined with the backend's
  hourly/daily caps, volume stays ban-safe.
- **Stop-on-block:** a 429 / checkpoint / feedback_required halts everything and
  logs it — walk away from that account for a day+.
- **Reply detection:** every ~2 min it checks your inbox and posts replies to
  `/api/agent/reply`, so people who answered get no more follow-ups.
- Driven by `chrome.alarms`, so it keeps working after the service worker sleeps.

## Files
- `manifest.json` — MV3 config (permissions + host access to instagram.com and
  `*.railway.app`).
- `background.js` — the agent (send + reply-watch + alarm loop). The two IG
  endpoints in here (`sendDM`, `readInbox`) are the **volatile layer** — if every
  send fails, that's what Instagram changed; fix it there.
- `popup.{html,css,js}` — the control panel (backend URL, Start/Stop, quota, log).

## Notes
- If your backend is on a **custom domain**, add it to `host_permissions` in
  `manifest.json`, then reload the extension.
- This only **sends**. Scraping leads is separate — import them via the backend's
  web console (CSV/JSON), which fills the queue this extension drains.

⚠️ Automating DMs violates Instagram's ToS. Burner account, keep it slow, stop at
the first block.
