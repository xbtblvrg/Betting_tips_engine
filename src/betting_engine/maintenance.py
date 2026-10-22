"""Bounded generated caches. Decisions, model versions and user ledger are durable."""
from pathlib import Path
import json, time, shutil, sqlite3
from .providers import DATA

def maintain(root=DATA):
    root = Path(root)
    raw = root / 'raw'
    now = time.time()
    kept = set()
    removed = 0

    # 1. Prune raw cache
    if raw.is_dir():
        for meta in raw.glob('*.json'):
            try:
                kept.add(json.loads(meta.read_text())['blob'])
            except (ValueError, KeyError, OSError):
                continue
        blobs = [(p, p.stat()) for p in raw.glob('*.data')]
        total = sum(s.st_size for _, s in blobs)
        for path, stat in sorted(blobs, key=lambda x: x[1].st_mtime):
            if path.name in kept or now - stat.st_mtime < 300:
                continue
            if now - stat.st_mtime > 7 * 86400 or total > 1024**3:
                path.unlink(missing_ok=True)
                total -= stat.st_size
                removed += 1
    else:
        total = 0

    # 2. Prune quotes cache
    quotes_dir = root / 'quotes'
    if quotes_dir.is_dir():
        quotes = [(p, p.stat()) for p in quotes_dir.glob('*.json')]
        qtotal = sum(s.st_size for _, s in quotes)
        for path, stat in sorted(quotes, key=lambda x: x[1].st_mtime):
            if now - stat.st_mtime < 300:
                continue
            if now - stat.st_mtime > 30 * 86400 or qtotal > 256 * 1024**2:
                path.unlink(missing_ok=True)
                qtotal -= stat.st_size
                removed += 1
    else:
        qtotal = 0

    # 3. Automated cleanup of staging releases older than 2 hours
    staging = root / 'releases' / 'staging'
    staging_removed = 0
    if staging.is_dir():
        for item in staging.iterdir():
            if item.is_dir() and (now - item.stat().st_mtime > 7200):
                try:
                    shutil.rmtree(item)
                    staging_removed += 1
                except Exception:
                    pass

    # 4. Automated cleanup of old development workspaces older than 4 hours
    workspaces = root / 'development' / 'workspaces'
    workspaces_removed = 0
    if workspaces.is_dir():
        for item in workspaces.iterdir():
            if item.is_dir() and (now - item.stat().st_mtime > 14400):
                try:
                    shutil.rmtree(item)
                    workspaces_removed += 1
                except Exception:
                    pass

    # 5. Automated cleanup of discard receipts older than 7 days
    for receipt_dir_name in ('release-staging-discard-receipts', 'workspace-input-discard-receipts'):
        rdir = root / receipt_dir_name
        if rdir.is_dir():
            for receipt in rdir.glob('*.json'):
                try:
                    if now - receipt.stat().st_mtime > 7 * 86400:
                        receipt.unlink(missing_ok=True)
                except Exception:
                    pass

    # 6. Automated database retention & compaction
    db_path = root / 'engine.sqlite3'
    if db_path.is_file():
        try:
            with sqlite3.connect(db_path, timeout=10) as conn:
                conn.execute('DELETE FROM publications WHERE id NOT IN (SELECT id FROM publications ORDER BY id DESC LIMIT 150)')
                conn.execute('DELETE FROM outbox WHERE id NOT IN (SELECT id FROM outbox ORDER BY id DESC LIMIT 500)')
                conn.commit()
                try:
                    pages = conn.execute("PRAGMA page_count").fetchone()[0]
                    freelist = conn.execute("PRAGMA freelist_count").fetchone()[0]
                    if pages > 5000 and freelist / pages > 0.30:
                        conn.execute("VACUUM")
                except Exception:
                    pass
                conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        except Exception:
            pass

    return {
        'raw_bytes': total,
        'quote_bytes': qtotal,
        'cache_files_removed': removed,
        'staging_dirs_removed': staging_removed,
        'workspaces_removed': workspaces_removed,
        'raw_history_max_bytes': 1024**3,
        'raw_history_retention_days': 7,
        'quote_max_bytes': 256 * 1024**2,
        'quote_retention_days': 30
    }
