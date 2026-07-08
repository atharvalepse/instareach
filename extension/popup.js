const $ = (id) => document.getElementById(id);
const msg = (m) => chrome.runtime.sendMessage(m).catch(() => null);

async function refresh() {
  const s = await msg({ type: "STATUS" });
  if (!s) return;
  $("loginWarn").hidden = s.loggedIn;
  if (document.activeElement !== $("backend")) $("backend").value = s.backend || "";

  $("sent").textContent = s.stats?.sent ?? 0;
  $("failed").textContent = s.stats?.failed ?? 0;
  $("remaining").textContent = s.quota ? s.quota.remaining : "–";

  // connection line — distinguish unreachable vs up-but-DB-broken
  const conn = $("conn");
  if (!s.backend) { conn.textContent = "no backend URL set"; conn.className = "conn"; }
  else if (s.health && s.health.ok && s.quota) {
    conn.textContent = `connected · ${s.quota.sent_last_day}/${s.quota.daily_cap} sent today`; conn.className = "conn ok";
  } else if (s.health && !s.health.ok) {
    conn.textContent = `backend up, DB error: ${s.health.error || "unknown"}`; conn.className = "conn bad";
  } else {
    conn.textContent = "backend unreachable (check the URL / that it's deployed)"; conn.className = "conn bad";
  }

  if (s.speed && document.activeElement !== $("speed")) $("speed").value = s.speed;
  $("start").disabled = s.running;
  $("stop").disabled = !s.running;
  $("statusbar").dataset.state = s.running ? "run" : (s.log?.[0] && /BLOCKED|Stopped/i.test(s.log[0]) ? "stop" : "idle");
  $("statusText").textContent = s.running ? "sending…" : "stopped";
  $("logs").textContent = (s.log || []).join("\n");
}

$("save").onclick = async () => { await msg({ type: "SAVE_BACKEND", backend: $("backend").value.trim() }); refresh(); };
$("speed").onchange = async () => { await msg({ type: "SAVE_SPEED", speed: $("speed").value }); };
$("start").onclick = async () => {
  const backend = $("backend").value.trim();
  if (!backend) return alert("Paste your Railway backend URL first.");
  if (!confirm("Start sending real DMs from this Instagram account?\n\nUse a burner. It stops automatically on a block.")) return;
  await msg({ type: "START", backend, speed: $("speed").value });
  refresh();
};
$("stop").onclick = async () => { await msg({ type: "STOP" }); refresh(); };
$("sendNow").onclick = async () => { await msg({ type: "SEND_NOW" }); setTimeout(refresh, 800); };

setInterval(refresh, 2000);
refresh();
