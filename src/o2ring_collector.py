"""
O2Ring S data collector/parser.

Supports two input formats:
1. CSV export from ViHealth app (Android/iOS)
2. Binary file from O2 Insight Pro desktop app

Usage:
    collector = O2RingCollector(db, store)
    result = collector.import_csv("2026-04-15", Path("session.csv"))
    result = collector.import_binary("2026-04-15", Path("session.bin"))
"""

import struct
from dataclasses import dataclass, field
from datetime import datetime, date as date_type, time as time_type, timedelta
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

_DESATURATION_DROP = 3     # % drop from baseline to count as desaturation
_DESATURATION_MIN_SEC = 10  # minimum seconds below threshold
_REF_DATE = date_type(2000, 1, 1)  # fixed reference for time-only arithmetic


@dataclass
class O2RingResult:
    date: str
    status: str          # 'ok' | 'error'
    n_records: int = 0
    errors: list = field(default_factory=list)


class O2RingCollector:
    def __init__(self, db, store):
        self.db = db
        self.store = store

    def import_csv(self, date: str, path: Path) -> O2RingResult:
        """
        Parse ViHealth CSV export and save to DB.

        ViHealth CSV format:
            Line 1: file info header (skip)
            Line 2: column names OR data starts directly
            Columns: Time, SpO2(%), Pulse Rate(bpm), Motion

        Example rows:
            00:00:00,97,72,0
            00:00:04,97,73,0
        """
        try:
            records = self._parse_vihealth_csv(path)
        except Exception as exc:
            return O2RingResult(date=date, status="error", errors=[str(exc)])

        if not records:
            return O2RingResult(date=date, status="error", errors=["No records parsed"])

        return self._save_records(date, records, source_path=path, kind="o2ring_csv")

    def import_binary(self, date: str, path: Path) -> O2RingResult:
        """
        Parse O2 Insight Pro binary file and save to DB.

        Binary format (OSCAR-documented):
          - 40-byte header: magic bytes + metadata
          - 5-byte records: SpO2 (uint8), HR (uint8), motion (uint8), 2 padding bytes
          - Sampling: every 4 seconds
        """
        try:
            records = self._parse_binary(path)
        except Exception as exc:
            return O2RingResult(date=date, status="error", errors=[str(exc)])

        if not records:
            return O2RingResult(date=date, status="error", errors=["No records parsed"])

        return self._save_records(date, records, source_path=path, kind="o2ring_bin")

    def _parse_vihealth_csv(self, path: Path) -> list[dict]:
        """
        Returns list of dicts: {time_str, spo2, heart_rate, motion}
        """
        records = []
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                parts = line.split(",")
                if len(parts) < 3:
                    continue
                # Try to parse first column as HH:MM:SS time
                try:
                    _parse_time(parts[0].strip())
                except ValueError:
                    continue  # skip header/non-data lines
                try:
                    spo2 = int(float(parts[1].strip()))
                    hr = int(float(parts[2].strip()))
                    motion = int(float(parts[3].strip())) if len(parts) > 3 else 0
                except (ValueError, IndexError):
                    continue
                records.append({
                    "time_str": parts[0].strip(),
                    "spo2": spo2,
                    "heart_rate": hr,
                    "motion": motion,
                })
        return records

    def _parse_binary(self, path: Path) -> list[dict]:
        """
        Parse O2 Insight Pro binary file.
        """
        HEADER_SIZE = 40
        RECORD_SIZE = 5

        data = path.read_bytes()
        if len(data) < HEADER_SIZE:
            raise ValueError(f"File too small: {len(data)} bytes")

        # Validate structure: body must be an exact multiple of RECORD_SIZE
        remaining = len(data) - HEADER_SIZE
        if remaining % RECORD_SIZE != 0:
            raise ValueError(
                f"Invalid binary file: body is {remaining} bytes, "
                f"not a multiple of record size {RECORD_SIZE}"
            )

        records = []
        offset = HEADER_SIZE
        t = time_type(0, 0, 0)
        interval = timedelta(seconds=4)

        while offset + RECORD_SIZE <= len(data):
            chunk = data[offset:offset + RECORD_SIZE]
            spo2, hr, motion = struct.unpack_from("<BBBxx", chunk)
            if spo2 > 0 and hr > 0:  # 0 = invalid/off-finger
                records.append({
                    "time_str": t.strftime("%H:%M:%S"),
                    "spo2": spo2,
                    "heart_rate": hr,
                    "motion": motion,
                })
            # Advance time (use fixed reference date — only .time() is needed)
            dt = datetime.combine(_REF_DATE, t) + interval
            t = dt.time()
            offset += RECORD_SIZE

        return records

    def _save_records(
        self, date: str, records: list[dict], source_path: Path, kind: str
    ) -> O2RingResult:
        """Compute session summary and save session + data to DB."""
        # Save raw file to store
        if source_path.suffix.lower() in (".bin", ".o2"):
            content = source_path.read_bytes()
        else:
            content = source_path.read_text(encoding="utf-8", errors="replace")
        self.store.save_raw("o2ring", date, kind, content)

        # Build timestamp list for DB, tracking midnight crossover
        date_obj = date_type.fromisoformat(date)
        o2ring_data = []
        prev_t = None
        day_offset = 0
        for r in records:
            t = _parse_time(r["time_str"])
            if prev_t is not None and t < prev_t:
                day_offset += 1  # time wrapped past midnight
            prev_t = t
            ts = datetime.combine(date_obj + timedelta(days=day_offset), t)
            o2ring_data.append({
                "timestamp": ts.isoformat(),
                "spo2": r["spo2"],
                "heart_rate": r["heart_rate"],
                "motion": r["motion"],
            })

        # Compute session summary
        spo2_vals = [r["spo2"] for r in records if r["spo2"] > 0]
        hr_vals   = [r["heart_rate"] for r in records if r["heart_rate"] > 0]

        avg_spo2 = round(sum(spo2_vals) / len(spo2_vals), 1) if spo2_vals else None
        min_spo2 = min(spo2_vals) if spo2_vals else None
        avg_hr   = round(sum(hr_vals) / len(hr_vals), 1) if hr_vals else None
        min_hr   = min(hr_vals) if hr_vals else None
        max_hr   = max(hr_vals) if hr_vals else None
        drops    = _count_desaturations(spo2_vals)

        duration_min = len(records) * 4 // 60

        start_time = o2ring_data[0]["timestamp"]
        end_time = (
            datetime.fromisoformat(o2ring_data[-1]["timestamp"]) + timedelta(seconds=4)
        ).isoformat()

        session_id = self.db.save_o2ring_session(
            date=date,
            start_time=start_time,
            end_time=end_time,
            duration_minutes=duration_min,
            avg_spo2=avg_spo2,
            min_spo2=min_spo2,
            spo2_drops_count=drops,
            avg_hr=avg_hr,
            min_hr=min_hr,
            max_hr=max_hr,
            o2_score=None,
        )
        self.db.save_o2ring_data(session_id, o2ring_data)

        return O2RingResult(date=date, status="ok", n_records=len(records))


def _parse_time(s: str) -> time_type:
    """Parse HH:MM:SS string to time object."""
    parts = s.split(":")
    if len(parts) != 3:
        raise ValueError(f"Not a time: {s!r}")
    return time_type(int(parts[0]), int(parts[1]), int(parts[2]))


def _count_desaturations(spo2_vals: list[int]) -> int:
    """
    Count SpO2 desaturation events: drop >= 3% from rolling baseline
    lasting >= 10 seconds (>= 3 consecutive 4-second samples).
    """
    if not spo2_vals:
        return 0

    MIN_SAMPLES = max(1, _DESATURATION_MIN_SEC // 4)
    baseline_window = 60  # samples for rolling baseline

    count = 0
    in_event = False
    event_start = 0

    for i, val in enumerate(spo2_vals):
        window = spo2_vals[max(0, i - baseline_window):i] or [val]
        baseline = sum(window) / len(window)
        drop = baseline - val

        if drop >= _DESATURATION_DROP:
            if not in_event:
                in_event = True
                event_start = i
        else:
            if in_event and (i - event_start) >= MIN_SAMPLES:
                count += 1
            in_event = False

    if in_event and (len(spo2_vals) - event_start) >= MIN_SAMPLES:
        count += 1

    return count
