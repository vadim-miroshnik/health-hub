"""
P0.1: CPAP parser populates AHI, AI, HI, event counts, pressure min/max,
and mask_on_off_count from Summary+Annotations EDF files.

Uses pyedflib.EdfWriter to synthesize realistic fixture EDFs on the fly so
the test is self-contained (no binary blobs checked in).
"""

from datetime import datetime
from pathlib import Path

import numpy as np
import pytest

from src.cpap_parser import CpapParser
from src.raw_store import RawStore


# ---------------------------------------------------------------------------
# Fixture builders — synthesize ResMed-shaped EDF files
# ---------------------------------------------------------------------------

def _write_summary_edf(
    path: Path,
    duration_seconds: int,
    pressure_values: list[float],
) -> None:
    """
    Write a minimal Summary-style EDF with one MaskPressure signal of the
    given sample values. Uses a 1-sample-per-second rate.
    """
    import pyedflib

    start = datetime(2026, 4, 15, 22, 30, 0)
    n_samples = max(1, len(pressure_values))
    # Pad / truncate pressure buffer to match duration
    pressures = np.array(
        (pressure_values + [pressure_values[-1]] * n_samples)[:n_samples],
        dtype=np.float64,
    )

    writer = pyedflib.EdfWriter(str(path), 1, pyedflib.FILETYPE_EDFPLUS)
    writer.setStartdatetime(start)
    writer.setSignalHeader(0, {
        "label": "MaskPressure.2s",
        "dimension": "cmH2O",
        "sample_frequency": 1,
        "physical_min": 0.0,
        "physical_max": 25.0,
        "digital_min": -32768,
        "digital_max": 32767,
        "transducer": "",
        "prefilter": "",
    })
    writer.setDatarecordDuration(1.0)
    writer.writeSamples([pressures])
    writer.close()

    # EdfWriter records n_samples seconds of data. For duration_seconds longer
    # than n_samples, the reader's getFileDuration returns n_samples seconds.
    # We therefore size pressure_values to match the desired duration upstream.


def _write_annotations_edf(path: Path, annotations: list[tuple[float, float, str]]) -> None:
    """
    Write a minimal Annotations-style EDF. Each annotation is
    (onset_seconds, duration_seconds, label).

    EDF+ binds annotations to data records. To ensure every annotation survives
    the roundtrip, the dummy signal must cover the full onset range of the
    annotation set.
    """
    import pyedflib

    max_onset = max((a[0] for a in annotations), default=0)
    n_samples = int(max_onset) + 60  # headroom past the last annotation

    writer = pyedflib.EdfWriter(str(path), 1, pyedflib.FILETYPE_EDFPLUS)
    writer.setStartdatetime(datetime(2026, 4, 15, 22, 30, 0))
    writer.setSignalHeader(0, {
        "label": "Dummy",
        "dimension": "",
        "sample_frequency": 1,
        "physical_min": 0.0,
        "physical_max": 1.0,
        "digital_min": -32768,
        "digital_max": 32767,
        "transducer": "",
        "prefilter": "",
    })
    writer.setDatarecordDuration(1.0)
    writer.writeSamples([np.zeros(n_samples, dtype=np.float64)])
    for onset, duration, label in annotations:
        writer.writeAnnotation(float(onset), float(duration), label)
    writer.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCpapAhi:
    @pytest.fixture
    def cpap_setup(self, tmp_path, db, raw_conn):
        """
        Build a raw/cpap/2026-04-15/ directory with Summary + Annotations EDFs
        representing a 6-hour therapy session with 18 apnea events + 12 hypopneas.
        """
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()
        cpap_dir = raw_dir / "cpap" / "2026-04-15"
        cpap_dir.mkdir(parents=True)

        # Summary EDF: 360 samples (6 min per sample × 60 samples/hr × 6hrs = 360)
        # but we keep it modest (6 minutes) so test is fast
        duration_sec = 360  # 6 minutes for test; formulas use duration in minutes
        pressures = [10.2 + (i % 40) * 0.1 for i in range(duration_sec)]
        # Include a clearly high + low value so min/max are well-defined
        pressures[0] = 8.5
        pressures[-1] = 16.8
        summary_path = cpap_dir / "20260415_Summary.edf"
        _write_summary_edf(summary_path, duration_sec, pressures)

        # Annotations EDF: 3 obstructive + 2 central + 4 hypopnea + 1 clear_airway
        # + 2 rera + 1 mask_on + 1 mask_off
        annotations = [
            (30.0,  15.0, "ObstructiveApnea"),
            (90.0,  12.0, "Obstructive Apnea"),  # spacing variant
            (150.0, 18.0, "ObstructiveApnea"),
            (120.0, 20.0, "CentralApnea"),
            (200.0, 10.0, "Central Apnea"),
            (60.0,  22.0, "Hypopnea"),
            (130.0, 14.0, "Hypopnea"),
            (180.0, 16.0, "Hypopnea"),
            (260.0, 19.0, "Hypopnea"),
            (80.0,  11.0, "ClearAirway"),
            (100.0,  5.0, "RERA"),
            (170.0,  6.0, "RERA"),
            (0.0,    0.0, "Mask On"),
            (355.0,  0.0, "Mask Off"),
        ]
        ann_path = cpap_dir / "20260415_Annotations.edf"
        _write_annotations_edf(ann_path, annotations)

        # Register raw files with the RawStore — use the db.conn (same DB)
        store = RawStore(db.conn, raw_dir)
        for p in (summary_path, ann_path):
            rel = str(p.relative_to(raw_dir.parent))
            db.conn.execute(
                """
                INSERT INTO raw_files(source, date, kind, filepath, fetched_at, size_bytes)
                VALUES ('cpap', '2026-04-15', ?, ?, datetime('now'), ?)
                """,
                (p.name, rel, p.stat().st_size),
            )
            db.conn.commit()

        return store

    def test_ahi_computed_from_events(self, cpap_setup, db):
        parser = CpapParser(db, cpap_setup)
        result = parser.parse_date("2026-04-15")
        assert result.status == "ok", result.errors

        session = db.get_cpap_session("2026-04-15")
        assert session is not None

        # 3 obstructive + 2 central + 4 hypopnea + 1 clear_airway = 10 apnea/hypopnea
        # duration ~ 6 min = 0.1 h → AHI = 10 / 0.1 = 100 events/hour
        # (unusually high because test duration is short; math is what we verify)
        assert session["obstructive_events"] == 3
        assert session["central_events"] == 2
        assert session["hypopnea_events"] == 4
        assert session["clear_airway_events"] == 1
        assert session["rera_events"] == 2

        expected_ahi = (3 + 2 + 4 + 1) / (session["duration_minutes"] / 60.0)
        assert session["ahi"] is not None
        assert abs(session["ahi"] - expected_ahi) <= 0.2, (
            f"ahi={session['ahi']} expected≈{expected_ahi}"
        )

        # AI = apnea (incl. clear_airway) per hour; HI = hypopnea per hour
        expected_ai = (3 + 2 + 1) / (session["duration_minutes"] / 60.0)
        expected_hi = 4 / (session["duration_minutes"] / 60.0)
        assert abs(session["ai"] - expected_ai) <= 0.2
        assert abs(session["hi"] - expected_hi) <= 0.2

    def test_pressure_min_max_populated(self, cpap_setup, db):
        parser = CpapParser(db, cpap_setup)
        parser.parse_date("2026-04-15")
        session = db.get_cpap_session("2026-04-15")

        assert session["pressure_min"] is not None
        assert session["pressure_max"] is not None
        # Our synthesized range included 8.5 and 16.8
        assert session["pressure_min"] <= 8.5 + 0.1
        assert session["pressure_max"] >= 16.8 - 0.1
        assert session["pressure_median"] is not None

    def test_mask_on_off_counted(self, cpap_setup, db):
        parser = CpapParser(db, cpap_setup)
        parser.parse_date("2026-04-15")
        session = db.get_cpap_session("2026-04-15")
        # Fixture contained 1 Mask On + 1 Mask Off → 2
        assert session["mask_on_off_count"] == 2

    def test_events_saved_to_cpap_events_table(self, cpap_setup, db):
        parser = CpapParser(db, cpap_setup)
        parser.parse_date("2026-04-15")
        events = db.get_cpap_events("2026-04-15")
        # 3 obstructive + 2 central + 4 hypopnea + 1 clear_airway + 2 rera = 12
        assert len(events) == 12
        types = {e["event_type"] for e in events}
        assert types == {"obstructive", "central", "hypopnea", "clear_airway", "rera"}
        # Mask on/off must NOT appear as events
        assert not any("mask" in e.get("event_type", "") for e in events)
