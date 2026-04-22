"""
P2.5: guard against double-commit anti-pattern in src/db.py.

A `with self.conn:` block is already transactional; adding an explicit
self.conn.commit() inside it commits early and makes the context-manager's
commit/rollback semantics misleading. See docs/db-conventions.md.
"""

from pathlib import Path


def test_no_commit_inside_with_conn_block():
    src = Path(__file__).parent.parent.parent / "src" / "db.py"
    lines = src.read_text(encoding="utf-8").splitlines()

    offenders: list[tuple[int, str]] = []
    in_with_block = False
    with_indent = -1

    for i, line in enumerate(lines, start=1):
        stripped = line.rstrip()
        if not stripped:
            continue
        indent = len(line) - len(line.lstrip())

        # Exit the block when we return to the `with` line's indent or less
        if in_with_block and indent <= with_indent and stripped.strip() != "":
            in_with_block = False
            with_indent = -1

        if "with self.conn:" in stripped:
            in_with_block = True
            with_indent = indent
            continue

        if in_with_block and "self.conn.commit()" in stripped:
            offenders.append((i, stripped))

    assert not offenders, (
        "Found self.conn.commit() inside a `with self.conn:` block in src/db.py.\n"
        "This is a double-commit — the `with` block already commits on success.\n"
        "See docs/db-conventions.md for the two allowed patterns.\n"
        "Offenders:\n" + "\n".join(f"  line {ln}: {src}" for ln, src in offenders)
    )


def test_db_conventions_doc_exists():
    doc = Path(__file__).parent.parent.parent / "docs" / "db-conventions.md"
    assert doc.exists(), "docs/db-conventions.md missing — see P2.5"
    text = doc.read_text(encoding="utf-8")
    assert "Pattern A" in text and "Pattern B" in text
    assert "Never add an explicit" in text or "never" in text.lower()
