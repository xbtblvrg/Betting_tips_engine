"""Local book extraction and page-addressable search. Extraction != reading."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=path.parent, encoding="utf-8", delete=False) as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        temp = f.name
    os.replace(temp, path)


def extract(path: Path) -> tuple[list[str], str]:
    if path.suffix.lower() == ".pdf":
        result = subprocess.run(["pdftotext", "-layout", str(path), "-"],
                                capture_output=True, timeout=180, check=True)
        pages = result.stdout.decode("utf-8").split("\f")
        if pages and not pages[-1].strip():
            pages.pop()
        return pages, "pdf_page"
    if path.suffix.lower() in (".txt", ".md"):
        return [path.read_text(encoding="utf-8")], "document"
    raise ValueError("Unsupported format; requires a dedicated extractor")


def ingest(source: Path, output: Path) -> dict:
    source, output = source.resolve(), output.resolve()
    if output == source or source in output.parents:
        raise ValueError("Output must be outside the source library")
    # scandir surfaces macOS permission errors instead of pretending library is empty.
    def walk(folder):
        with os.scandir(folder) as entries:
            for entry in sorted(entries, key=lambda e: e.name):
                if entry.is_symlink() or entry.name.startswith("."):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    yield from walk(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    yield Path(entry.path)

    files = list(walk(source))
    output.mkdir(parents=True, exist_ok=True)
    records = []
    with sqlite3.connect(output / "library.sqlite") as db:
        db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS pages USING fts5(book_id UNINDEXED, locator UNINDEXED, text)")
        for path in files:
            record = {"path": str(path), "name": path.name,
                      "read_status": "not_read", "reviewed_locators": []}
            try:
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                record["sha256"] = digest
                pages, locator_type = extract(path)
                record.update(locator_type=locator_type, units=len(pages),
                              characters=sum(len(page) for page in pages),
                              sparse_units=[i for i, page in enumerate(pages, 1)
                                            if len(page.strip()) < 80],
                              status="extracted_needs_review")
                db.execute("DELETE FROM pages WHERE book_id = ?", (digest,))
                db.executemany("INSERT INTO pages VALUES (?, ?, ?)",
                               [(digest, str(i), page) for i, page in enumerate(pages, 1)])
                extracted = output / "extracted" / digest
                extracted.mkdir(parents=True, exist_ok=True)
                for i, page in enumerate(pages, 1):
                    (extracted / f"{i:05d}.txt").write_text(page, encoding="utf-8")
            except (OSError, ValueError, UnicodeError, subprocess.SubprocessError) as exc:
                record.update(status="extraction_failed", error=str(exc))
            records.append(record)
        active = {r["sha256"] for r in records if r["status"] == "extracted_needs_review"}
        for (book_id,) in db.execute("SELECT DISTINCT book_id FROM pages").fetchall():
            if book_id not in active:
                db.execute("DELETE FROM pages WHERE book_id = ?", (book_id,))
    manifest = {"source": str(source), "generated_at": datetime.now(timezone.utc).isoformat(),
                "note": "Extraction and indexing are not evidence of reading or comprehension.",
                "files": records}
    atomic_json(output / "manifest.json", manifest)
    return manifest


def search(database: Path, query: str, limit: int = 10) -> list[dict]:
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        rows = db.execute("SELECT book_id, locator, snippet(pages, 2, '[', ']', '…', 30) "
                          "FROM pages WHERE pages MATCH ? ORDER BY rank LIMIT ?",
                          (query, limit)).fetchall()
    return [dict(book_id=row[0], locator=row[1], excerpt=row[2]) for row in rows]
