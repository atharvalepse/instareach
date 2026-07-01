// browser_channel.js — the "hands" of the outreach suite.
// ---------------------------------------------------------------------------
// Runs inside the igscrapper MV3 extension (merge into background.js, or add as
// a second module). It polls the backend outbox, sends each DM FROM THE USER'S
// REAL logged-in instagram.com session (same cookies/IP/fingerprint the scraper
// already uses), and reports the result back. No password, no server-side login.
//
// SAFETY: this actually sends DMs and violates Instagram's ToS. Use a burner,
// keep the throttle high, and stop the moment you see a block.
//
// WIRING (in the igscrapper extension):
//   1. manifest host_permissions must include BOTH:
//        "https://www.instagram.com/*"   (already there)
//        "http://127.0.0.1/*"            (or your deployed backend origin)
//   2. import/paste this file into the MV3 service worker (background.js).
//   3. call startAgent() to begin, stopAgent() to halt.
// ---------------------------------------------------------------------------

const AGENT = {
  BACKEND: "http://127.0.0.1:5000",   // where the brain lives
  APP_ID: "936619743392459",          // instagram.com web app id (same as scraper)
  pollMs: 15000,                       // how often to ask the backend for work
  batch: 3,                            // max sends fetched per poll
  // throttle between individual sends — DMs are far riskier than reads, go slow
  baseMs: 45000,                       // 45s minimum gap between DMs
  jitterMs: 45000,                     // + up to 45s random
};

let AGENT_RUNNING = false;

const _sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const _gap = () => AGENT.baseMs + Math.floor(Math.random() * AGENT.jitterMs);
const _uuid = () =>
  "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });

async function _csrf() {
  const c = await chrome.cookies.get({ url: "https://www.instagram.com", name: "csrftoken" });
  return c ? c.value : "";
}

async function _loggedIn() {
  const c = await chrome.cookies.get({ url: "https://www.instagram.com", name: "sessionid" });
  return !!c;
}

// Resolve @username -> numeric user id (same endpoint the scraper hydrates with).
async function resolveUserId(username) {
  const url = `https://www.instagram.com/api/v1/users/web_profile_info/?username=${encodeURIComponent(username)}`;
  const res = await fetch(url, {
    credentials: "include",
    headers: { "x-ig-app-id": AGENT.APP_ID, "x-requested-with": "XMLHttpRequest", accept: "application/json" },
  });
  if (res.status === 429 || res.status === 401 || res.status === 403) {
    const e = new Error(`HTTP ${res.status}`); e.blocked = true; throw e;
  }
  const json = await res.json();
  const id = json?.data?.user?.id;
  if (!id) throw new Error("could not resolve user id");
  return id;
}

// ===========================================================================
// VOLATILE LAYER — Instagram changes this DM endpoint/params periodically.
// If every send suddenly fails, THIS is what needs updating (like the scraper's
// ENDPOINTS block). Everything else is stable.
// ===========================================================================
async function sendDM(userId, text) {
  const csrf = await _csrf();
  const body = new URLSearchParams({
    recipient_users: `[[${userId}]]`,
    action: "send_item",
    client_context: _uuid(),
    text,
  });
  const res = await fetch("https://www.instagram.com/api/v1/direct_v2/threads/broadcast/text/", {
    method: "POST",
    credentials: "include",
    headers: {
      "x-csrftoken": csrf,
      "x-ig-app-id": AGENT.APP_ID,
      "x-requested-with": "XMLHttpRequest",
      "content-type": "application/x-www-form-urlencoded",
    },
    body: body.toString(),
  });

  if (res.status === 429) { const e = new Error("HTTP 429 rate limited"); e.blocked = true; throw e; }
  if (res.status === 401 || res.status === 403) { const e = new Error(`HTTP ${res.status}`); e.blocked = true; throw e; }
  let json = {};
  try { json = await res.json(); } catch { /* some sends return empty body */ }
  if (json && (json.message === "checkpoint_required" || json.message === "feedback_required")) {
    const e = new Error(json.message); e.blocked = true; throw e;
  }
  if (json && json.status && json.status !== "ok") throw new Error(json.status);
  return true;
}

async function _report(id, status) {
  await fetch(`${AGENT.BACKEND}/api/agent/result`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ id, status }),
  }).catch(() => {});
}

// One poll cycle: fetch pending work, deliver each with pacing, report back.
async function pollOnce() {
  if (!(await _loggedIn())) return { stopped: "not logged into instagram.com" };
  let items = [];
  try {
    const res = await fetch(`${AGENT.BACKEND}/api/agent/next?limit=${AGENT.batch}`);
    items = await res.json();
  } catch {
    return { error: "backend unreachable" };
  }
  for (const item of items) {
    if (!AGENT_RUNNING) break;
    await _sleep(_gap());                 // throttle BEFORE each send
    try {
      const uid = await resolveUserId(item.username);
      await sendDM(uid, item.text);
      await _report(item.id, "ok");
    } catch (e) {
      if (e.blocked) {
        await _report(item.id, "blocked"); // backend pauses the campaign
        AGENT_RUNNING = false;             // hard stop the whole agent
        return { stopped: `blocked: ${e.message}` };
      }
      await _report(item.id, "failed");    // soft skip (private/deleted/etc.)
    }
  }
  return { processed: items.length };
}

async function startAgent() {
  if (AGENT_RUNNING) return;
  AGENT_RUNNING = true;
  while (AGENT_RUNNING) {
    const r = await pollOnce();
    if (r.stopped) { console.warn("[agent] stopped:", r.stopped); break; }
    await _sleep(AGENT.pollMs);
  }
  AGENT_RUNNING = false;
}

function stopAgent() { AGENT_RUNNING = false; }

// ===========================================================================
// AUTO REPLY-POLLER — watches the IG inbox and tells the backend who replied,
// so follow-ups auto-stop with no manual marking. Reading the inbox is the
// other VOLATILE layer (endpoint/shape changes periodically — fix HERE).
// ===========================================================================
let WATCH_RUNNING = false;

async function readInbox() {
  const res = await fetch("https://www.instagram.com/api/v1/direct_v2/inbox/?persistentBadging=true&limit=20", {
    credentials: "include",
    headers: { "x-ig-app-id": AGENT.APP_ID, "x-requested-with": "XMLHttpRequest", accept: "application/json" },
  });
  const json = await res.json();
  return (json?.inbox?.threads) || [];
}

async function replyWatchOnce() {
  let watch;
  try {
    watch = new Set(await (await fetch(`${AGENT.BACKEND}/api/agent/watchlist`)).json());
  } catch { return { error: "backend unreachable" }; }
  if (!watch.size) return { watched: 0 };

  const me = (await chrome.cookies.get({ url: "https://www.instagram.com", name: "ds_user_id" }))?.value;
  let threads = [];
  try { threads = await readInbox(); } catch { return { error: "inbox read failed" }; }

  let found = 0;
  for (const t of threads) {
    const other = (t.users || []).find((u) => String(u.pk) !== String(me));
    if (!other || !watch.has(other.username)) continue;
    const last = (t.items || [])[0];               // newest message first
    if (last && String(last.user_id) !== String(me)) {   // last message is inbound = they replied
      await fetch(`${AGENT.BACKEND}/api/agent/reply`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ username: other.username }),
      }).catch(() => {});
      found++;
    }
  }
  return { watched: watch.size, replies: found };
}

async function startReplyWatch(intervalMs = 120000) {
  if (WATCH_RUNNING) return;
  WATCH_RUNNING = true;
  while (WATCH_RUNNING) {
    if (await _loggedIn()) await replyWatchOnce();
    await _sleep(intervalMs);
  }
}
function stopReplyWatch() { WATCH_RUNNING = false; }

// One call to fully automate: deliver queued DMs AND auto-detect replies.
function startAll() { startAgent(); startReplyWatch(); }
function stopAll() { stopAgent(); stopReplyWatch(); }

// Expose for wiring into the popup / background message router.
if (typeof self !== "undefined") {
  self.startAgent = startAgent;
  self.stopAgent = stopAgent;
  self.pollOnce = pollOnce;   // handy for a single manual "send now" trigger
  self.startReplyWatch = startReplyWatch;
  self.stopReplyWatch = stopReplyWatch;
  self.startAll = startAll;   // ← the "max automation" entry point
  self.stopAll = stopAll;
}
