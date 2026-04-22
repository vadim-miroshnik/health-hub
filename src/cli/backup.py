"""
`hhub backup` — online SQLite backup via sqlite3.Connection.backup(), with
rotation of old snapshots.

Uses SQLite's official online backup API, which is WAL-safe and doesn't
block readers. Run from cron at 03:00 (outside the collector's 21:00
window) — see docs/deploy.md.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_BACKUP_FILENAME_RE = re.compile(r"^health-(\d{8})\.db$")
_KEEP_BACKUPS = 30


def cmd_backup(args: Any = None) -> None:  # noqa: ARG001 — argparse contract
    """
    CLI entry: back up DB_PATH to RAW_DIR's sibling data/backups/ and rotate.
    """
    db_path = Path(os.environ.get("DB_PATH", "data/health.db"))
    backup_dir = Path(os.environ.get(
        "BACKUP_DIR",
        str(db_path.parent / "backups"),
    ))
    backup_dir.mkdir(parents=True, exist_ok=True)

    if not db_path.exists():
        logger.error("Backup skipped: database not found at %s", db_path)
        raise SystemExit(1)

    out = backup_dir / f"health-{date.today().strftime('%Y%m%d')}.db"
    run_backup(db_path, out)
    n_removed = rotate_backups(backup_dir, keep=_KEEP_BACKUPS)

    logger.info("Backup %s (removed %d old files)", out, n_removed)
    print(f"backup: {out} ({out.stat().st_size} bytes, pruned {n_removed})")


def run_backup(src: Path, dest: Path) -> None:
    """Atomic online backup using SQLite's C backup API via sqlite3."""
    src_conn = sqlite3.connect(str(src))
    try:
        dest_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        src_conn.close()


def rotate_backups(backup_dir: Path, keep: int = _KEEP_BACKUPS) -> int:
    """
    Keep only the `keep` most recent backups matching health-YYYYMMDD.db.

    Returns the number of files removed.
    """
    candidates: list[tuple[str, Path]] = []
    for p in backup_dir.glob("health-*.db"):
        m = _BACKUP_FILENAME_RE.match(p.name)
        if m:
            candidates.append((m.group(1), p))

    candidates.sort(key=lambda t: t[0], reverse=True)  # newest first
    to_delete = candidates[keep:]
    for _, path in to_delete:
        try:
            path.unlink()
        except OSError as exc:
            logger.warning("Could not prune old backup %s: %s", path, exc)
    return len(to_delete)
