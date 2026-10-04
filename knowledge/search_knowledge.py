"""Search the saved critical reading notes locally; never infer reading from extraction."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3

ROOT = Path(__file__).resolve().parent
READING = ROOT / "reading_v2"
DB = ROOT / "knowledge.sqlite3"


def sources():
    return (sorted(READING.glob("*notes.hu.md")) + sorted((ROOT / "cards").glob("*.md"))
            + sorted((ROOT / "online").glob("*notes.hu.md")))


def ledgers():
    return [p for p in (READING / "reading_ledger.jsonl", ROOT / "online/reading_ledger.jsonl") if p.exists()]


def signature(paths):
    digest = hashlib.sha256()
    for path in paths + ledgers():
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def sections(path):
    heading, buffer, start = "Bevezetés", [], 1
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if line.startswith("#"):
            if buffer:
                yield heading, start, "\n".join(buffer)
            heading, buffer, start = line.lstrip("# "), [], number
        else:
            buffer.append(line)
    if buffer:
        yield heading, start, "\n".join(buffer)


def rebuild(connection, paths, fingerprint):
    latest = {}
    for ledger in ledgers():
        for line in ledger.read_text().splitlines():
            entry = json.loads(line)
            key = entry.get("book_id", entry.get("id"))
            if isinstance(key, str):
                latest[key] = entry["status"]
    with connection:
        connection.execute("DROP TABLE IF EXISTS passages")
        connection.execute("CREATE VIRTUAL TABLE passages USING fts5("
                           "source, heading, body, path UNINDEXED, line UNINDEXED, "
                           "status UNINDEXED, tokenize='unicode61 remove_diacritics 2')")
        for path in paths:
            ids = re.findall(r"[BRGEMD]\d{2}", path.name)
            status = "; ".join(f"{key}: {latest.get(key, 'nincs olvasási státusz')}" for key in ids)
            if not ids:
                status = "Tudáskártya; ellenőrzési állapot a szövegben"
            for heading, line, body in sections(path):
                if body.strip():
                    connection.execute("INSERT INTO passages VALUES(?,?,?,?,?,?)",
                                       (", ".join(ids) or path.stem, heading, body,
                                        str(path), line, status))
        connection.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
        connection.execute("INSERT OR REPLACE INTO metadata VALUES('signature',?)", (fingerprint,))


def main():
    parser = argparse.ArgumentParser(description="Helyi keresés a forráskritikai tudástárban")
    parser.add_argument("query", nargs="?", help="Szó vagy több szó; minden szó szerepeljen")
    parser.add_argument("--rebuild", action="store_true", help="Index újraépítése")
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args()
    paths = sources()
    fingerprint = signature(paths)
    with sqlite3.connect(DB) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT)")
        old = connection.execute("SELECT value FROM metadata WHERE key='signature'").fetchone()
        if args.rebuild or not old or old[0] != fingerprint:
            rebuild(connection, paths, fingerprint)
        if not args.query:
            count = connection.execute("SELECT COUNT(*) FROM passages").fetchone()[0]
            print(f"{len(paths)} jegyzet/tudáskártya, {count} kereshető szakasz. {DB}")
            return
        tokens = re.findall(r"\w+", args.query, flags=re.UNICODE)
        if not tokens:
            parser.error("Adj meg legalább egy szót.")
        # Literal word prefixes: no untrusted FTS syntax or interpolated SQL.
        query = " AND ".join('"' + token + '"*' for token in tokens)
        results = connection.execute(
            "SELECT source,heading,path,line,status,snippet(passages,2,'[',']',' … ',45) "
            "FROM passages WHERE passages MATCH ? ORDER BY bm25(passages,2,4,1,0,0,0) LIMIT ?",
            (query, max(1, min(args.limit, 50))),
        ).fetchall()
        if not results:
            print("Nincs találat; próbálj rövidebb szótövet vagy másik kifejezést.")
        for source, heading, path, line, status, excerpt in results:
            print(f"\n{source} — {heading}\n{path}:{line}\nÁllapot: {status}\n{excerpt}")


if __name__ == "__main__":
    main()
