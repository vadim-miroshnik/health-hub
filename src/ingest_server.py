"""
Health Connect HTTP ingest server (Phase 10).

Accepts push batches from the Android Health Connect Bridge over HTTPS,
authenticates via a shared secret in the `X-Auth-Token` header, archives the
raw batch to `data/raw/health_connect/{date}/batch_{batch_id}.json`, and
writes each record into `hc_records` with `ON CONFLICT(uid) DO NOTHING` for
idempotent retries.

The FastAPI handlers are sync (`def`, not `async def`) on purpose — FastAPI
runs them in a threadpool, so the rest of the Health Hub codebase stays
synchronous per CLAUDE.md's "no async" rule. This module is the only seam
where a separate async runtime (uvicorn) runs in its own process.

Run:
    hhub serve-ingest --port 8765
or directly:
    uvicorn src.ingest_server:app --host 0.0.0.0 --port 8765

Env vars:
    HC_INGEST_AUTH_TOKEN   — required shared secret; 401 if missing/mismatch
    DB_PATH                — path to health.db (default data/health.db)
    RAW_DIR                — raw data lake root (default data/raw)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date as date_type, datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

if not os.environ.get("NO_DOTENV"):
    load_dotenv(override=True)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request schema — deliberately permissive; HC can emit 50+ record types and we
# don't want to fail the whole batch over one unknown field.
# ---------------------------------------------------------------------------

class HCRecord(BaseModel):
    uid: str = Field(..., min_length=1)
    type: str = Field(..., min_length=1)
    start_time: str
    end_time: str
    value: float | None = None
    unit: str | None = None
    source_app: str | None = None
    source_device: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class HCBatch(BaseModel):
    batch_id: str = Field(..., min_length=1)
    synced_at: str
    records: list[HCRecord] = Field(default_factory=list)


class HCResponse(BaseModel):
    ok: bool
    accepted: int
    duplicates: int


# ---------------------------------------------------------------------------
# Timezone helper — CLAUDE.md "Timezone policy" says hc_records.date is the
# local wall-clock date derived from start_time. We treat the server's local
# tz as the user's home tz (single-user deployment on Beelink).
# ---------------------------------------------------------------------------

def _local_date(iso8601: str) -> str:
    """
    Convert an ISO8601 timestamp (with or without tz) into a local YYYY-MM-DD.

    Trailing 'Z' is normalized to '+00:00' so datetime.fromisoformat accepts it.
    Naive timestamps are assumed to already be local wall-clock.
    """
    s = iso8601.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return date_type.today().isoformat()
    if dt.tzinfo is not None:
        dt = dt.astimezone()  # convert to system-local tz
    return dt.date().isoformat()


# ---------------------------------------------------------------------------
# App + dependencies
# ---------------------------------------------------------------------------

def _db_path() -> Path:
    return Path(os.environ.get("DB_PATH", "data/health.db"))


def _raw_dir() -> Path:
    return Path(os.environ.get("RAW_DIR", "data/raw"))


def _require_auth(
    x_auth_token: str | None = Header(default=None, alias="X-Auth-Token"),
) -> None:
    expected = os.environ.get("HC_INGEST_AUTH_TOKEN", "").strip()
    if not expected:
        # If the token env var isn't set, refuse all traffic — server should
        # not start in this mode in production (see cli/serve_ingest.py).
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="HC_INGEST_AUTH_TOKEN not configured on server",
        )
    if x_auth_token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing X-Auth-Token",
        )


app = FastAPI(
    title="Health Hub Ingest",
    description="Push endpoint for Google Health Connect records from Android.",
    version="0.1.0",
)


@app.get("/health")
def healthcheck() -> dict:
    """Unauthenticated liveness probe for systemd / load balancers."""
    return {"ok": True}


@app.post(
    "/ingest/health-connect",
    response_model=HCResponse,
    dependencies=[Depends(_require_auth)],
)
def ingest_health_connect(batch: HCBatch, request: Request) -> HCResponse:
    """
    Ingest a batch of HC records.

    1. Save the raw batch JSON to the raw data lake.
    2. Insert each record with `ON CONFLICT(uid) DO NOTHING`. Duplicates are
       counted via `rowcount` on each INSERT.
    3. Return {ok, accepted, duplicates}.
    """
    # Write the raw batch to disk first — even if DB insert later fails, the
    # push is durable and re-parseable.
    batch_date = _local_date(batch.synced_at) if batch.records else date_type.today().isoformat()
    raw_target = _raw_dir() / "health_connect" / batch_date
    raw_target.mkdir(parents=True, exist_ok=True)
    safe_batch_id = _sanitize_filename(batch.batch_id)
    raw_path = raw_target / f"batch_{safe_batch_id}.json"
    raw_path.write_text(
        json.dumps(batch.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Insert records.
    from src.db import Database
    db = Database(_db_path())
    try:
        accepted = 0
        duplicates = 0
        now = datetime.now(timezone.utc).isoformat()
        for rec in batch.records:
            local_date = _local_date(rec.start_time)
            data_json = json.dumps(rec.model_dump(), ensure_ascii=False)
            cur = db.conn.execute(
                """
                INSERT INTO hc_records(
                    uid, type, start_time, end_time, date,
                    value, unit, source_app, source_device, data_json, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(uid) DO NOTHING
                """,
                (
                    rec.uid, rec.type, rec.start_time, rec.end_time, local_date,
                    rec.value, rec.unit, rec.source_app, rec.source_device,
                    data_json, now,
                ),
            )
            if cur.rowcount:
                accepted += 1
            else:
                duplicates += 1
        db.conn.commit()
    finally:
        db.close()

    logger.info(
        "hc-ingest batch=%s accepted=%d duplicates=%d",
        batch.batch_id, accepted, duplicates,
    )
    return HCResponse(ok=True, accepted=accepted, duplicates=duplicates)


def _sanitize_filename(name: str) -> str:
    """Strip path separators and control chars from user-supplied batch_id."""
    return "".join(c for c in name if c.isalnum() or c in "-_.").strip(".") or "batch"
