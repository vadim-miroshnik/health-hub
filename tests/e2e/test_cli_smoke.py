"""
Smoke-тесты CLI через subprocess.

Проверяют что:
- entry point работает (нет import errors)
- argparse сконфигурирован корректно
- каждая команда завершается с ожидаемым кодом
- вывод соответствует ожидаемому формату

Реальных API-вызовов нет — Fitbit credentials намеренно пустые.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Используем тот же Python что и в тестовом окружении
PYTHON = sys.executable
PROJECT = Path(__file__).parent.parent.parent

# Запускаем через python -m src.main чтобы не зависеть от пути бинарника
def hhub(*args, env_extra: dict | None = None, cwd=None) -> subprocess.CompletedProcess:
    base_env = {
        "PATH": str(PROJECT / ".venv" / "bin") + ":/usr/bin:/bin",
        "PYTHONPATH": str(PROJECT),
        # Credentials намеренно пустые — тесты не должны делать реальных запросов
        "NO_DOTENV": "1",  # не грузить .env из корня проекта
        "FITBIT_CLIENT_ID": "",
        "FITBIT_CLIENT_SECRET": "",
        "TELEGRAM_BOT_TOKEN": "",
        "TELEGRAM_CHAT_ID": "",
        "TOKENS_PATH": str(PROJECT / "tokens.json"),
        "DB_PATH": str(PROJECT / "data" / "health.db"),
        "RAW_DATA_DIR": str(PROJECT / "data" / "raw"),
        "CPAP_DATA_DIR": "",
        "O2RING_DATA_DIR": "",
    }
    if env_extra:
        base_env.update(env_extra)
    return subprocess.run(
        [PYTHON, "-m", "src.main", *args],
        capture_output=True,
        text=True,
        cwd=str(PROJECT),
        env=base_env,
    )


# ===========================================================================
# --help
# ===========================================================================

class TestHelp:
    def test_help_exits_zero(self):
        r = hhub("--help")
        assert r.returncode == 0

    def test_help_lists_all_commands(self):
        r = hhub("--help")
        for cmd in ("status", "fetch", "show", "auth", "daily", "backfill"):
            assert cmd in r.stdout

    def test_unknown_command_exits_nonzero(self):
        r = hhub("nonexistent-command")
        assert r.returncode != 0

    def test_no_args_exits_nonzero(self):
        r = hhub()
        assert r.returncode != 0


# ===========================================================================
# hhub status
# ===========================================================================

class TestStatus:
    def test_exits_zero(self):
        r = hhub("status")
        assert r.returncode == 0

    def test_shows_sources_header(self):
        r = hhub("status")
        assert "Sources:" in r.stdout

    def test_shows_all_three_sources(self):
        r = hhub("status")
        assert "Fitbit" in r.stdout
        assert "CPAP" in r.stdout
        assert "O2Ring" in r.stdout

    def test_fitbit_disabled_when_no_creds(self):
        r = hhub("status")
        assert "disabled" in r.stdout

    def test_cpap_disabled_when_no_dir(self):
        r = hhub("status")
        # CPAP_DATA_DIR пустая → disabled
        lines = r.stdout.splitlines()
        cpap_line = next((l for l in lines if "CPAP" in l), "")
        assert "disabled" in cpap_line


# ===========================================================================
# hhub show <date>
# ===========================================================================

class TestShow:
    def test_exits_zero_on_missing_db(self):
        r = hhub("show", "2020-01-01",
                 env_extra={"DB_PATH": "/tmp/nonexistent_health_hub_test.db"})
        assert r.returncode == 0

    def test_outputs_valid_json(self):
        r = hhub("show", "2020-01-01",
                 env_extra={"DB_PATH": "/tmp/nonexistent_health_hub_test.db"})
        data = json.loads(r.stdout)
        assert isinstance(data, dict)

    def test_json_has_date_key(self):
        r = hhub("show", "2020-01-01",
                 env_extra={"DB_PATH": "/tmp/nonexistent_health_hub_test.db"})
        data = json.loads(r.stdout)
        assert data["date"] == "2020-01-01"

    def test_json_has_all_source_keys(self):
        r = hhub("show", "2020-01-01",
                 env_extra={"DB_PATH": "/tmp/nonexistent_health_hub_test.db"})
        data = json.loads(r.stdout)
        for key in ("nutrition", "activity", "sleep", "weight", "hrv", "cpap", "o2ring"):
            assert key in data

    def test_empty_db_returns_nulls(self):
        r = hhub("show", "2020-01-01",
                 env_extra={"DB_PATH": "/tmp/nonexistent_health_hub_test.db"})
        data = json.loads(r.stdout)
        assert data["nutrition"] is None
        assert data["sleep"] == []

    def test_show_with_source_flag(self, tmp_path):
        db = tmp_path / "test.db"
        r = hhub("show", "2020-01-01", "--source", "fitbit",
                 env_extra={"DB_PATH": str(db)})
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert "nutrition" in data

    def test_show_cpap_source(self):
        # DB не существует → возвращает пустую cpap-структуру
        r = hhub("show", "2020-01-01", "--source", "cpap",
                 env_extra={"DB_PATH": "/tmp/nonexistent_health_hub_test.db"})
        assert r.returncode == 0
        data = json.loads(r.stdout)
        assert "cpap_session" in data


# ===========================================================================
# hhub auth check
# ===========================================================================

class TestAuthCheck:
    def test_exits_nonzero_when_no_credentials(self):
        r = hhub("auth", "check")
        assert r.returncode == 1

    def test_error_message_mentions_credentials(self):
        r = hhub("auth", "check")
        assert "FITBIT_CLIENT_ID" in r.stderr or "CLIENT_SECRET" in r.stderr

    def test_auth_check_help(self):
        r = hhub("auth", "--help")
        assert r.returncode == 0
        assert "check" in r.stdout


# ===========================================================================
# hhub fetch <source> <date>
# ===========================================================================

class TestFetch:
    def test_exits_nonzero_when_no_credentials(self):
        r = hhub("fetch", "fitbit", "2026-04-15")
        assert r.returncode == 1

    def test_error_mentions_credentials(self):
        r = hhub("fetch", "fitbit", "2026-04-15")
        assert "FITBIT_CLIENT_ID" in r.stderr or "CLIENT_SECRET" in r.stderr

    def test_fetch_help(self):
        r = hhub("fetch", "--help")
        assert r.returncode == 0

    def test_invalid_source_exits_nonzero(self):
        r = hhub("fetch", "--help")
        # argparse не пропустит невалидный source
        r2 = subprocess.run(
            [PYTHON, "-m", "src.main", "fetch", "invalid_source", "2020-01-01"],
            capture_output=True, text=True, cwd=str(PROJECT),
            env={
                "PATH": "/usr/bin:/bin",
                "PYTHONPATH": str(PROJECT),
                "FITBIT_CLIENT_ID": "", "FITBIT_CLIENT_SECRET": "",
            }
        )
        assert r2.returncode != 0


# ===========================================================================
# Нереализованные команды — понятная ошибка
# ===========================================================================

class TestStubs:
    def test_daily_skips_fitbit_when_no_credentials(self):
        """
        Fitbit is now optional (deprecated Sept 2026, registrations closed).
        `hhub daily` must NOT crash without credentials — it logs the skip
        and proceeds to report from whatever data is in the DB. With an
        empty DB it still exits 1 ("No data to report"), which is fine.
        """
        r = hhub("daily")
        assert r.returncode == 1
        combined = r.stdout + r.stderr
        assert "Fitbit collector skipped" in combined
        assert "FITBIT_CLIENT_ID" in combined or "CLIENT_SECRET" in combined

    def test_backfill_exits_nonzero_when_no_credentials(self):
        r = hhub("backfill")
        assert r.returncode == 1
        assert "FITBIT_CLIENT_ID" in r.stderr or "CLIENT_SECRET" in r.stderr
