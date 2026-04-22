# Database transaction conventions

`src/db.py` writes to SQLite one of two ways. Pick exactly one per function.

## Pattern A — single-statement write

Use for a single `INSERT ... ON CONFLICT DO UPDATE` that stands alone:

```python
def save_nutrition(self, date: str, ...) -> None:
    self.conn.execute("INSERT INTO daily_nutrition(...) VALUES (?) ON CONFLICT ...", (...))
    self.conn.commit()
```

The `execute` + `commit()` pair is atomic — SQLite auto-opens an implicit
transaction on the `execute` and the `commit()` ends it.

## Pattern B — batch write inside `with self.conn:`

Use when you need to wrap multiple statements (e.g. DELETE + executemany)
in one atomic transaction:

```python
def save_sleep_stages(self, log_id: int, stages: list[dict]) -> None:
    with self.conn:
        self.conn.execute("DELETE FROM sleep_stages WHERE log_id=?", (log_id,))
        self.conn.executemany("INSERT INTO sleep_stages(...) VALUES(...)", rows)
```

The `with self.conn:` context manager commits on successful exit and rolls
back on exception. **Never add an explicit `self.conn.commit()` inside this
block** — it's redundant (and easy to misread as a bug fix later).

## Don't mix

Not allowed:

```python
def save_bad(self, ...):
    with self.conn:
        self.conn.execute(...)
        self.conn.commit()   # ← double commit: ends the txn early, the `with`
                             #    exit then has no net effect.
```

A grep-assertion test in `tests/unit/test_db_commit_hygiene.py` keeps this
invariant enforced.

## Why it matters for sync_log crash recovery

`collector.collect_day` issues per-endpoint writes (one transaction per
endpoint) so that a crash mid-run leaves earlier endpoints durable and the
`sync_log` row reflects `partial` — we can retry only the failed endpoints
via `reparse_day()`. Collapsing all 12 endpoints into one outer transaction
would break this guarantee, which is why writers keep commits scoped to the
call site that logically corresponds to one sync step.
