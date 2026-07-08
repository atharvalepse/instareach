# Deploying (always-on host + Supabase)

The backend is a stateful, always-running service (a Postgres source-of-truth +
an in-process auto-scheduler). State lives in **Supabase** (hosted Postgres), so
the host itself is stateless — but it still must **stay up** to run the
scheduler, so **not** a serverless platform like Vercel.

Good fits: **Railway, Render, Fly.io, or any $5 VPS / EC2.**

## Database: zero-config, or Supabase for persistence

- **No `DATABASE_URL`** → the app uses a built-in **SQLite** file and **just
  works** with no setup. Caveat: on Railway/containers the disk is ephemeral, so
  data (campaigns, contacts, queue) **resets on every redeploy/restart**. Fine
  for trying it out or short runs.
- **Set `DATABASE_URL`** → the app uses **Postgres/Supabase** and state persists
  across redeploys. Recommended once you're running real campaigns.

Either way, tables are created automatically on first boot — no migrations.

**One non-negotiable regardless:** a **single web worker**. The auto-scheduler
runs *inside* the web process (`--workers 1`); multiple workers would tick
concurrently and double-send. Use threads (`--threads 8`) for HTTP throughput.

## Environment variables

| Var | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | *(unset → SQLite)* | Supabase/Postgres string for persistence |
| `AUTO_TICK` | `1` | `1` = auto-scheduler on; `0` = manual ticking only |
| `AUTO_TICK_SECONDS` | `300` | how often follow-ups are enqueued |
| `HOURLY_CAP` | `10` | **ban-safety**: max DMs enqueued per rolling hour |
| `DAILY_CAP` | `80` | **ban-safety**: max DMs enqueued per rolling day |
| `PORT` | `8000` | web port (platforms usually inject this) |

## Adding Supabase later (optional)
1. Create a Supabase project.
2. **Project Settings → Database → Connection string → URI** → use the
   **Connection pooler** (port `6543`, "Transaction" mode):
   `postgresql://postgres.<ref>:<pw>@<host>:6543/postgres`.
3. Set it as `DATABASE_URL`. Done — the app switches to Postgres on next boot.

### Ban safety (read this)
Two layers keep volume safe, and **both matter** — spacing alone doesn't prevent
bans, volume caps do:
- **Spacing** — the extension waits **60–180s between each DM** (randomized).
- **Caps** — the backend refuses to enqueue past `HOURLY_CAP` / `DAILY_CAP`,
  counting delivered + in-flight, so a big queue can't burst out. Check headroom
  any time at `GET /api/agent/quota`.

Follow-ups are **prioritized within the cap** — time-sensitive nudges go out
before new intros, so raising the ceiling never starves your sequences.

Defaults (10/hr, **80/day**) are a HIGH ceiling — only safe for a **well-aged,
warmed** account, and even then it's aggressive for cold DMs. For a **new/burner**
account start much lower (e.g. `HOURLY_CAP=3`, `DAILY_CAP=10`) and raise slowly
over a couple of weeks. Don't run 24/7 — leave the browser open only during
normal active hours.

## Railway
1. New Project → Deploy from the GitHub repo (uses the `Dockerfile`).
2. **Variables** → add `DATABASE_URL` = your Supabase pooler URI (+ optionally
   `HOURLY_CAP`/`DAILY_CAP`). No volume needed.
3. Deploy. Your API is at the generated URL.

## Render
1. New → **Web Service** from the repo (Docker).
2. Add env var `DATABASE_URL` (Supabase). No disk needed.
3. Deploy.

## Fly.io
```bash
fly launch --no-deploy
fly secrets set DATABASE_URL="postgresql://...supabase-pooler..."
fly deploy
```

## Plain VPS / EC2
```bash
docker build -t outreach .
docker run -d -p 8000:8000 -e DATABASE_URL="postgresql://...supabase..." \
  --restart unless-stopped outreach
```

## After deploy — point the extension at it
In `extension/browser_channel.js` set `AGENT.BACKEND` to your deployed URL and add
that origin to the extension's `host_permissions`. Then, in a browser that's
logged into the (burner) IG account, run `startAll()`:

- the cloud backend **schedules & queues** DMs 24/7 (auto-scheduler),
- the extension **delivers** them from your session and **auto-detects replies**.

That's the fully-automated loop: scrape → import → **Start**, and everything else
runs itself while the browser stays open.

> ⚠️ This sends real DMs and violates Instagram's ToS. Burner account, high
> throttle, stop on the first block.
