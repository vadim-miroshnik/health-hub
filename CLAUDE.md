# Health Hub

Единая система сбора данных о здоровье: Fitbit + CPAP (ResMed AirSense 10) + O2Ring S + **Google Health Connect** → SQLite + Telegram + MCP.
Полный план: [docs/plan.md](docs/plan.md).

## Статус

| Фаза | Что | Статус |
|------|-----|--------|
| 1 | OAuth2 (`auth/oauth_setup.py`, `src/fitbit_client.py`) | ✅ done |
| 2 | Storage layer (`src/migrations.py`, `src/raw_store.py`, `src/db.py`) | ✅ done |
| 3 | Collector (`src/collector.py`, 12 Fitbit эндпоинтов + intraday HR) | ✅ done |
| 4 | Backfill (`src/backfill.py`) | ✅ done |
| 5 | Formatter + Telegram (`src/formatter.py`, `src/telegram.py`) | ✅ done |
| 6 | CLI (`src/main.py`, `src/cli/`) | ✅ done |
| 7 | CPAP парсер (`src/cpap_parser.py`) | ✅ done |
| 8 | O2Ring collector (`src/o2ring_collector.py`) | ✅ done |
| 9 | MCP server (`mcp_server/server.py`, 24 tools) | ✅ done |
| 10 | Health Connect Ingest (`src/ingest_server.py`) | ⬜ next |

## Команды

```bash
make install-dev       # создать venv + установить зависимости
make auth              # первичная OAuth2 авторизация Fitbit
make test              # pytest unit + integration
make test-all          # + e2e
make coverage          # coverage HTML
make status            # hhub status

# Debug
hhub fetch fitbit <date>       # забрать данные Fitbit за дату
hhub show <date>               # вывести БД за дату (JSON)
hhub preview [date]            # Telegram-отчёт в stdout
hhub backfill                  # исторический backfill
hhub cpap-parse <date>         # парсить EDF файлы CPAP
hhub o2ring-parse <date> <file># парсить O2Ring CSV/binary

# MCP server
hhub-mcp                       # stdio MCP server для Claude Desktop
```

## Стек

- Python 3.11+, SQLite (WAL), no async, no Docker
- `requests`, `python-dotenv`, `tqdm`, `pyedflib`, `bleak`, `mcp`
- Фаза 10: `fastapi`, `uvicorn` — HTTP ingest server
- Tests: `pytest`, `responses` (HTTP mock), `freezegun`, `pytest-mock`

## Критичные правила

- **CPAP_DATA_DIR / O2RING_DATA_DIR не заданы или путь не существует** → источник отключён, не ошибка.
- **FITBIT_CLIENT_ID / SECRET пусты** → `RuntimeError` при старте, запустить `make auth`.
- **TELEGRAM_BOT_TOKEN / CHAT_ID пусты** → Telegram опционален, алерты не отправляются.
- **HC_INGEST_AUTH_TOKEN не задан** → ingest server не запускается, остальное работает.
- Raw-файлы на диске (`data/raw/`) + структурированный SQLite (`data/health.db`) — два уровня хранения.
- Посекундные CPAP-каналы **только** в EDF, не в БД.
- Миграции идемпотентны, применяются автоматически при открытии `Database`.
- Health Connect записи идемпотентны по `uid`: повторный push того же `uid` UPSERT'ит `data_json`/timestamps (last-write-wins). Это позволяет re-sync чинить записи если Android-app начал слать обогащённый payload (например, добавил `stages` в `SleepSession`). Счётчик в ответе: `accepted` = первый раз для этого uid, `duplicates` = uid уже был, payload обновлён.

## Timezone policy

Все `date` столбцы в БД — **локальная wall-clock дата** (часовой пояс пользователя, резидентно Europe/Moscow). UTC-нормализация на колонки `date` не применяется; для кросс-источниковых join по `date` источники выровнены по локальному календарному дню.

- `daily_*`, `sleep_sessions.date_of_sleep`, `cpap_sessions.date`, `o2ring_sessions.date`, `sync_log.date`, `raw_files.date` — локальная дата.
- `hc_records.start_time` / `end_time` — ISO8601 с TZ (UTC `Z` от Health Connect).
- `hc_records.date` — производное: `date(start_time_local)` (конверсия `start_time` → локальный tz → дата). Формула живёт в `ingest_server.py`.
- `ingested_at`, `synced_at`, `updated_at`, `fetched_at` — UTC ISO8601 (служебные timestamps).
- DST-переходы принимаются как есть; 23- и 25-часовые сутки — валидные записи, не ошибка.

Миграция `005_add_health_connect.sql` ссылается на эту политику в header-комментарии.

## Архитектура источников

| Источник | Сбор | Хранение |
|---|---|---|
| Fitbit | `collector.py` (API pull, cron) | `daily_*`, `sleep_*`, `hr_intraday`, `hc_records` |
| CPAP | `cpap_parser.py` (EDF с SD-карты) | `cpap_sessions`, `cpap_events` + EDF файлы |
| O2Ring | `o2ring_collector.py` (CSV / binary) | `o2ring_sessions`, `o2ring_data` |
| Health Connect | `ingest_server.py` (HTTP push) | `hc_records` (универсальная таблица) |

## Секреты (не коммитить)

- `.env` — credentials Fitbit, Telegram, пути к данным, HC_INGEST_AUTH_TOKEN (см. `.env.example`)
- `tokens.json` — OAuth2 токены Fitbit
- `data/` — SQLite и raw data lake
