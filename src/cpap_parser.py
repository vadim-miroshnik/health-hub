"""
CPAP data parser for ResMed AirSense 10 SD card format.

Reads EDF files from data/raw/cpap/{date}/ and extracts:
- Session summary → cpap_sessions table
- Apnea events → cpap_events table

Detailed signal channels (pressure/flow/leak per second) stay in EDF files.
MCP tool get_cpap_detailed() reads them on-demand via pyedflib.

Usage:
    parser = CpapParser(db, raw_store)
    result = parser.parse_date("2026-04-15")
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class CpapParseResult:
    date: str
    status: str          # 'ok' | 'no_data' | 'error'
    errors: list = field(default_factory=list)


class CpapParser:
    def __init__(self, db, store):
        self.db = db
        self.store = store

    def parse_date(self, date: str) -> CpapParseResult:
        """
        Find and parse all CPAP EDF files for a date.
        Returns CpapParseResult with status.
        """
        # Find EDF files in raw store for this date
        raw_files = self.store.list_raw("cpap", date, date)
        edf_files = [
            Path(f["filepath"])
            for f in raw_files
            if f["filepath"].endswith(".edf")
        ]

        if not edf_files:
            return CpapParseResult(date=date, status="no_data")

        errors = []
        session_saved = False

        for path in edf_files:
            if not path.exists():
                # Try as relative to store base parent
                abs_path = self.store._base.parent / path
                if abs_path.exists():
                    path = abs_path

            try:
                if "_Annotations" in path.name:
                    events = self._parse_annotations_edf(path)
                    if events:
                        self.db.save_cpap_events(date, events)
                elif "_Summary" in path.name:
                    summary = self._parse_summary_edf(path)
                    if summary:
                        self.db.save_cpap_session(date, **summary)
                        session_saved = True
                # else: detailed signal file, skip (stays in EDF)
            except Exception as exc:
                errors.append(f"{path.name}: {exc}")
                logger.error("CPAP parse error %s: %s", path.name, exc)

        status = "ok" if not errors else "error"
        if not session_saved and not errors:
            status = "no_data"

        return CpapParseResult(date=date, status=status, errors=errors)

    def _parse_summary_edf(self, path: Path) -> dict | None:
        """
        Parse a ResMed Summary EDF file.
        Returns dict matching cpap_sessions columns, or None if unreadable.

        ResMed Summary EDF signals (typical):
          - MaskPressure.50Hz → pressure stats
          - LeakTotal.50Hz → leak stats
          - RespRate → respiratory rate
          - TidalVolume → tidal volume
          - MinuteVent → minute ventilation

        Returns a best-effort dict from EDF header + available signals.
        """
        try:
            import pyedflib
        except ImportError:
            raise RuntimeError("pyedflib not installed: pip install pyedflib")

        try:
            f = pyedflib.EdfReader(str(path))
        except Exception as exc:
            raise RuntimeError(f"Cannot open EDF: {exc}") from exc

        try:
            n_signals = f.signals_in_file
            labels = f.getSignalLabels()
            start_dt = f.getStartdatetime()

            # Build signal name → index map
            sig_map = {labels[i].strip(): i for i in range(n_signals)}

            def _stats(name: str):
                """Return (median, 95th percentile) of a signal."""
                for label, idx in sig_map.items():
                    if name.lower() in label.lower():
                        data = f.readSignal(idx)
                        if len(data) == 0:
                            return None, None
                        # Filter out zero/invalid values
                        filtered = [v for v in data if v > 0]
                        if not filtered:
                            return None, None
                        try:
                            import numpy as np
                            arr = np.array(filtered)
                            return float(np.median(arr)), float(np.percentile(arr, 95))
                        except ImportError:
                            sorted_vals = sorted(filtered)
                            n = len(sorted_vals)
                            mid = n // 2
                            median = (sorted_vals[mid] + sorted_vals[~mid]) / 2
                            p95_idx = int(0.95 * n)
                            return float(median), float(sorted_vals[min(p95_idx, n - 1)])
                return None, None

            def _median(name: str):
                """Return median of a signal."""
                med, _ = _stats(name)
                return med

            duration_s = f.getFileDuration()
            duration_min = int(duration_s / 60) if duration_s else None

            pressure_med, pressure_95 = _stats("MaskPressure")
            leak_med, leak_95 = _stats("LeakTotal")
            resp_rate_med = _median("RespRate")
            tidal_med = _median("TidalVolume")
            minute_vent_med = _median("MinuteVent")

            return {
                "start_time": start_dt.isoformat() if start_dt else None,
                "end_time": None,  # calculated from duration if needed
                "duration_minutes": duration_min,
                "ahi": None,           # AHI requires event count / duration
                "ai": None,
                "hi": None,
                "obstructive_events": None,
                "central_events": None,
                "hypopnea_events": None,
                "clear_airway_events": None,
                "rera_events": None,
                "leak_median": leak_med,
                "leak_95pct": leak_95,
                "pressure_min": None,
                "pressure_max": None,
                "pressure_median": pressure_med,
                "pressure_95pct": pressure_95,
                "tidal_volume_median": tidal_med,
                "minute_vent_median": minute_vent_med,
                "resp_rate_median": resp_rate_med,
                "mask_on_off_count": None,
            }
        finally:
            f.close()

    def _parse_annotations_edf(self, path: Path) -> list[dict]:
        """
        Parse ResMed Annotations EDF for apnea events.
        Returns list of dicts matching cpap_events columns.

        EDF+ annotation format: TAL (Time-stamped Annotations List)
        Each annotation: onset (seconds from start), duration, text (event type)

        ResMed event type strings:
          'ObstructiveApnea', 'CentralApnea', 'Hypopnea',
          'ClearAirway', 'RERA', 'FlowLimitation'
        """
        try:
            import pyedflib
        except ImportError:
            raise RuntimeError("pyedflib not installed")

        _TYPE_MAP = {
            "ObstructiveApnea":  "obstructive",
            "CentralApnea":      "central",
            "Hypopnea":          "hypopnea",
            "ClearAirway":       "clear_airway",
            "RERA":              "rera",
            "FlowLimitation":    "flow_limit",
        }

        try:
            f = pyedflib.EdfReader(str(path))
        except Exception as exc:
            raise RuntimeError(f"Cannot open EDF: {exc}") from exc

        try:
            from datetime import timedelta

            start_dt = f.getStartdatetime()
            annotations = f.readAnnotations()
            events = []
            for onset, duration, label in zip(*annotations):
                event_type = _TYPE_MAP.get(label.strip())
                if event_type is None:
                    continue
                ts = start_dt + timedelta(seconds=float(onset))
                events.append({
                    "timestamp": ts.isoformat(),
                    "event_type": event_type,
                    "duration_seconds": float(duration) if duration else None,
                })
            return events
        finally:
            f.close()
