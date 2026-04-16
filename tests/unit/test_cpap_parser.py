import pytest
from unittest.mock import MagicMock, patch

from src.cpap_parser import CpapParser, CpapParseResult


class TestCpapParserNoFiles:
    def test_no_data_when_no_raw_files(self, db):
        store = MagicMock()
        store.list_raw.return_value = []
        parser = CpapParser(db, store)
        result = parser.parse_date("2026-04-15")
        assert result.status == "no_data"
        assert result.date == "2026-04-15"

    def test_count_desaturations_pure_logic(self):
        from src.o2ring_collector import _count_desaturations
        assert _count_desaturations([97] * 50) == 0


class TestCpapParserWithFiles:
    def test_missing_file_path_returns_error(self, db, tmp_path):
        store = MagicMock()
        store._base = tmp_path / "raw"
        store.list_raw.return_value = [
            {"filepath": str(tmp_path / "20260415_Summary.edf"), "kind": "edf_summary"}
        ]
        parser = CpapParser(db, store)
        result = parser.parse_date("2026-04-15")
        # File doesn't exist → error, but gracefully handled
        assert result.status in ("error", "no_data")
