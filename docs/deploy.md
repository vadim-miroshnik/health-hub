# Deployment — Health Hub

Notes for running Health Hub on a small always-on server (Beelink / Raspberry Pi).

## Requirements

- Python 3.11+
- SQLite 3.35+ (ships with Python)
- Optional: BLE adapter (O2Ring), SD card reader on shared network path (CPAP)

## One-time setup

```bash
git clone … health-hub && cd health-hub
make install-dev
cp .env.example .env
$EDITOR .env            # fill credentials + HC_INGEST_AUTH_TOKEN
make auth               # Fitbit OAuth2
make status             # sanity check
```

## Cron (daily pull + backup)

Suggested crontab:

```cron
# Fitbit daily pull + Telegram report — local time, after the watch syncs.
0 21 * * * cd /opt/health-hub && .venv/bin/hhub daily >> logs/daily.log 2>&1

# CPAP backfill — runs after the bridge uploads overnight EDF dumps.
0 10 * * * cd /opt/health-hub && .venv/bin/hhub backfill --source cpap >> logs/cpap.log 2>&1

# Nightly SQLite backup. MUST NOT overlap the 21:00 pull window.
0  3 * * * cd /opt/health-hub && .venv/bin/hhub backup  >> logs/backup.log 2>&1
```

Rotate `logs/*.log` with `logrotate` or `find logs -mtime +30 -delete`.

## Health Connect Ingest server (Phase 10)

The Android Health Connect Bridge app pushes record batches to
`POST /ingest/health-connect`. Run the server as a systemd service so it is
always available when the phone is on the same VPN.

### systemd unit

```ini
# /etc/systemd/system/health-hub-ingest.service
[Unit]
Description=Health Hub Health Connect ingest server
After=network.target

[Service]
Type=simple
User=hhub
WorkingDirectory=/opt/health-hub
Environment=HC_INGEST_AUTH_TOKEN=REPLACE_WITH_STRONG_SECRET
Environment=DB_PATH=/opt/health-hub/data/health.db
Environment=RAW_DIR=/opt/health-hub/data/raw
ExecStart=/opt/health-hub/.venv/bin/hhub serve-ingest --port 8765 --host 0.0.0.0
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

Activate:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now health-hub-ingest
sudo systemctl status health-hub-ingest
```

### Verifying with curl

```bash
TOKEN=$(grep HC_INGEST_AUTH_TOKEN .env | cut -d= -f2-)

# 1. Liveness
curl -fsS http://localhost:8765/health
# → {"ok":true}

# 2. Happy-path insert
curl -X POST http://localhost:8765/ingest/health-connect \
  -H "X-Auth-Token: $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"batch_id":"smoke-1","synced_at":"2026-04-16T10:00:00Z",
       "records":[{"uid":"smoke-hrv-1","type":"HeartRateVariabilityRmssd",
         "start_time":"2026-04-16T03:00:00Z","end_time":"2026-04-16T03:00:00Z",
         "value":42.5,"unit":"ms","source_app":"test","source_device":"curl",
         "metadata":{}}]}'
# → {"ok":true,"accepted":1,"duplicates":0}

# 3. Dedup check (same uid)
curl -X POST http://localhost:8765/ingest/health-connect \
  -H "X-Auth-Token: $TOKEN" -H "Content-Type: application/json" \
  -d '{"batch_id":"smoke-2","synced_at":"2026-04-16T10:05:00Z",
       "records":[{"uid":"smoke-hrv-1","type":"HeartRateVariabilityRmssd",
         "start_time":"2026-04-16T03:00:00Z","end_time":"2026-04-16T03:00:00Z",
         "value":42.5,"unit":"ms","source_app":"test","source_device":"curl",
         "metadata":{}}]}'
# → {"ok":true,"accepted":0,"duplicates":1}

# 4. Auth rejection
curl -o /dev/null -w '%{http_code}\n' \
  -X POST http://localhost:8765/ingest/health-connect \
  -H "Content-Type: application/json" -d '{}'
# → 401
```

### Nginx reverse proxy (optional)

```nginx
server {
  listen 443 ssl http2;
  server_name ingest.home.lan;

  ssl_certificate     /etc/ssl/home/fullchain.pem;
  ssl_certificate_key /etc/ssl/home/privkey.pem;

  location /ingest/ {
    proxy_pass http://127.0.0.1:8765;
    proxy_read_timeout 30s;
    client_max_body_size 1m;
  }
}
```

## MCP server

Claude Desktop (`~/.claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "health-hub": {
      "command": "/opt/health-hub/.venv/bin/hhub-mcp",
      "env": { "DB_PATH": "/opt/health-hub/data/health.db" }
    }
  }
}
```

The MCP server opens a per-request read-only connection to the SQLite DB so
the ingest server and cron collector can write concurrently.
