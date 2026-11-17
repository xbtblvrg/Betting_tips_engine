"""Bounded generated caches. Decisions, model versions and user ledger are durable."""
from pathlib import Path
from contextlib import closing
import json, time, shutil, sqlite3, os
try:
    from .providers import DATA
except ImportError:
    try:
        from betting_engine.providers import DATA
    except ImportError:
        DATA = Path(__file__).resolve().parent.parent.parent / "data"

def maintain(root=DATA):
    root = Path(root)
    raw = root / 'raw'
    now = time.time()
    kept = set()
    removed = 0

    # 1. Prune raw cache
    if raw.is_dir():
        import os
        kept = set()
        blobs = []
        total = 0
        for entry in os.scandir(raw):
            if entry.name.endswith('.json'):
                kept.add(entry.name[:-5] + '.data')
            elif entry.name.endswith('.data'):
                try:
                    st = entry.stat()
                    blobs.append((Path(entry.path), st))
                    total += st.st_size
                except OSError:
                    pass
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

    # 4. Automated cleanup of old development workspaces older than 2 hours
    workspaces = root / 'development' / 'workspaces'
    workspaces_removed = 0
    if workspaces.is_dir():
        for item in workspaces.iterdir():
            if item.is_dir() and (now - item.stat().st_mtime > 7200):
                try:
                    shutil.rmtree(item)
                    workspaces_removed += 1
                    if workspaces_removed >= 200:
                        break
                except Exception:
                    pass

    # 4b. Automated cleanup of excessive engineering evidence older than 48 hours
    eng_evidence = root / 'development' / 'engineering-evidence'
    if eng_evidence.is_dir():
        ev_count = 0
        try:
            for item in os.scandir(eng_evidence):
                ev_count += 1
                if ev_count > 50000 or (item.name.endswith('.json') and (now - item.stat().st_mtime > 172800)):
                    try:
                        os.unlink(item.path)
                    except OSError:
                        pass
        except Exception:
            pass

    # 4c. Automated cleanup of outcome feedback inputs/observations older than 48 hours
    for of_dir_name in ('inputs', 'observations'):
        of_dir = root / 'outcome_feedback' / of_dir_name
        if of_dir.is_dir():
            try:
                for item in os.scandir(of_dir):
                    if now - item.stat().st_mtime > 172800:
                        try:
                            os.unlink(item.path)
                        except OSError:
                            pass
            except Exception:
                pass

    # 4d. Automated cleanup of market catalog versions (retain latest 5)
    cat_versions = root / 'market_catalog' / 'versions'
    if cat_versions.is_dir():
        try:
            v_dirs = [d for d in cat_versions.iterdir() if d.is_dir()]
            if len(v_dirs) > 5:
                v_dirs.sort(key=lambda d: d.stat().st_mtime)
                for old_v in v_dirs[:-5]:
                    shutil.rmtree(old_v, ignore_errors=True)
        except Exception:
            pass

    # 4e. Automated cleanup of stake_api_archive older than 7 days (or when > 50,000 files)
    stake_archive = root / 'stake_api_archive'
    if stake_archive.is_dir():
        try:
            arch_count = 0
            for item in os.scandir(stake_archive):
                arch_count += 1
                if arch_count > 50000 or (item.name.endswith('.json.gz') and (now - item.stat().st_mtime > 7 * 86400)):
                    try:
                        os.unlink(item.path)
                    except OSError:
                        pass
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
            with closing(sqlite3.connect(db_path, timeout=10)) as conn:
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
                conn.execute('PRAGMA wal_checkpoint(PASSIVE)')
        except Exception:
            pass

    # 7. Automated log truncation (prevent runaway log files > 50MB)
    logs_dir = root / 'logs'
    if logs_dir.is_dir():
        for log_file in logs_dir.glob('*'):
            if log_file.is_file() and log_file.suffix in ('.log', '.jsonl'):
                try:
                    if log_file.stat().st_size > 50 * 1024 * 1024:
                        chunk = 10 * 1024 * 1024
                        with open(log_file, 'rb') as f:
                            f.seek(-chunk, os.SEEK_END)
                            tail_bytes = f.read()
                        with open(log_file, 'wb') as f:
                            f.write(tail_bytes)
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

if __name__ == '__main__':
    res = maintain()
    print("Maintenance run complete:", res)
