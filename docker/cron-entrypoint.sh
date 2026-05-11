#!/bin/sh
# Cron daemon entrypoint for hhub-cron container.
# Exports .env values into cron's environment (cron jobs don't inherit
# container env automatically) and runs cron in the foreground for docker.

set -eu

# Materialize current env into /etc/environment so cron jobs see it.
# `env -0` is safest against newlines/special chars in values.
env | grep -E '^(FITBIT_|TELEGRAM_|HC_|CPAP_|O2RING_|DB_PATH|RAW_DATA_DIR|TOKENS_PATH|TZ)' \
    > /etc/environment || true

mkdir -p /app/logs
chown -R hhub:hhub /app/logs /app/data 2>/dev/null || true

# Make sure the crontab file has a fresh trailing newline & correct perms.
chmod 0644 /etc/cron.d/hhub

# Foreground cron so docker can supervise it. `-f` foreground, `-L 15` log level.
exec cron -f -L 15
