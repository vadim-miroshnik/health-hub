"""
P0.4: Collector must NOT swallow 403 — that's a permission/scope issue, not
"no data". Only 404 is silent.
"""

import json
import time
from pathlib import Path

import pytest
import responses as resp_lib

from src.collector import Collector
from src.fitbit_client import FitbitClient
from src.raw_store import RawStore

API = "https://api.fitbit.com"
DATE = "2026-04-15"
FIXTURES = Path(__file__).parent.parent / "fixtures" / "fitbit"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def tokens_file(tmp_path: Path) -> Path:
    p = tmp_path / "tokens.json"
    p.write_text(json.dumps({
        "access_token": "t",
        "refresh_token": "r",
        "expires_at": time.time() + 3600,
    }))
    return p


@pytest.fixture
def fitbit_client(tokens_file: Path) -> FitbitClient:
    return FitbitClient(tokens_path=tokens_file, client_id="c", client_secret="s")


@pytest.fixture
def collector(fitbit_client, db, tmp_path) -> Collector:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    return Collector(fitbit_client, db, RawStore(db.conn, raw_dir))


def _mock_happy_except(bad_kind: str, bad_status: int) -> None:
    """
    Register successful responses for every endpoint except `bad_kind`, which
    returns `bad_status`. Devices endpoint also mocked happy.
    """
    mapping = [
        ("nutrition",    f"{API}/1/user/-/foods/log/date/{DATE}.json", "nutrition.json"),
        ("water",        f"{API}/1/user/-/foods/log/water/date/{DATE}.json", "water.json"),
        ("activity",     f"{API}/1/user/-/activities/date/{DATE}.json", "activity.json"),
        ("sleep",        f"{API}/1.2/user/-/sleep/date/{DATE}.json", "sleep_stages.json"),
        ("weight",       f"{API}/1/user/-/body/log/weight/date/{DATE}.json", "weight.json"),
        ("hrv",          f"{API}/1.2/user/-/hrv/date/{DATE}.json", "hrv.json"),
        ("heart_rate",   f"{API}/1/user/-/activities/heart/date/{DATE}/1d/1min.json", "heart_rate.json"),
        ("azm",          f"{API}/1/user/-/activities/active-zone-minutes/date/{DATE}.json", "azm.json"),
        ("br",           f"{API}/1/user/-/br/date/{DATE}.json", "br.json"),
        ("spo2",         f"{API}/1/user/-/spo2/date/{DATE}.json", "spo2.json"),
        ("skin_temp",    f"{API}/1/user/-/temp/skin/date/{DATE}.json", "skin_temp.json"),
        ("cardio_score", f"{API}/1/user/-/cardioscore/date/{DATE}.json", "cardio_score.json"),
    ]
    for kind, url, fixture in mapping:
        if kind == bad_kind:
            resp_lib.add(resp_lib.GET, url, json={"error": bad_kind}, status=bad_status)
        else:
            resp_lib.add(resp_lib.GET, url, json=_load(fixture), status=200)
    resp_lib.add(resp_lib.GET, f"{API}/1/user/-/devices.json",
                 json=_load("devices.json"), status=200)


@resp_lib.activate
def test_403_surfaces_as_error(collector: Collector) -> None:
    _mock_happy_except("hrv", 403)
    result = collector.collect_day(DATE)
    assert result.status in ("partial", "error"), (
        f"403 on one endpoint should NOT be 'ok'; got {result.status}"
    )
    assert any("hrv" in e and "403" in e for e in result.errors), (
        f"Expected 403/hrv substring in errors, got {result.errors}"
    )


@resp_lib.activate
def test_404_still_silent(collector: Collector, db) -> None:
    _mock_happy_except("hrv", 404)
    result = collector.collect_day(DATE)
    # 404 = "no data", not an error
    assert result.status == "ok", (
        f"404 on hrv should remain silent, got {result.status} with errors {result.errors}"
    )
    assert db.get_sync_status(DATE)["status"] == "ok"


@resp_lib.activate
def test_403_recorded_in_sync_log(collector: Collector, db) -> None:
    _mock_happy_except("spo2", 403)
    collector.collect_day(DATE)
    status = db.get_sync_status(DATE)
    assert status is not None
    assert status["status"] != "ok"
    assert any("spo2" in e for e in status["errors"])
