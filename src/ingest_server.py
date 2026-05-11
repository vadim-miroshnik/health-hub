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
import uuid
from datetime import date as date_type, datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

if not os.environ.get("NO_DOTENV"):
    load_dotenv(override=True)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request schema — deliberately permissive; HC can emit 50+ record types and we
# don't want to fail the whole batch over one unknown field.
# ---------------------------------------------------------------------------

class HCRecord(BaseModel):
    # extra="allow" preserves rich nested fields like SleepSession.stages,
    # ExerciseSession.segments, Nutrition macros — otherwise Pydantic v2
    # silently drops them and we lose the most useful payload.
    model_config = ConfigDict(extra="allow")

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
    model_config = ConfigDict(extra="allow")

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
async def ingest_health_connect(request: Request) -> HCResponse:
    """
    Ingest a batch of HC records.

    1. Persist the raw request body verbatim BEFORE validation, so a future
       schema-drift or pydantic config bug can never silently drop data —
       we can re-parse from `incoming/` even if the validated archive is
       wrong.
    2. Validate with Pydantic (extra="allow" preserves rich nested fields).
    3. Save the normalized batch JSON alongside the raw one.
    4. Insert each record with `ON CONFLICT(uid) DO NOTHING`. Duplicates are
       counted via `rowcount` on each INSERT.
    5. Return {ok, accepted, duplicates}.
    """
    raw_body = await request.body()

    # Step 1 — raw body archive. Naming: `incoming/{today}/{iso_ts}_{nonce}.raw.json`.
    # We don't yet know batch_id (haven't validated), so use server timestamp +
    # short uuid. `today` here is the server-local date when the request landed.
    incoming_dir = _raw_dir() / "health_connect" / "incoming" / date_type.today().isoformat()
    incoming_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    nonce = uuid.uuid4().hex[:8]
    raw_incoming_path = incoming_dir / f"{ts}_{nonce}.raw.json"
    raw_incoming_path.write_bytes(raw_body)

    # Step 2 — validate. On failure, raw is already on disk for forensics.
    try:
        batch = HCBatch.model_validate_json(raw_body)
    except ValidationError as e:
        logger.warning("hc-ingest validation failed for %s: %s", raw_incoming_path.name, e)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=e.errors(),
        ) from e

    # Step 3 — normalized archive (keyed by batch_id, easier to find later).
    batch_date = _local_date(batch.synced_at) if batch.records else date_type.today().isoformat()
    raw_target = _raw_dir() / "health_connect" / batch_date
    raw_target.mkdir(parents=True, exist_ok=True)
    safe_batch_id = _sanitize_filename(batch.batch_id)
    raw_path = raw_target / f"batch_{safe_batch_id}.json"
    raw_path.write_text(
        json.dumps(batch.model_dump(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Upsert records. We pre-check existence so the response can still
    # distinguish first-time inserts from refreshes; the INSERT itself uses
    # DO UPDATE so a re-sync with richer payload (e.g. SleepSession.stages
    # that was missing before) overwrites the old row instead of being
    # silently dropped as a "duplicate".
    from src.db import Database
    db = Database(_db_path())
    try:
        accepted = 0
        duplicates = 0
        now = datetime.now(timezone.utc).isoformat()
        for rec in batch.records:
            local_date = _local_date(rec.start_time)
            data_json = json.dumps(rec.model_dump(), ensure_ascii=False)
            existed = db.conn.execute(
                "SELECT 1 FROM hc_records WHERE uid=?", (rec.uid,),
            ).fetchone() is not None
            db.conn.execute(
                """
                INSERT INTO hc_records(
                    uid, type, start_time, end_time, date,
                    value, unit, source_app, source_device, data_json, ingested_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(uid) DO UPDATE SET
                    type          = excluded.type,
                    start_time    = excluded.start_time,
                    end_time      = excluded.end_time,
                    date          = excluded.date,
                    value         = excluded.value,
                    unit          = excluded.unit,
                    source_app    = excluded.source_app,
                    source_device = excluded.source_device,
                    data_json     = excluded.data_json,
                    ingested_at   = excluded.ingested_at
                """,
                (
                    rec.uid, rec.type, rec.start_time, rec.end_time, local_date,
                    rec.value, rec.unit, rec.source_app, rec.source_device,
                    data_json, now,
                ),
            )
            if existed:
                duplicates += 1
            else:
                accepted += 1
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
