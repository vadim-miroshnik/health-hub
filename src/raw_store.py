"""
Файловое raw data lake + индекс в таблице raw_files.

Хранит оригинальные ответы API и бинарные файлы в data/raw/{source}/{date}/.
Регистрирует каждый файл в raw_files для быстрого поиска через SQLite.

Использование:
    store = RawStore(conn, Path("data/raw"))
    path = store.save_raw("fitbit", "2026-04-15", "sleep", json_bytes)
    path = store.get_raw("fitbit", "2026-04-15", "sleep")
    rows = store.list_raw("fitbit", start_date="2026-04-01")
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# kind (или суффикс kind) → расширение файла
_EXT_MAP: dict[str, str] = {
    "edf": ".edf",
    "bin": ".bin",
    "csv": ".csv",
}
_DEFAULT_EXT = ".json"


def _ext_for(kind: str) -> str:
    # Разрешаем составные kind вида "edf_pressure" → ищем по последнему сегменту
    suffix = kind.split("_")[-1]
    return _EXT_MAP.get(suffix, _DEFAULT_EXT)


class RawStore:
    """
    Сохраняет и читает raw-файлы из файловой системы,
    синхронизируя метаданные с таблицей raw_files в SQLite.

    Params:
        conn     — соединение SQLite (уже с применёнными миграциями)
        base_dir — корень файлового хранилища (например, data/raw)
    """

    def __init__(self, conn: sqlite3.Connection, base_dir: Path) -> None:
        self._conn = conn
        self._base = base_dir

    # ------------------------------------------------------------------
    # Запись
    # ------------------------------------------------------------------

    def save_raw(
        self,
        source: str,
        date: str,
        kind: str,
        content: bytes | str,
    ) -> Path:
        """
        Сохраняет файл в {base_dir}/{source}/{date}/{kind}{ext}
        и регистрирует запись в raw_files.

        Повторный вызов с тем же (source, date, kind) перезаписывает файл
        и обновляет fetched_at / size_bytes.

        Возвращает абсолютный путь к файлу.
        """
        target_dir = self._base / source / date
        target_dir.mkdir(parents=True, exist_ok=True)

        path = target_dir / f"{kind}{_ext_for(kind)}"

        if isinstance(content, str):
            path.write_text(content, encoding="utf-8")
        else:
            path.write_bytes(content)

        size = path.stat().st_size
        now = datetime.now(timezone.utc).isoformat()
        # filepath хранится относительно родителя base_dir (т.е. relative to data/)
        rel_path = str(path.relative_to(self._base.parent))

        self._conn.execute(
            """
            INSERT INTO raw_files(source, date, kind, filepath, fetched_at, size_bytes)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, date, kind) DO UPDATE SET
                filepath   = excluded.filepath,
                fetched_at = excluded.fetched_at,
                size_bytes = excluded.size_bytes
            """,
            (source, date, kind, rel_path, now, size),
        )
        self._conn.commit()
        return path

    # ------------------------------------------------------------------
    # Чтение
    # ------------------------------------------------------------------

    def get_raw(self, source: str, date: str, kind: str) -> Path:
        """
        Возвращает абсолютный путь к raw-файлу.
        Выбрасывает FileNotFoundError если файл не зарегистрирован или отсутствует на диске.
        """
        row = self._conn.execute(
            "SELECT filepath FROM raw_files WHERE source=? AND date=? AND kind=?",
            (source, date, kind),
        ).fetchone()

        if row is None:
            raise FileNotFoundError(
                f"Raw file not found: source={source!r} date={date!r} kind={kind!r}"
            )

        abs_path = self._base.parent / row[0]
        if not abs_path.exists():
            raise FileNotFoundError(
                f"Raw file registered but missing on disk: {abs_path}"
            )
        return abs_path

    # ------------------------------------------------------------------
    # Перечисление
    # ------------------------------------------------------------------

    def list_raw(
        self,
        source: str,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> list[dict]:
        """
        Возвращает список raw-файлов для источника, опционально за диапазон дат.

        Каждая запись: {source, date, kind, filepath, fetched_at, size_bytes}
        """
        query = (
            "SELECT source, date, kind, filepath, fetched_at, size_bytes "
            "FROM raw_files WHERE source=?"
        )
        params: list[str] = [source]

        if start_date is not None:
            query += " AND date >= ?"
            params.append(start_date)
        if end_date is not None:
            query += " AND date <= ?"
            params.append(end_date)

        query += " ORDER BY date, kind"
        rows = self._conn.execute(query, params).fetchall()

        return [
            {
                "source": r[0],
                "date": r[1],
                "kind": r[2],
                "filepath": r[3],
                "fetched_at": r[4],
                "size_bytes": r[5],
            }
            for r in rows
        ]
