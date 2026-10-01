"""Atomic replacement prevents concurrent readers seeing partial artifacts."""
import json,os,tempfile
from pathlib import Path

def atomic_bytes(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def atomic_json(path,data):atomic_bytes(path,json.dumps(data,ensure_ascii=False,allow_nan=False).encode())
