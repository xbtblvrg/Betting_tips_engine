"""Bounded generated caches. Decisions, model versions and user ledger are durable."""
from pathlib import Path
import json,time
from .providers import DATA

def maintain(root=DATA):
    root=Path(root);raw=root/'raw';now=time.time();kept=set();removed=0
    for meta in raw.glob('*.json'):
        try:kept.add(json.loads(meta.read_text())['blob'])
        except (ValueError,KeyError,OSError):continue
    blobs=[(p,p.stat()) for p in raw.glob('*.data')];total=sum(s.st_size for _,s in blobs)
    # Retain current URL snapshots, prune generated historical versions by age/cap.
    # The short grace period protects a response whose pointer is being committed.
    for path,stat in sorted(blobs,key=lambda x:x[1].st_mtime):
        if path.name in kept or now-stat.st_mtime<300:continue
        if now-stat.st_mtime>7*86400 or total>1024**3:
            path.unlink(missing_ok=True);total-=stat.st_size;removed+=1
    quotes=[(p,p.stat()) for p in (root/'quotes').glob('*.json')];qtotal=sum(s.st_size for _,s in quotes)
    for path,stat in sorted(quotes,key=lambda x:x[1].st_mtime):
        if now-stat.st_mtime<300:continue
        if now-stat.st_mtime>30*86400 or qtotal>256*1024**2:
            path.unlink(missing_ok=True);qtotal-=stat.st_size;removed+=1
    return {'raw_bytes':total,'quote_bytes':qtotal,'cache_files_removed':removed,'raw_history_max_bytes':1024**3,'raw_history_retention_days':7,'quote_max_bytes':256*1024**2,'quote_retention_days':30}
