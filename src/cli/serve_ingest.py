"""
`hhub serve-ingest` — run the Health Connect HTTP ingest server under uvicorn.

Refuses to start if HC_INGEST_AUTH_TOKEN is not set in the environment —
running open would let any caller write into hc_records.
"""

import os
import sys


def cmd_serve_ingest(args) -> None:
    token = os.environ.get("HC_INGEST_AUTH_TOKEN", "").strip()
    if not token:
        print(
            "ERROR: HC_INGEST_AUTH_TOKEN is not set — refusing to start "
            "unauthenticated ingest server. Set it in .env.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        import uvicorn
    except ImportError:
        print(
            "ERROR: uvicorn not installed. Run: pip install -e '.' (or make install).",
            file=sys.stderr,
        )
        sys.exit(1)

    port = int(getattr(args, "port", None) or os.environ.get("HC_INGEST_PORT", 8765))
    host = getattr(args, "host", None) or os.environ.get("HC_INGEST_HOST", "0.0.0.0")
    uvicorn.run("src.ingest_server:app", host=host, port=port, log_level="info")
