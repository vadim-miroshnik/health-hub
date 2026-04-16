# Health Data Hub — Fitbit + CPAP + O2Ring + Health Connect → Telegram + MCP

## Обзор проекта

Единая система сбора, хранения и анализа данных о здоровье из **четырёх источников**: Fitbit (питание, активность, сон, вес, HRV + 12 эндпоинтов), ResMed AirSense 10 CPAP (терапия апноэ), Wellue O2Ring S (пульсоксиметрия), **Google Health Connect** (Pixel Watch 3 и другие Android-устройства, push через HTTP). Хранение в SQLite, ежевечерняя сводка в Telegram, MCP-сервер для доступа Claude ко всем данным. Python 3.11+.

## Архитектура

```
health-hub/
├── src/
│   ├── __init__.py
│   ├── fitbit_client.py      # OAuth2 + запросы к Fitbit API
│   ├── cpap_parser.py        # Парсинг данных ResMed с SD-карты (формат OSCAR)
│   ├── o2ring_collector.py   # Сбор данных с O2Ring (BLE или CSV)
│   ├── raw_store.py          # Работа с файловым raw data lake
│   ├── db.py                 # SQLite: схема, запись, чтение
│   ├── migrations.py         # Миграции схемы БД по schema_version
│   ├── collector.py          # Оркестрация сбора из всех источников
│   ├── backfill.py           # Загрузка истории (Fitbit API + архив CPAP/O2Ring)
│   ├── formatter.py          # Форматирование → Telegram MarkdownV2
│   ├── telegram.py           # Отправка через Bot API
│   ├── ingest_server.py      # HTTP ingest endpoint для Health Connect (FastAPI)
│   ├── cli/                  # CLI подкоманды
│   │   ├── __init__.py
│   │   ├── production.py     # daily, backfill, report, status
│   │   └── debug.py          # fetch, parse, show, preview, auth, db, telegram
│   └── main.py               # Точка входа argparse
├── mcp_server/
│   ├── __init__.py
│   └── server.py             # MCP-сервер (read-only tools)
├── auth/
│   └── oauth_setup.py        # Одноразовая OAuth2 авторизация Fitbit
├── tests/
│   ├── __init__.py
│   ├── conftest.py           # Pytest фикстуры (tmp_db, mock_fitbit, sample data)
│   ├── fixtures/             # Реальные примеры данных
│   │   ├── fitbit/           # JSON-ответы API (sleep stages/classic, nutrition, activity)
│   │   ├── cpap/             # sample EDF-файлы
│   │   └── o2ring/           # sample CSV и бинарники
│   ├── unit/                 # Изолированные юнит-тесты
│   ├── integration/          # Интеграционные (collector → db → formatter)
│   └── e2e/                  # End-to-end с мок-источниками
├── data/
│   ├── health.db             # SQLite база (структурированные данные)
│   └── raw/                  # Raw data lake (оригинальные файлы от источников)
│       ├── fitbit/{date}/{endpoint}.json
│       ├── cpap/{date}/*.edf
│       ├── o2ring/{date}/*.bin
│       └── health_connect/{date}/batch_{uuid}.json
├── migrations/
│   ├── 001_initial.sql
│   ├── 002_add_cpap.sql
│   ├── 003_add_o2ring.sql
│   ├── 004_expand_fitbit.sql   # HR intraday, AZM, health_metrics, devices
│   └── 005_add_health_connect.sql
├── tokens.json               # OAuth токены (gitignored)
├── .env                      # Credentials + пути к данным CPAP/O2Ring
├── .env.example
├── requirements.txt
├── requirements-dev.txt      # pytest, pytest-mock, responses, freezegun
├── pyproject.toml            # pytest config, entry point `hhub`
├── Makefile
└── README.md
```

### Принципы хранения

**Двухуровневое хранение:** raw files на диске + structured data в SQLite. Raw — это страховка и возможность переструктурировать без повторного похода к источнику. Structured — для быстрых запросов из MCP.

**Что попадает в SQLite:** всё что помещается в таблицы среднего размера и нужно для запросов. Sleep stages (30-сек интервалы, ~900 записей/ночь) — в БД. O2Ring 4-секундные данные (~7200/ночь) — в БД, но с отдельным решением по ретеншну (см. ниже).

**Что остаётся в файлах:** raw JSON Fitbit, EDF CPAP, бинарники O2Ring, детальные CPAP каналы (посекундные давление/поток/утечка — за 5 лет это 200+M строк, SQLite захлебнётся). MCP tool для детальных CPAP данных читает EDF on-demand через pyedflib.

**Миграции:** таблица `schema_version`, файлы `migrations/NNN_name.sql`, применяются идемпотентно при старте.

## Схема БД (SQLite)

```sql
-- === Метаданные ===

CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL,
    description TEXT
);

-- Индекс сырых файлов на диске (что где лежит, без содержимого)
CREATE TABLE raw_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,         -- 'fitbit', 'cpap', 'o2ring'
    date TEXT NOT NULL,
    kind TEXT NOT NULL,           -- 'nutrition', 'sleep', 'edf_pressure', 'o2ring_session'
    filepath TEXT NOT NULL,       -- путь относительно data/raw/
    fetched_at TEXT NOT NULL,
    size_bytes INTEGER,
    UNIQUE(source, date, kind)
);
CREATE INDEX idx_raw_files_date ON raw_files(date, source);

-- === Fitbit (структурированные данные) ===

CREATE TABLE daily_nutrition (
    date TEXT PRIMARY KEY,
    calories INTEGER,
    protein_g REAL,
    fat_g REAL,
    carbs_g REAL,
    fiber_g REAL,
    water_ml REAL
);

CREATE TABLE daily_activity (
    date TEXT PRIMARY KEY,
    steps INTEGER,
    distance_km REAL,
    floors INTEGER,
    calories_burned INTEGER,
    active_minutes_lightly INTEGER,
    active_minutes_fairly INTEGER,
    active_minutes_very INTEGER,
    sedentary_minutes INTEGER
);

CREATE TABLE sleep_sessions (
    log_id INTEGER PRIMARY KEY,  -- Fitbit log ID
    date_of_sleep TEXT NOT NULL,  -- дата пробуждения
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    duration_minutes INTEGER,
    efficiency INTEGER,
    is_main_sleep BOOLEAN,       -- true = основной сон, false = дневной/нэп
    log_type TEXT,                -- 'auto_detected' или 'manual'
    sleep_type TEXT,              -- 'stages' или 'classic'
    deep_minutes INTEGER,        -- NULL если classic
    light_minutes INTEGER,
    rem_minutes INTEGER,
    wake_minutes INTEGER,
    -- classic-only поля
    asleep_minutes INTEGER,      -- NULL если stages
    restless_minutes INTEGER,
    awake_minutes INTEGER,
    minutes_to_fall_asleep INTEGER,
    minutes_after_wakeup INTEGER,
    time_in_bed INTEGER
);

CREATE TABLE sleep_stages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    log_id INTEGER NOT NULL REFERENCES sleep_sessions(log_id),
    date_time TEXT NOT NULL,      -- ISO8601 таймстамп начала интервала
    level TEXT NOT NULL,          -- 'deep', 'light', 'rem', 'wake' (stages) или 'asleep', 'restless', 'awake' (classic)
    seconds INTEGER NOT NULL,    -- длительность интервала
    is_short BOOLEAN DEFAULT 0   -- true для shortData (кратковременные пробуждения <3мин)
);
CREATE INDEX idx_sleep_stages_log ON sleep_stages(log_id);
CREATE INDEX idx_sleep_stages_time ON sleep_stages(date_time);

CREATE TABLE daily_weight (
    date TEXT PRIMARY KEY,
    weight_kg REAL,
    bmi REAL,
    fat_percent REAL
);

CREATE TABLE daily_hrv (
    date TEXT PRIMARY KEY,
    rmssd REAL,
    coverage REAL,
    low_freq REAL,
    high_freq REAL
);

CREATE TABLE food_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    meal_type TEXT,              -- 'Breakfast', 'Lunch', 'Dinner', 'Snack'
    food_name TEXT,
    calories INTEGER,
    protein_g REAL,
    fat_g REAL,
    carbs_g REAL,
    amount REAL,
    unit TEXT
);

CREATE TABLE sync_log (
    date TEXT PRIMARY KEY,
    synced_at TEXT NOT NULL,
    status TEXT NOT NULL,        -- 'ok', 'partial', 'error'
    errors TEXT                  -- JSON массив ошибок если есть
);

-- === Fitbit расширенные метрики (migration 004) ===

CREATE TABLE daily_health_metrics (
    date TEXT PRIMARY KEY,
    breathing_rate REAL,         -- дыхательная частота, вдохов/мин
    spo2_avg REAL,               -- средний ночной SpO2, %
    spo2_min REAL,               -- минимальный ночной SpO2, %
    skin_temp_delta REAL,        -- отклонение температуры кожи от базовой, °C
    cardio_score_min REAL,       -- VO2 max нижняя граница
    cardio_score_max REAL        -- VO2 max верхняя граница
);

CREATE TABLE daily_heart_rate (
    date TEXT PRIMARY KEY,
    resting_hr INTEGER,
    out_of_range_minutes INTEGER,
    fat_burn_minutes INTEGER,
    cardio_minutes INTEGER,
    peak_minutes INTEGER,
    out_of_range_calories REAL,
    fat_burn_calories REAL,
    cardio_calories REAL,
    peak_calories REAL
);

-- Intraday HR (~1440 строк/день, ~2.6M за 5 лет)
CREATE TABLE hr_intraday (
    date TEXT NOT NULL,
    time TEXT NOT NULL,          -- HH:MM:SS
    bpm INTEGER,
    PRIMARY KEY (date, time)
);
CREATE INDEX idx_hr_intraday_date ON hr_intraday(date);

CREATE TABLE daily_azm (
    date TEXT PRIMARY KEY,
    fat_burn_minutes INTEGER,
    cardio_minutes INTEGER,
    peak_minutes INTEGER,
    total_minutes INTEGER
);

CREATE TABLE activity_log (
    log_id INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    start_time TEXT NOT NULL,
    name TEXT,
    duration_minutes INTEGER,
    calories INTEGER,
    distance_km REAL,
    avg_hr INTEGER,
    max_hr INTEGER,
    steps INTEGER
);
CREATE INDEX idx_activity_log_date ON activity_log(date);

CREATE TABLE devices (
    id TEXT PRIMARY KEY,
    updated_at TEXT NOT NULL,
    device_version TEXT,
    battery TEXT,                -- 'High', 'Medium', 'Low', 'Empty'
    battery_level INTEGER,       -- 0-100
    last_sync_time TEXT,
    device_type TEXT             -- 'TRACKER', 'SCALE'
);

-- === Google Health Connect (migration 005) ===

-- Универсальная таблица для всех записей Health Connect
-- Единая схема вместо 30+ отдельных таблиц; VIEW'ы для часто используемых метрик
CREATE TABLE hc_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL UNIQUE,           -- UID от Health Connect (идемпотентность)
    type TEXT NOT NULL,                 -- HeartRateVariabilityRmssd, SleepSession, etc.
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    date TEXT NOT NULL,                 -- дата start_time для быстрого фильтра
    value REAL,                         -- для scalar-типов
    unit TEXT,
    source_app TEXT,
    source_device TEXT,
    data_json TEXT NOT NULL,            -- полная запись с metadata
    ingested_at TEXT NOT NULL
);
CREATE INDEX idx_hc_records_date_type ON hc_records(date, type);
CREATE INDEX idx_hc_records_type_time ON hc_records(type, start_time);

-- VIEW'ы для часто запрашиваемых метрик
CREATE VIEW daily_hc_hrv AS
SELECT date, AVG(value) avg_rmssd, MIN(value) min_rmssd, MAX(value) max_rmssd, COUNT(*) measurements
FROM hc_records WHERE type = 'HeartRateVariabilityRmssd' GROUP BY date;

CREATE VIEW daily_hc_skin_temp AS
SELECT date, AVG(value) avg_temp, MIN(value) min_temp, MAX(value) max_temp
FROM hc_records WHERE type = 'SkinTemperature' GROUP BY date;

CREATE VIEW daily_hc_resting_hr AS
SELECT date, AVG(value) avg_resting_hr FROM hc_records
WHERE type = 'RestingHeartRate' GROUP BY date;

-- === CPAP (ResMed AirSense 10 через CPAP-AutoSync) ===

CREATE TABLE cpap_sessions (
    date TEXT PRIMARY KEY,       -- дата сессии
    start_time TEXT,
    end_time TEXT,
    duration_minutes INTEGER,    -- общее время использования
    ahi REAL,                    -- Apnea-Hypopnea Index
    ai REAL,                     -- Apnea Index
    hi REAL,                     -- Hypopnea Index
    obstructive_events INTEGER,
    central_events INTEGER,
    hypopnea_events INTEGER,
    clear_airway_events INTEGER,
    rera_events INTEGER,         -- Respiratory Effort-Related Arousal
    leak_median REAL,            -- утечка (медиана, л/мин)
    leak_95pct REAL,             -- утечка (95-й перцентиль)
    pressure_min REAL,           -- давление (мин, смH2O)
    pressure_max REAL,
    pressure_median REAL,
    pressure_95pct REAL,
    tidal_volume_median REAL,    -- дыхательный объём (мл)
    minute_vent_median REAL,     -- минутная вентиляция (л/мин)
    resp_rate_median REAL,       -- частота дыхания
    mask_on_off_count INTEGER    -- количество снятий маски
);

CREATE TABLE cpap_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    event_type TEXT NOT NULL,     -- 'obstructive', 'central', 'hypopnea', 'clear_airway', 'rera', 'flow_limit'
    duration_seconds REAL
);
CREATE INDEX idx_cpap_events_date ON cpap_events(date);

-- Детальные каналы (давление/поток/утечка посекундно) НЕ хранятся в SQLite.
-- Они остаются в исходных EDF-файлах в data/raw/cpap/{date}/,
-- MCP tool get_cpap_detailed() читает их on-demand через pyedflib.

-- === Пульсоксиметрия (Wellue O2Ring S) ===

CREATE TABLE o2ring_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,           -- дата ночи
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    duration_minutes INTEGER,
    avg_spo2 REAL,
    min_spo2 REAL,
    spo2_drops_count INTEGER,    -- количество десатураций
    avg_hr REAL,
    min_hr REAL,
    max_hr REAL,
    o2_score REAL                -- O2 Score из ViHealth
);

CREATE TABLE o2ring_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES o2ring_sessions(id),
    timestamp TEXT NOT NULL,      -- каждые 4 секунды
    spo2 INTEGER,                -- 0-100%
    heart_rate INTEGER,
    motion INTEGER               -- уровень движения
);
CREATE INDEX idx_o2ring_data_session ON o2ring_data(session_id);
CREATE INDEX idx_o2ring_data_time ON o2ring_data(timestamp);
```

Две стратегии хранения: `raw_responses` с полным JSON (чтобы ничего не потерять и можно было переструктурировать позже) + структурированные таблицы для быстрых запросов через MCP.

## Фазы разработки

### Фаза 1: OAuth2 модуль
**Файлы:** `auth/oauth_setup.py`, `src/fitbit_client.py`

- Скрипт первичной авторизации: localhost HTTP-сервер для OAuth callback, открывает браузер, обменивает code → tokens, сохраняет в `tokens.json`
- Fitbit app тип "Personal" на dev.fitbit.com
- `FitbitClient` класс: загрузка токенов, auto-refresh через refresh token, retry при 401
- Scope: `activity heartrate sleep weight nutrition profile`
- При невозможности refresh — alert в Telegram, exit(1)
- Rate limiting: 150 запросов/час для Personal app, встроенный sleep между запросами при backfill

### Фаза 2: Storage layer (raw files + SQLite + migrations)
**Файлы:** `src/raw_store.py`, `src/db.py`, `src/migrations.py`, `migrations/*.sql`

**raw_store.py** — модуль для работы с файловым хранилищем:
- `save_raw(source, date, kind, content)` — записывает файл в `data/raw/{source}/{date}/{kind}.{ext}`, регистрирует запись в `raw_files`
- `get_raw(source, date, kind)` — возвращает путь или содержимое файла
- `list_raw(source, [date_range])` — перечисление сохранённых файлов
- Дедупликация по `(source, date, kind)`: повторная запись перезаписывает файл и обновляет `fetched_at`

**migrations.py** — простая система миграций:
- При старте читает `schema_version`, применяет все `migrations/NNN_*.sql` с версией больше текущей
- Каждая миграция — один SQL-файл с транзакцией, идемпотентная (IF NOT EXISTS)
- После применения — INSERT в `schema_version`

**db.py** — работа со структурированными данными:
- Функции записи (Fitbit): `save_nutrition()`, `save_activity()`, `save_sleep_session()`, `save_sleep_stages()`, `save_weight()`, `save_hrv()`, `save_food_log()`, `save_water()`, `save_heart_rate()`, `save_hr_intraday()`, `save_health_metrics()`, `save_azm()`, `save_activity_log()`, `save_devices()`
- Функции записи (CPAP/O2Ring): `save_cpap_session()`, `save_cpap_events()`, `save_o2ring_session()`, `save_o2ring_data()`
- Функции чтения: `get_day(date)`, `get_range(start, end, metric)`, `get_latest(metric)`, `get_food_log(date)`, `get_sleep_stages(log_id)`, `get_sleep_sessions(date)`, `get_heart_rate(date)`, `get_hr_intraday(date)`, `get_health_metrics(date)`, `get_azm(date)`, `get_activity_log(date)`, `get_devices()`, `get_cpap_session(date)`, `get_o2ring_session(date)` и т.д.
- `is_date_synced(source, date)` — проверка по `sync_log`
- `PRAGMA journal_mode=WAL`, `PRAGMA synchronous=NORMAL` при открытии
- Batch inserts в транзакциях (sleep_stages, hr_intraday, o2ring_data)
- `save_health_metrics()` использует `COALESCE` при конфликте — несколько эндпоинтов (br, spo2, skin_temp, cardio_score) дополняют одну строку без перезаписи уже сохранённых полей

### Фаза 3: Сбор данных за день
**Файл:** `src/collector.py`

- `collect_day(date)` — забирает все эндпоинты за указанную дату, сохраняет JSON через raw_store, парсит в структурированные таблицы
- Эндпоинты:
  - `/1/user/-/foods/log/date/{date}.json` — питание + список продуктов + тренировки из `activities[]`
  - `/1/user/-/foods/log/water/date/{date}.json` — вода
  - `/1/user/-/activities/date/{date}.json` — сводка активности (шаги, дистанция, калории, минуты) + тренировки в `activity_log`
  - `/1.2/user/-/sleep/date/{date}.json` — сон (v1.2 — обязательно, v1 не отдаёт стадии)
  - `/1/user/-/body/log/weight/date/{date}.json` — вес
  - `/1.2/user/-/hrv/date/{date}.json` — HRV (404 если устройство не поддерживает — не ошибка)
  - `/1/user/-/activities/heart/date/{date}/1d/1min.json` — пульс покоя, зоны, intraday 1-мин (~1440 строк/день)
  - `/1/user/-/activities/active-zone-minutes/date/{date}.json` — Active Zone Minutes (fat burn / cardio / peak)
  - `/1/user/-/br/date/{date}.json` — дыхательная частота (404 если нет данных — не ошибка)
  - `/1/user/-/spo2/date/{date}.json` — SpO2 ночной avg/min/max (404 если нет данных — не ошибка)
  - `/1/user/-/temp/skin/date/{date}.json` — температура кожи, отклонение от базовой (404 если нет — не ошибка)
  - `/1/user/-/cardioscore/date/{date}.json` — Cardio Fitness Score / VO2 max диапазон (404 если нет — не ошибка)
- Дополнительно в `hhub fetch` (не в коллекторе — нет даты): `/1/user/-/devices.json` — статус устройств и батарейки
- Поток на каждый эндпоинт: `fetch → raw_store.save_raw → parse → db.save_*`. Если что-то сломается при парсинге, raw-файл уже на диске — можно перепарсить потом без повторного запроса к API
- **404 = нет данных, не ошибка:** HRV, BR, SpO2, skin temp, cardio score возвращают 404 если устройство не поддерживает метрику или данных за дату нет. Такие ответы логируются как info и не влияют на sync_log status
- Парсинг сна: из каждой sleep session → `sleep_sessions` (суммарные минуты по стадиям) + `sleep_stages` (каждый 30-сек интервал из `levels.data` и `levels.shortData`). Обработка обоих типов: `stages` (deep/light/rem/wake) и `classic` (asleep/restless/awake). `shortData` сохраняется с флагом `is_short=1`
- Intraday HR: ~1440 строк/день, за 5 лет ~2.6M строк — SQLite справляется. Хранится в `hr_intraday(date, time, bpm)` с индексом по date. Запись через DELETE + executemany в транзакции
- При ошибке отдельного эндпоинта — записывает partial, логирует ошибку в sync_log, продолжает остальные
- Идемпотентность: если день уже собран, пропускает (с флагом --force для перезаписи)

### Фаза 4: Historical backfill
**Файл:** `src/backfill.py`

- Загрузка всех данных с начала использования Fitbit до вчера
- Определение стартовой даты: `GET /1/user/-/profile.json` → `memberSince`
- Итерация по дням от `memberSince` до yesterday, пропуск уже синхронизированных
- Rate limiting: пауза между запросами чтобы не упереться в 150/час (6 эндпоинтов × день = ~25 дней/час)
- Progress bar в консоли (tqdm) и промежуточный отчёт в Telegram каждые 100 дней
- Возобновляемость: при обрыве перезапуск продолжит с последнего несинхронизированного дня
- CLI: `python -m src.backfill` или `make backfill`
- Опция `--start-date` и `--end-date` для частичного backfill

### Фаза 5: Форматирование и Telegram
**Файлы:** `src/formatter.py`, `src/telegram.py`

Telegram MarkdownV2 сводка из данных в БД (не из API напрямую). Формат:

```
📊 *Health Hub · 15 апреля 2026*

🍽 1842 kcal · Б 98 · Ж 72 · У 184
💧 1.8л

🏃 8 432 шага · 5.2 км · 47 акт.мин

😴 7ч 23м · 89% · Глуб 1:12 · REM 1:45

⚖️ 87.2 кг   ❤️ HRV 42 мс · Skin temp +0.2°C

🫁 CPAP 6ч 52м · AHI 3.2
  Обстр 4 · Центр 12 · Гипопн 6
  Утечка 4.1 л/м · Давл 10.2-14.8

🩸 SpO2 ср 95% · мин 88%
  Десатураций: 7 · HR ср 62
```

Секции с отсутствующими данными пропускаются. CPAP и O2Ring секции появляются только если данные за эту ночь есть.

### Фаза 6: CLI и оркестрация
**Файл:** `src/main.py`, `src/cli/*.py`

CLI через argparse с подкомандами. Разделён на два режима: **production** (для cron) и **debug** (для разработки и тестов).

**Production команды:**
- `hhub daily` — собрать сегодня из всех источников + отправить отчёт в Telegram (cron)
- `hhub backfill [--source SRC] [--start DATE] [--end DATE]` — историческая загрузка, можно по одному источнику
- `hhub report [DATE]` — собрать + отправить отчёт за конкретный день (повтор пропущенного)
- `hhub status` — сколько дней в БД по каждому источнику, последняя синхронизация, пробелы в данных

**Debug команды (работают без Telegram, вывод в stdout):**
- `hhub fetch <source> <date>` — забрать сырые данные из источника, сохранить в raw_store, показать что получено (без парсинга в БД)
- `hhub parse <source> <date>` — перепарсить уже сохранённый raw-файл в БД (для отладки парсеров без повторных API-запросов)
- `hhub show <date> [--source SRC]` — вывести всё что есть в БД за день в JSON (для инспекции результатов)
- `hhub preview [DATE]` — сформировать и вывести текст Telegram-отчёта в консоль, без отправки (--dry-run по умолчанию)
- `hhub auth check` — проверить валидность Fitbit OAuth токена, попробовать refresh
- `hhub db check` — проверка целостности БД: применены ли все миграции, нет ли orphaned записей, пробелы в sync_log
- `hhub telegram test` — отправить тестовое сообщение в Telegram чтобы убедиться что credentials работают

**CLI для парсеров напрямую (без сохранения в БД):**
- `hhub cpap parse <edf-file>` — распарсить EDF-файл, вывести summary в JSON
- `hhub o2ring parse <csv-or-bin>` — распарсить файл O2Ring, вывести summary в JSON
- `hhub fitbit parse-sleep <json-file>` — распарсить сохранённый JSON сна Fitbit

Эти команды удобны для: быстрой проверки парсеров на реальных данных, написания тестов (копируешь вывод в фикстуру), отладки проблем с конкретным днём.

Cron:
```cron
0 21 * * * cd /path/to/health-hub && .venv/bin/hhub daily >> logs/daily.log 2>&1
0 10 * * * cd /path/to/health-hub && .venv/bin/hhub backfill --source cpap >> logs/cpap.log 2>&1
```

### Фаза 7: Импорт данных CPAP (ResMed AirSense 10)
**Файл:** `src/cpap_parser.py`

Источник данных: CPAP-AutoSync (ESP32 WiFi SD-карта) автоматически заливает файлы с AirSense 10 на Beelink по сети. Данные лежат в стандартном формате SD-карты ResMed.

- Парсинг формата ResMed SD: структура каталогов `DATALOG/{date}/`, файлы `.edf` (European Data Format) для детальных каналов, бинарные summary-файлы
- Библиотека `pyedflib` для чтения EDF. Альтернатива: портировать логику из OSCAR (open source, GPL)
- EDF-файлы копируются в `data/raw/cpap/{date}/` через raw_store, регистрируются в `raw_files`
- Из EDF извлекается **только summary и события** для БД:
  - Агрегаты за ночь (AHI, медианы/перцентили давления и утечки, и т.д.) → `cpap_sessions`
  - События апноэ с таймстампами и типами → `cpap_events`
- **Посекундные каналы (давление, поток, утечка, resp rate) НЕ разворачиваются в БД** — остаются в EDF. За 5 лет это были бы сотни миллионов строк
- MCP tool `get_cpap_detailed(date, channel)` читает EDF через pyedflib on-demand и отдаёт данные нужного канала
- Watchdir: мониторинг папки куда CPAP-AutoSync складывает файлы, автоимпорт новых
- Backfill: обработка всех накопленных файлов на SD-карте при первом запуске

Важно: данные с AirSense 10 будут с задержкой — CPAP-AutoSync заливает после окончания терапии (утром). Cron для импорта CPAP можно поставить на ~10:00.

### Фаза 8: Импорт данных O2Ring S
**Файл:** `src/o2ring_collector.py`

O2Ring S записывает SpO2, пульс и движение каждые 4 секунды. Хранит 4 сессии по 10 часов.

Варианты сбора данных (от простого к сложному):
1. **CSV из ViHealth** — экспортировать вручную из приложения, положить в папку, скрипт подхватит. Минимум автоматизации, но работает сразу
2. **Бинарные файлы через O2 Insight Pro** — десктопное ПО, USB-подключение, файлы в `AppData/Local/O2 Insight Pro/DATA/`. Формат: 40-байтный заголовок + 5-байтные записи (little endian). Хорошо документирован в OSCAR
3. **BLE напрямую** — Python-скрипт `o2r` (bleak) скачивает данные по Bluetooth Low Energy. Самый автоматизированный вариант: Beelink с BLE-адаптером, cron утром, скачал → распарсил → записал в БД

Рекомендация: начать с варианта 1 (CSV), потом мигрировать на BLE когда основная система заработает.

- Парсинг CSV: timestamp, SpO2, HR, motion → `o2ring_data`
- Агрегация за сессию: avg/min SpO2, количество десатураций, avg/min/max HR → `o2ring_sessions`
- Десатурация определяется как падение SpO2 ≥ 3% от baseline на ≥ 10 секунд

### Фаза 9: MCP-сервер
**Файл:** `mcp_server/server.py`

Read-only MCP-сервер поверх SQLite. Протокол: stdio (для Claude Desktop) или SSE (для Claude Code / удалённый доступ).

Tools — Fitbit:
- `get_nutrition(date)` — питание за день с детализацией по продуктам
- `get_activity(date)` — активность за день
- `get_sleep(date)` — сон за день (все сессии с суммарными стадиями)
- `get_sleep_stages(date)` — полная гипнограмма: 30-секундные интервалы стадий сна
- `get_sleep_range(start_date, end_date)` — сон за период (для анализа трендов глубокого/REM сна)
- `get_weight(date)` — вес
- `get_hrv(date)` — HRV
- `get_food_log(date)` — детальный лог питания с продуктами
- `search_food_log(query, [start_date], [end_date])` — поиск по названиям продуктов

Tools — CPAP:
- `get_cpap_session(date)` — суммарная статистика CPAP за ночь (AHI, давление, утечки)
- `get_cpap_events(date)` — все события апноэ с таймстампами и типами
- `get_cpap_range(start_date, end_date)` — тренд AHI и ключевых метрик за период
- `get_cpap_detailed(date, channel)` — посекундные данные канала (давление, поток, утечка)

Tools — O2Ring:
- `get_oximetry(date)` — суммарная статистика пульсоксиметрии за ночь
- `get_oximetry_data(date)` — полные 4-секундные данные SpO2/HR/motion
- `get_oximetry_range(start_date, end_date)` — тренд средних/минимальных SpO2 за период

Tools — Health Connect:
- `get_hc_records(date, type)` — все записи заданного типа за дату (raw)
- `get_hc_hrv(date)` — HRV из Health Connect (avg/min/max rMSSD)
- `get_hc_hrv_range(start_date, end_date)` — тренд HRV
- `get_hc_skin_temp(date)` — температура кожи за дату
- `get_hc_skin_temp_range(start_date, end_date)` — тренд температуры
- `get_hc_resting_hr(date)` — пульс покоя из Health Connect
- `get_hc_vo2_max(date)` — VO2 max / Cardio Fitness Score

Tools — кросс-источники:
- `get_day_summary(date)` — всё за день из всех источников одним вызовом
- `get_night_summary(date)` — ночь целиком: Fitbit сон + CPAP + O2Ring + HC HRV/skin temp
- `get_range(metric, start_date, end_date)` — данные за период (для трендов)
- `get_status()` — метаинфо: диапазон данных по каждому источнику, последняя синхронизация

Библиотека: `mcp` (official Python SDK)

### Фаза 10: Health Connect Ingest Server
**Файл:** `src/ingest_server.py`

HTTP-сервер для приёма push-батчей от Android-приложения **Health Connect Bridge** (Pixel Watch 3 + другие Android-устройства).

**Endpoint:**
```
POST /ingest/health-connect
Headers: Content-Type: application/json
         X-Auth-Token: <HC_INGEST_AUTH_TOKEN из .env>
Body: {"batch_id": "uuid", "synced_at": "ISO8601", "records": [...]}
Response: {"ok": true, "accepted": N, "duplicates": M}
```

**Формат записи (record):**
```json
{
  "uid": "health-connect-record-uid",   // для идемпотентности
  "type": "HeartRateVariabilityRmssd",  // или любой HC тип
  "start_time": "2026-04-16T03:15:23Z",
  "end_time": "2026-04-16T03:15:23Z",
  "value": 35.2,
  "unit": "ms",
  "source_app": "com.google.android.apps.fitness",
  "source_device": "Pixel Watch 3",
  "metadata": {}
}
```

**Поддерживаемые типы:** `HeartRate`, `HeartRateVariabilityRmssd`, `RestingHeartRate`, `BloodPressure`, `OxygenSaturation`, `RespiratoryRate`, `SkinTemperature`, `Weight`, `BodyFat`, `Steps`, `Distance`, `ExerciseSession`, `ActiveCaloriesBurned`, `BasalMetabolicRate`, `Vo2Max`, `SleepSession`, `Nutrition`, `Hydration` и другие HC типы.

**Ingest pipeline:**
1. Проверка `X-Auth-Token`
2. Сохранение батча в `data/raw/health_connect/{date}/batch_{uuid}.json`
3. `INSERT INTO hc_records ... ON CONFLICT(uid) DO NOTHING` — дедупликация по uid
4. Возврат `{"ok": true, "accepted": N, "duplicates": M}`

**Деплой на Beelink:**
- systemd service `health-hub-ingest.service`
- Доступен через VPN или reverse proxy Nginx
- Порт: `HC_INGEST_PORT=8765` из .env

**Тестирование без Android:**
```bash
curl -X POST http://localhost:8765/ingest/health-connect \
  -H "X-Auth-Token: $HC_INGEST_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"batch_id":"test-1","synced_at":"2026-04-16T10:00:00Z","records":[
    {"uid":"test-hrv-1","type":"HeartRateVariabilityRmssd",
     "start_time":"2026-04-16T03:00:00Z","end_time":"2026-04-16T03:00:00Z",
     "value":42.5,"unit":"ms","source_app":"test","source_device":"test","metadata":{}}
  ]}'
# → {"ok": true, "accepted": 1, "duplicates": 0}
# Повторный запрос → {"ok": true, "accepted": 0, "duplicates": 1}
```

**Фазы реализации:**
1. Миграция 005 (`hc_records` + VIEW'ы) + тесты
2. FastAPI ingest server + auth + dedup тесты
3. systemd + nginx proxy + smoke test с curl
4. MCP tools для HC метрик
5. Обновить Telegram формат: добавить `❤️ HRV 42 мс · Skin temp +0.2°C`

**Зависимости:** `fastapi>=0.100`, `uvicorn>=0.23`


## Технические решения

- **SQLite + WAL + NORMAL sync** — одновременное чтение из MCP и запись из collector без конфликтов. Batch inserts в транзакциях для высокочастотных данных (sleep_stages, o2ring_data)
- **Raw data lake на файлах + structured в SQLite** — JSON/EDF/бинарники остаются файлами в `data/raw/{source}/{date}/`, в БД только путь через таблицу `raw_files`. SQLite не пухнет, бэкапы гранулярные (raw можно не бэкапить часто, db — ежедневно), переструктурирование без повторных запросов к API
- **Типизированные таблицы вместо generic metrics** — для single-user системы с MCP-потребителем структурированные таблицы проще и быстрее, чем unified `(timestamp, metric_type, value)` паттерн из enterprise health lakes. Trade-off: при добавлении источника — миграция схемы (через `migrations/*.sql`), а не просто новая строка в generic table. Для 3-5 источников это разумная цена
- **Детальные каналы CPAP — только в EDF** — посекундные данные давления/потока в БД — это 200M+ строк за 5 лет. Остаются в raw файлах, MCP читает on-demand
- **Schema migrations через нумерованные SQL-файлы** — простая реализация (без Alembic), таблица `schema_version`, идемпотентное применение
- **Native resolution, агрегация при запросе** — не ресемплим 4-сек O2Ring в 30-сек Fitbit при записи. Каждый источник хранится в своей гранулярности, MCP tools агрегируют по необходимости
- **Без async** — всё последовательно, для backfill rate limit всё равно узкое горло
- **Без docker** — venv на Beelink достаточно
- **MCP stdio + SSE** — stdio для Claude Desktop локально, SSE для удалённого доступа (как с TickTick MCP)

## Тестирование

**Стек:** pytest, pytest-mock, responses (HTTP мокинг), freezegun (фиксация времени), pytest-cov для покрытия.

**Принципы:**
- Никаких живых API-запросов в тестах — всё через `responses` с HTTP-фикстурами
- Никаких запросов к Telegram — `telegram.send()` мокается или заменяется на stdout через DI
- Реальная SQLite, но in-memory (`:memory:`) или в `tmp_path` из pytest — не мокать БД
- Фикстуры данных — реальные примеры (обрезанные до нескольких дней) положенные в `tests/fixtures/`. Получены через `hhub fetch` + анонимизация
- Покрытие — цель 80%+ на парсерах и formatter (где бывают тонкие баги), 60%+ на остальном

### Что тестировать

**fitbit_client.py** — юнит-тесты с мокнутыми HTTP-ответами:
- OAuth refresh: протухший access token → 401 → автоматический refresh через refresh token → повтор запроса
- Refresh token невалиден → exception + alert
- Rate limit 429 → backoff и retry (не до бесконечности)
- Каждый эндпоинт возвращает dataclass с ожидаемыми полями на примере реального JSON

**Парсеры (самое важное, здесь обычно баги):**
- Fitbit sleep: тип `stages` (deep/light/rem/wake) — проверяем разбивку минут, гипнограмму, shortData-интервалы с флагом is_short
- Fitbit sleep: тип `classic` (asleep/restless/awake) — другой формат, парсер должен обработать
- Fitbit sleep: несколько sleep sessions за день (ночь + nap) — обе должны сохраниться
- Fitbit sleep: пустой ответ (нет данных за день) — не должен падать
- CPAP EDF: парсинг summary каналов, выделение событий апноэ по типам из event-каналов
- CPAP EDF: битый файл / обрыв посреди ночи — graceful degradation
- O2Ring CSV: корректное вычисление avg/min SpO2, количества десатураций (≥3% на ≥10 сек)
- O2Ring бинарник: 40-байтный заголовок + 5-байтные записи little-endian (формат OSCAR)

**formatter.py** — критично из-за Telegram MarkdownV2:
- Экранирование всех спецсимволов: `_*[]()~`>#+-=|{}.!`
- Точка в числах (87.2 кг) — классический источник багов
- Секции с отсутствующими данными корректно пропускаются
- Длинное сообщение не превышает лимит Telegram (4096 символов)
- Snapshot-тесты: сохранённый ожидаемый вывод на фикстуре данных за день

**db.py и migrations.py:**
- Все миграции применяются на пустой БД, schema_version обновляется
- Повторное применение миграций идемпотентно
- Миграция с версии N до N+2 проходит через N+1
- `is_date_synced` корректно определяет ok/partial/error статусы
- Batch insert sleep_stages за ночь (~900 строк) в одной транзакции

**collector.py** — интеграционные тесты:
- Полный день: fetch → raw_store → parse → db, данные появляются в нужных таблицах
- Частичный fail: один эндпоинт 500, остальные 200 → sync_log=partial, данные остальных сохранены
- Идемпотентность: повторный `collect_day(date)` не создаёт дубли

**backfill.py:**
- Пропуск уже синхронизированных дней (idempotency)
- Rate limiting: паузы между запросами (через freezegun проверяем время между вызовами)
- Возобновляемость: обрыв на середине → перезапуск продолжает с пропущенного дня

**raw_store.py:**
- Запись файла создаёт запись в `raw_files` с правильным путём и размером
- Повторная запись того же `(source, date, kind)` перезаписывает файл и обновляет `fetched_at`
- Чтение несуществующего файла — понятная ошибка

**CLI** — smoke-тесты через `subprocess` или Click testing:
- `hhub status` на пустой БД не падает
- `hhub preview <date>` выводит MarkdownV2-текст
- `hhub parse <source> <date>` на фикстуре даёт ожидаемый результат

**MCP server** — тестировать каждый tool:
- Возвращает ожидаемый JSON на заполненной тестовыми данными БД
- Пустой результат (нет данных за день) обрабатывается корректно
- `get_night_summary` сводит данные из трёх источников за одну ночь

### Что НЕ тестировать

- Реальные API Fitbit/Telegram — только через моки
- pyedflib, bleak, requests — это чужие библиотеки, их не наше дело
- UI Claude Desktop с MCP — интеграция тестируется вручную

### CI

Простой GitHub Actions или локальный `make test`:
```
make test        # pytest tests/unit tests/integration
make test-all    # + e2e
make coverage    # pytest --cov=src --cov-report=html
```

## Порядок реализации

Фазы 1-3 — ядро Fitbit, минимальный рабочий продукт. Фаза 4 — backfill истории Fitbit. Фазы 5-6 — ежедневное использование с Telegram. Фаза 7 — CPAP импорт. Фаза 8 — O2Ring. Фаза 9 — MCP. Фаза 10 — Health Connect ingest (параллельно разрабатывается Android-приложение).

Фазы 7, 8 и 10 независимы друг от друга, можно делать в любом порядке.

## Known Issues & Future Work

### Fitbit Web API Deprecation (сентябрь 2026)

Fitbit Web API (`api.fitbit.com`) будет отключён в сентябре 2026 — Fitbit переходит на Google Health Connect API.

**Что это значит:**
- Все эндпоинты `api.fitbit.com` перестанут работать
- Потребуется миграция на Google Health Connect API: другой OAuth flow, другие эндпоинты, другие форматы ответов

**Что делать:**
- Мониторить [dev.fitbit.com](https://dev.fitbit.com) на объявления о сроках
- **Фаза 10 (Health Connect ingest) закрывает эту проблему** — Android-приложение Health Connect Bridge уже начнёт писать данные в `hc_records` параллельно с Fitbit API
- К сентябрю 2026 накопится история в `hc_records`, Fitbit collector можно отключить без потери данных

**Почему raw data layer защитит историю:**
Двухуровневое хранение полностью защищает исторические данные при смене источника:
1. Новый коллектор (Google Health Connect) пишет данные с определённой даты
2. Все raw Fitbit JSON остаются в `data/raw/fitbit/` — ничего не теряется
3. Структурированные таблицы SQLite пересобираются из нового источника без пробелов в истории
4. `reparse_day()` / backfill из новых raw файлов дополнит SQLite без изменения схемы

## Зависимости

**requirements.txt:**
```
requests>=2.31
python-dotenv>=1.0
tqdm>=4.66
pyedflib>=0.1.34       # парсинг EDF файлов CPAP (фаза 7)
bleak>=0.21            # BLE для O2Ring (фаза 8, опционально)
mcp>=1.0               # MCP-сервер (фаза 9)
fastapi>=0.100         # ingest HTTP server (фаза 10)
uvicorn>=0.23          # ASGI runner для FastAPI
```

**requirements-dev.txt:**
```
pytest>=8.0
pytest-mock>=3.12
pytest-cov>=4.1
responses>=0.25        # мокинг HTTP запросов
freezegun>=1.4         # фиксация времени в тестах
```
