FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# atlas.db (itinerary_items, audit_log, and the LangGraph checkpointer's own
# tables for in-flight interrupts) is created fresh inside the container by
# db.py's init_db() at import time — .dockerignore excludes any *.db* file
# from the build context, so a locally-generated DB is never baked in.
#
# FLAG: no volume is mounted for it here or in docker-compose.yml, so this
# is SQLite data living in the container's writable layer only. It survives
# a plain restart (`restart: unless-stopped` keeps the same container), but
# is permanently lost on any container replacement — `docker compose up
# --build`, a redeploy, `docker rm`, or scaling to >1 replica (each gets its
# own independent file, so approvals/audit history diverge across them).
# For a feature whose entire point is a persistent, reviewable audit trail
# and resumable pending approvals, this is a real gap, not a hardening nice-
# to-have: an itinerary proposal a human hasn't approved yet is silently
# discarded if the container is torn down first. Needs a named volume
# mounted at wherever atlas.db lives (and DB_PATH in db.py made configurable
# via an env var to point at it) before this goes anywhere that redeploys.

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
