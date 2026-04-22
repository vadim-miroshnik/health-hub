"""
Точка входа CLI: hhub <command> [args]

Production: daily, backfill, report, status
Debug:      fetch, show, auth check
"""

import argparse
import sys

from src.cli.debug import (
    cmd_auth_check,
    cmd_cpap_parse,
    cmd_fetch,
    cmd_o2ring_parse,
    cmd_preview,
    cmd_show,
)
from src.cli.backup import cmd_backup
from src.cli.production import cmd_backfill, cmd_daily, cmd_report, cmd_status
from src.cli.serve_ingest import cmd_serve_ingest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hhub",
        description="Health Hub — Fitbit + CPAP + O2Ring data collector",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    # ---- Production -------------------------------------------------------

    sub.add_parser("status", help="Show data coverage and source configuration")
    sub.add_parser("daily",  help="Collect today's data and send Telegram report")

    p_backfill = sub.add_parser("backfill", help="Load historical data")
    p_backfill.add_argument("--source", choices=["fitbit", "cpap", "o2ring"])
    p_backfill.add_argument("--start", metavar="DATE")
    p_backfill.add_argument("--end",   metavar="DATE")
    p_backfill.add_argument("--force", action="store_true", help="Re-fetch already synced days")

    p_report = sub.add_parser("report", help="Send Telegram report for a specific date")
    p_report.add_argument("date", nargs="?")

    # ---- Debug ------------------------------------------------------------

    p_fetch = sub.add_parser(
        "fetch",
        help="Fetch raw data from source for a date (no DB parse)",
    )
    p_fetch.add_argument("source", choices=["fitbit", "cpap", "o2ring"])
    p_fetch.add_argument("date",   metavar="DATE", help="YYYY-MM-DD")

    p_show = sub.add_parser(
        "show",
        help="Print all DB data for a date as JSON",
    )
    p_show.add_argument("date", metavar="DATE", help="YYYY-MM-DD")
    p_show.add_argument("--source", choices=["fitbit", "cpap", "o2ring"],
                        help="Limit to one source")

    p_preview = sub.add_parser("preview", help="Format and print Telegram report (no sending)")
    p_preview.add_argument("date", nargs="?", metavar="DATE", help="YYYY-MM-DD (default: yesterday)")

    p_auth = sub.add_parser("auth", help="Auth-related commands")
    auth_sub = p_auth.add_subparsers(dest="auth_cmd", metavar="CMD")
    auth_sub.required = True
    auth_sub.add_parser("check", help="Verify Fitbit OAuth token")

    # CPAP parse
    p_cpap = sub.add_parser("cpap-parse", help="Parse CPAP EDF files for a date into DB")
    p_cpap.add_argument("date", metavar="DATE", help="YYYY-MM-DD")

    # O2Ring parse
    p_o2r = sub.add_parser("o2ring-parse", help="Parse O2Ring CSV or binary file into DB")
    p_o2r.add_argument("date", metavar="DATE", help="YYYY-MM-DD")
    p_o2r.add_argument("file", metavar="FILE", help="Path to CSV or binary file")

    # Health Connect ingest server (Phase 10)
    p_ingest = sub.add_parser(
        "serve-ingest",
        help="Run the Health Connect HTTP ingest server (uvicorn)",
    )
    p_ingest.add_argument("--port", type=int, default=None,
                          help="TCP port (default 8765 or HC_INGEST_PORT env)")
    p_ingest.add_argument("--host", default=None,
                          help="Bind host (default 0.0.0.0 or HC_INGEST_HOST env)")

    sub.add_parser(
        "backup",
        help="Snapshot health.db to data/backups/health-YYYYMMDD.db and rotate",
    )

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "auth":
        if args.auth_cmd == "check":
            cmd_auth_check(args)
        return

    dispatch = {
        "status":       cmd_status,
        "daily":        cmd_daily,
        "backfill":     cmd_backfill,
        "report":       cmd_report,
        "fetch":        cmd_fetch,
        "show":         cmd_show,
        "preview":      cmd_preview,
        "cpap-parse":   cmd_cpap_parse,
        "o2ring-parse": cmd_o2ring_parse,
        "serve-ingest": cmd_serve_ingest,
        "backup":       cmd_backup,
    }

    try:
        dispatch[args.command](args)
    except NotImplementedError as e:
        print(f"Not implemented yet: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
