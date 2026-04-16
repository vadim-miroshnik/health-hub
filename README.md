# Health Hub

Единая система сбора, хранения и анализа данных о здоровье.

**Источники:** Fitbit (питание, активность, сон, вес, HRV) · ResMed AirSense 10 через CPAP-AutoSync · Wellue O2Ring S (пульсоксиметрия) · Google Health Connect (HRV, Skin Temperature, все типы с Pixel Watch 3)

**Хранение:** двухуровневое — raw-файлы на диске + структурированный SQLite

**Выход:** ежевечерняя сводка в Telegram + MCP-сервер для доступа Claude ко всем данным

## Быстрый старт

```bash
make install-dev       # создать venv и установить зависимости
cp .env.example .env   # заполнить credentials
make auth              # первичная OAuth2 авторизация Fitbit
make backfill          # загрузить историю
make daily             # собрать данные за сегодня и отправить в Telegram
```

## CLI

```bash
hhub daily                        # сбор + Telegram-отчёт (cron)
hhub backfill [--source fitbit|cpap|o2ring] [--start DATE] [--end DATE]
hhub report [DATE]                # повтор отчёта за конкретный день
hhub status                       # покрытие данных по источникам

hhub fetch <source> <date>        # забрать сырые данные
hhub parse <source> <date>        # перепарсить raw → db
hhub show <date>                  # вывести данные из db в JSON
hhub preview [DATE]               # сформировать Telegram-текст без отправки
hhub auth check                   # проверить OAuth токен
hhub db check                     # целостность БД
hhub telegram test                # тест Telegram credentials
```

## Архитектура

```
src/                — бизнес-логика (collector, parsers, db, formatter, telegram)
src/ingest_server.py — HTTP ingest endpoint для Health Connect Android app (Phase 10)
src/cli/            — CLI подкоманды (production + debug)
mcp_server/         — read-only MCP-сервер поверх SQLite
auth/               — OAuth2 авторизация Fitbit
migrations/         — SQL-миграции схемы БД
data/               — SQLite + raw data lake (gitignored)
tests/              — unit / integration / e2e
```

## Тесты

```bash
make test        # unit + integration
make test-all    # + e2e
make coverage    # с HTML-отчётом покрытия
```
