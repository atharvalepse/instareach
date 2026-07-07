# Always-on host image for the outreach backend (Railway / Render / Fly / VPS).
# Runs the Flask API + the in-process auto-scheduler as a SINGLE gunicorn worker
# (one worker so exactly one scheduler thread ticks).
#
# State lives in Postgres (Supabase) — set DATABASE_URL at deploy time. No
# volume needed; the external DB IS the persistence.
FROM python:3.12-slim

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY . .

# DATABASE_URL is REQUIRED (Supabase connection string) — provide it in the host's env.
ENV AUTO_TICK=1
ENV AUTO_TICK_SECONDS=300
ENV PORT=8000
EXPOSE 8000

WORKDIR /app/backend
# --workers 1 is REQUIRED: the auto-scheduler runs in-process; multiple workers
# would tick concurrently and double-enqueue.
CMD gunicorn app:app --workers 1 --threads 8 --bind 0.0.0.0:${PORT} --timeout 120
