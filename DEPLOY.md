# Deploying (always-on host)

The backend is a stateful, always-running service (a SQLite source-of-truth + an
in-process auto-scheduler). It needs a host that stays up and keeps a disk —
**not** a serverless platform like Vercel (ephemeral filesystem + no background
process = it won't work).

Good fits: **Railway, Render, Fly.io, or any $5 VPS / EC2.**

## Two non-negotiables

1. **A persistent volume for the SQLite DB.** Containers on these platforms have
   ephemeral filesystems by default — without a mounted disk the DB resets on
   every redeploy/restart. Mount a volume and point `OUTREACH_DB` at it
   (the Dockerfile defaults to `/data/outreach.db`).
2. **A single web worker.** The auto-scheduler runs *inside* the web process
   (`--workers 1`). Multiple workers would tick concurrently and double-send.
   For more HTTP throughput use threads (`--threads 8`), not workers.

## Environment variables

| Var | Default | Purpose |
|---|---|---|
| `OUTREACH_DB` | `/data/outreach.db` | SQLite path — put it on the volume |
| `AUTO_TICK` | `1` | `1` = auto-scheduler on; `0` = manual ticking only |
| `AUTO_TICK_SECONDS` | `300` | how often follow-ups are enqueued |
| `PORT` | `8000` | web port (platforms usually inject this) |

## Railway
1. New Project → Deploy from the GitHub repo (uses the `Dockerfile`).
2. Add a **Volume**, mount path `/data`.
3. Deploy. Your API is at the generated URL.

## Render
1. New → **Web Service** from the repo (Docker).
2. Add a **Disk**, mount path `/data`.
3. Set env vars above. Deploy.

## Fly.io
```bash
fly launch --no-deploy
fly volumes create data --size 1
# in fly.toml add:  [mounts]  source="data"  destination="/data"
fly deploy
```

## Plain VPS / EC2
```bash
docker build -t outreach .
docker run -d -p 8000:8000 -v /opt/outreach-data:/data --restart unless-stopped outreach
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
