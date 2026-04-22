"""
CPAP data parser for ResMed AirSense 10 SD card format.

Reads EDF files from data/raw/cpap/{date}/ and extracts:
- Session summary → cpap_sessions table (with AHI/AI/HI aggregated from events)
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

# Annotation label → canonical apnea event type.
# Accepts both CamelCase (ResMed) and space-separated (OSCAR/pyedflib) forms.
_EVENT_LABEL_MAP: dict[str, str] = {
    "obstructiveapnea":   "obstructive",
    "obstructive apnea":  "obstructive",
    "centralapnea":       "central",
    "central apnea":      "central",
    "hypopnea":           "hypopnea",
    "clearairway":        "clear_airway",
    "clear airway":       "clear_airway",
    "rera":               "rera",
    "flowlimitation":     "flow_limit",
    "flow limitation":    "flow_limit",
}

_MASK_ON_LABELS = {"mask on", "maskon", "mask-on"}
_MASK_OFF_LABELS = {"mask off", "maskoff", "mask-off"}


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

        Two-pass: first collect summary + events + mask on/off count, then merge
        event-derived fields (AHI/AI/HI, event counts, mask_on_off_count) into
        the session row before INSERT.

        Returns CpapParseResult with status.
        """
        raw_files = self.store.list_raw("cpap", date, date)
        edf_files = [
            Path(f["filepath"])
            for f in raw_files
            if f["filepath"].endswith(".edf")
        ]

        if not edf_files:
            return CpapParseResult(date=date, status="no_data")

        errors: list[str] = []
        summary: dict | None = None
        events: list[dict] = []
        mask_on_off_count = 0

        for path in edf_files:
            if not path.exists():
                abs_path = self.store._base.parent / path
                if abs_path.exists():
                    path = abs_path

            try:
                if "_Annotations" in path.name:
                    parsed_events, mask_count = self._parse_annotations_edf(path)
                    events.extend(parsed_events)
                    mask_on_off_count += mask_count
                elif "_Summary" in path.name:
                    summary = self._parse_summary_edf(path)
                # else: detailed signal file, skip (stays in EDF)
            except Exception as exc:
                errors.append(f"{path.name}: {exc}")
                logger.error("CPAP parse error %s: %s", path.name, exc)

        session_saved = False
        if summary:
            _enrich_with_events(summary, events, mask_on_off_count)
            self.db.save_cpap_session(date, **summary)
            session_saved = True

        if events:
            self.db.save_cpap_events(date, events)

        status = "ok" if not errors else "error"
        if not session_saved and not errors:
            status = "no_data"

        return CpapParseResult(date=date, status=status, errors=errors)

    def _parse_summary_edf(self, path: Path) -> dict | None:
        """
        Parse a ResMed Summary EDF file.
        Returns dict matching cpap_sessions columns, or None if unreadable.

        ResMed Summary EDF signals (typical):
          - MaskPressure.50Hz → pressure min/median/p95/max
          - LeakTotal.50Hz → leak median/p95
          - RespRate → respiratory rate
          - TidalVolume → tidal volume
          - MinuteVent → minute ventilation

        Event-count fields (ahi/ai/hi, *_events, mask_on_off_count) stay None
        here — they're filled in by _enrich_with_events() after annotations are
        parsed.
        """
        try:
            import pyedflib
        except ImportError as exc:
            raise RuntimeError("pyedflib not installed: pip install pyedflib") from exc

        try:
            f = pyedflib.EdfReader(str(path))
        except Exception as exc:
            raise RuntimeError(f"Cannot open EDF: {exc}") from exc

        try:
            n_signals = f.signals_in_file
            labels = f.getSignalLabels()
            start_dt = f.getStartdatetime()

            sig_map = {labels[i].strip(): i for i in range(n_signals)}

            def _signal_stats(name: str) -> dict | None:
                """Return {min, max, median, p95} for a named signal, or None."""
                for label, idx in sig_map.items():
                    if name.lower() not in label.lower():
                        continue
                    data = f.readSignal(idx)
                    if len(data) == 0:
                        return None
                    filtered = [v for v in data if v > 0]
                    if not filtered:
                        return None
                    try:
                        import numpy as np
                        arr = np.array(filtered)
                        return {
                            "min":    float(arr.min()),
                            "max":    float(arr.max()),
                            "median": float(np.median(arr)),
                            "p95":    float(np.percentile(arr, 95)),
                        }
                    except ImportError:
                        sorted_vals = sorted(filtered)
                        n = len(sorted_vals)
                        mid = n // 2
                        median = (sorted_vals[mid] + sorted_vals[~mid]) / 2
                        p95_idx = int(0.95 * n)
                        return {
                            "min":    float(sorted_vals[0]),
                            "max":    float(sorted_vals[-1]),
                            "median": float(median),
                            "p95":    float(sorted_vals[min(p95_idx, n - 1)]),
                        }
                return None

            def _median(name: str) -> float | None:
                stats = _signal_stats(name)
                return stats["median"] if stats else None

            duration_s = f.getFileDuration()
            duration_min = int(duration_s / 60) if duration_s else None

            pressure = _signal_stats("MaskPressure") or {}
            leak = _signal_stats("LeakTotal") or {}

            return {
                "start_time": start_dt.isoformat() if start_dt else None,
                "end_time": None,
                "duration_minutes": duration_min,
                "ahi": None,
                "ai": None,
                "hi": None,
                "obstructive_events": None,
                "central_events": None,
                "hypopnea_events": None,
                "clear_airway_events": None,
                "rera_events": None,
                "leak_median": leak.get("median"),
                "leak_95pct": leak.get("p95"),
                "pressure_min": pressure.get("min"),
                "pressure_max": pressure.get("max"),
                "pressure_median": pressure.get("median"),
                "pressure_95pct": pressure.get("p95"),
                "tidal_volume_median": _median("TidalVolume"),
                "minute_vent_median": _median("MinuteVent"),
                "resp_rate_median": _median("RespRate"),
                "mask_on_off_count": None,
            }
        finally:
            f.close()

    def _parse_annotations_edf(self, path: Path) -> tuple[list[dict], int]:
        """
        Parse ResMed Annotations EDF.

        Returns (events, mask_on_off_count):
          - events: list of dicts matching cpap_events columns for apnea events.
          - mask_on_off_count: total count of 'Mask On' + 'Mask Off' markers.

        Event label matching is case-insensitive and tolerates both ResMed
        CamelCase ('ObstructiveApnea') and OSCAR-style space-separated
        ('Obstructive Apnea') forms.
        """
        try:
            import pyedflib
        except ImportError as exc:
            raise RuntimeError("pyedflib not installed") from exc

        try:
            f = pyedflib.EdfReader(str(path))
        except Exception as exc:
            raise RuntimeError(f"Cannot open EDF: {exc}") from exc

        try:
            from datetime import timedelta

            start_dt = f.getStartdatetime()
            annotations = f.readAnnotations()
            events: list[dict] = []
            mask_on_off = 0
            for onset, duration, label in zip(*annotations):
                key = label.strip().lower()
                if key in _MASK_ON_LABELS or key in _MASK_OFF_LABELS:
                    mask_on_off += 1
                    continue
                event_type = _EVENT_LABEL_MAP.get(key)
                if event_type is None:
                    continue
                ts = start_dt + timedelta(seconds=float(onset))
                events.append({
                    "timestamp": ts.isoformat(),
                    "event_type": event_type,
                    "duration_seconds": float(duration) if duration else None,
                })
            return events, mask_on_off
        finally:
            f.close()


def _enrich_with_events(
    summary: dict,
    events: list[dict],
    mask_on_off_count: int,
) -> None:
    """
    Merge per-event aggregates into the session summary in-place.

    Computes:
      - obstructive_events / central_events / hypopnea_events /
        clear_airway_events / rera_events (counts by type).
      - ahi = (obstructive + central + hypopnea + clear_airway) / hours.
      - ai  = (obstructive + central + clear_airway) / hours.
      - hi  = hypopnea / hours.
      - mask_on_off_count.
    """
    counts: dict[str, int] = {}
    for ev in events:
        et = ev["event_type"]
        counts[et] = counts.get(et, 0) + 1

    summary["obstructive_events"]  = counts.get("obstructive", 0)
    summary["central_events"]      = counts.get("central", 0)
    summary["hypopnea_events"]     = counts.get("hypopnea", 0)
    summary["clear_airway_events"] = counts.get("clear_airway", 0)
    summary["rera_events"]         = counts.get("rera", 0)
    summary["mask_on_off_count"]   = mask_on_off_count

    duration_min = summary.get("duration_minutes")
    if duration_min and duration_min > 0:
        hours = duration_min / 60.0
        apnea = (
            summary["obstructive_events"]
            + summary["central_events"]
            + summary["clear_airway_events"]
        )
        hypopnea = summary["hypopnea_events"]
        summary["ahi"] = round((apnea + hypopnea) / hours, 2)
        summary["ai"]  = round(apnea / hours, 2)
        summary["hi"]  = round(hypopnea / hours, 2)
