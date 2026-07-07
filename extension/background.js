// background.js — the "hands" (MV3 service worker).
// ---------------------------------------------------------------------------
// Polls your outreach backend for queued DMs, sends each FROM YOUR REAL
// logged-in instagram.com session, throttles heavily, stops on block, and
// reports results back so the backend advances follow-ups. It also watches your
// IG inbox and auto-tells the backend who replied (so follow-ups stop).
//
// Driven by chrome.alarms so it keeps working after the service worker sleeps.
// All config + state lives in chrome.storage.local (set from the popup).
//
// ⚠ Sending DMs violates Instagram's ToS. Use a burner. The 60–180s spacing +
// the backend's hourly/daily caps keep volume low; stop at the first block.
// ---------------------------------------------------------------------------

const IG_APP_ID = "936619743392459";
const ALARM = "agent-tick";
const TICK_MIN = 0.5;                 // alarm period: 30s (Chrome minimum)
const GAP_MIN = 60000, GAP_MAX = 180000;   // 60–180s between DMs
const REPLY_EVERY = 120000;           // check the inbox every 2 min

const rnd = (a, b) => a + Math.floor(Math.random() * (b - a));

// ---- config / state -------------------------------------------------------
async function state() {
  const d = await chrome.storage.local.get(
    ["backend", "running", "lastSendAt", "gapMs", "lastReplyAt", "stats", "log"]);
  return {
    backend: (d.backend || "").replace(/\/+$/, ""),
    running: !!d.running,
    lastSendAt: d.lastSendAt || 0,
    gapMs: d.gapMs || 0,
    lastReplyAt: d.lastReplyAt || 0,
    stats: d.stats || { sent: 0, failed: 0 },
    log: d.log || [],
  };
}
const set = (o) => chrome.storage.local.set(o);
async function bump(key) { const s = await state(); s.stats[key]++; await set({ stats: s.stats }); }
async function log(msg) {
  const { log } = await chrome.storage.local.get("log");
  const arr = log || [];
  arr.unshift(`${new Date().toLocaleTimeString()}  ${msg}`);
  await set({ log: arr.slice(0, 60) });
}

// ---- instagram session helpers --------------------------------------------
async function cookie(name) {
  const c = await chrome.cookies.get({ url: "https://www.instagram.com", name });
  return c ? c.value : "";
}
const loggedIn = async () => !!(await cookie("sessionid"));

async function resolveUserId(username) {
  const url = `https://www.instagram.com/api/v1/users/web_profile_info/?username=${encodeURIComponent(username)}`;
  const res = await fetch(url, {
    credentials: "include",
    headers: { "x-ig-app-id": IG_APP_ID, "x-requested-with": "XMLHttpRequest", accept: "application/json" },
  });
  if ([401, 403, 429].includes(res.status)) { const e = new Error(`HTTP ${res.status}`); e.blocked = true; throw e; }
  const j = await res.json();
  const id = j?.data?.user?.id;
  if (!id) throw new Error("could not resolve user id");
  return id;
}

// === VOLATILE: Instagram's DM endpoint. If every send fails, fix it HERE. ===
async function sendDM(userId, text) {
  const body = new URLSearchParams({
    recipient_users: `[[${userId}]]`, action: "send_item",
    client_context: crypto.randomUUID(), text,
  });
  const res = await fetch("https://www.instagram.com/api/v1/direct_v2/threads/broadcast/text/", {
    method: "POST", credentials: "include",
    headers: {
      "x-csrftoken": await cookie("csrftoken"), "x-ig-app-id": IG_APP_ID,
      "x-requested-with": "XMLHttpRequest", "content-type": "application/x-www-form-urlencoded",
    },
    body: body.toString(),
  });
  if ([401, 403, 429].includes(res.status)) { const e = new Error(`HTTP ${res.status}`); e.blocked = true; throw e; }
  let j = {};
  try { j = await res.json(); } catch { /* some sends return empty */ }
  if (j && (j.message === "checkpoint_required" || j.message === "feedback_required")) {
    const e = new Error(j.message); e.blocked = true; throw e;
  }
  if (j && j.status && j.status !== "ok") throw new Error(j.status);
  return true;
}

// === VOLATILE: the inbox read used for reply detection. ===
async function readInbox() {
  const res = await fetch("https://www.instagram.com/api/v1/direct_v2/inbox/?persistentBadging=true&limit=20", {
    credentials: "include",
    headers: { "x-ig-app-id": IG_APP_ID, "x-requested-with": "XMLHttpRequest", accept: "application/json" },
  });
  const j = await res.json();
  return j?.inbox?.threads || [];
}

// ---- backend calls --------------------------------------------------------
const apiGet = (b, p) => fetch(b + p).then((r) => r.json());
const apiPost = (b, p, body) =>
  fetch(b + p, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });

// ---- one unit of work -----------------------------------------------------
async function sendOne(backend) {
  let items = [];
  try { items = await apiGet(backend, "/api/agent/next?limit=1"); }
  catch { await log("Backend unreachable — check the URL and that it's running."); return; }
  if (!Array.isArray(items) || !items.length) return;      // nothing queued right now
  const it = items[0];
  try {
    const uid = await resolveUserId(it.username);
    await sendDM(uid, it.text);
    await apiPost(backend, "/api/agent/result", { id: it.id, status: "ok" });
    await bump("sent");
    await log(`✓ Sent DM to @${it.username}`);
  } catch (e) {
    if (e.blocked) {
      await apiPost(backend, "/api/agent/result", { id: it.id, status: "blocked" });
      await set({ running: false });
      await chrome.alarms.clear(ALARM);
      await log(`⛔ BLOCKED by Instagram (${e.message}) — STOPPED. Rest this account a day+.`);
    } else {
      await apiPost(backend, "/api/agent/result", { id: it.id, status: "failed" });
      await bump("failed");
      await log(`✗ Failed @${it.username}: ${e.message}`);
    }
  }
}

async function replyWatch(backend) {
  let watch;
  try { watch = new Set(await apiGet(backend, "/api/agent/watchlist")); } catch { return; }
  if (!watch.size) return;
  const me = await cookie("ds_user_id");
  let threads = [];
  try { threads = await readInbox(); } catch { return; }
  for (const t of threads) {
    const other = (t.users || []).find((u) => String(u.pk) !== String(me));
    if (!other || !watch.has(other.username)) continue;
    const last = (t.items || [])[0];
    if (last && String(last.user_id) !== String(me)) {           // last message is inbound = replied
      await apiPost(backend, "/api/agent/reply", { username: other.username }).catch(() => {});
      await log(`↩ Reply from @${other.username} — follow-ups stopped.`);
    }
  }
}

async function tick() {
  const s = await state();
  if (!s.running) { await chrome.alarms.clear(ALARM); return; }
  if (!s.backend) { await log("No backend URL set."); return; }
  if (!(await loggedIn())) { await log("Not logged into instagram.com — waiting."); return; }

  const now = Date.now();
  if (now - s.lastReplyAt >= REPLY_EVERY) {
    await set({ lastReplyAt: now });
    await replyWatch(s.backend);
  }
  if (now - s.lastSendAt >= (s.gapMs || 0)) {
    await set({ lastSendAt: Date.now(), gapMs: rnd(GAP_MIN, GAP_MAX) });   // reserve the slot first
    await sendOne(s.backend);
  }
}

// ---- wake-ups + popup messaging (listeners registered at top level) -------
chrome.alarms.onAlarm.addListener((a) => { if (a.name === ALARM) tick(); });

chrome.runtime.onMessage.addListener((m, _s, reply) => {
  (async () => {
    if (m.type === "START") {
      await set({ running: true, backend: (m.backend || "").replace(/\/+$/, ""), lastSendAt: 0, gapMs: 0 });
      await chrome.alarms.create(ALARM, { periodInMinutes: TICK_MIN });
      await log("▶ Started sending.");
      tick();
      reply({ ok: true });
    } else if (m.type === "STOP") {
      await set({ running: false });
      await chrome.alarms.clear(ALARM);
      await log("■ Stopped.");
      reply({ ok: true });
    } else if (m.type === "SAVE_BACKEND") {
      await set({ backend: (m.backend || "").replace(/\/+$/, "") });
      reply({ ok: true });
    } else if (m.type === "SEND_NOW") {
      const s = await state();
      if (s.backend && (await loggedIn())) await sendOne(s.backend);
      reply({ ok: true });
    } else if (m.type === "STATUS") {
      const s = await state();
      let health = null, quota = null;
      if (s.backend) {
        try {
          const r = await fetch(s.backend + "/api/health");
          health = await r.json();                 // {ok, db, error?} even on 500
        } catch { health = null; }                 // truly unreachable (bad URL / offline)
        if (health && health.ok) {
          try { quota = await apiGet(s.backend, "/api/agent/quota"); } catch { /* ignore */ }
        }
      }
      reply({ ...s, loggedIn: await loggedIn(), health, quota });
    }
  })();
  return true;   // async response
});
