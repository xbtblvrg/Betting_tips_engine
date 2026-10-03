#!/usr/bin/env python3
"""Compress unused historical backup/test databases; never select live databases.

Keeps a byte-exact SHA256-verified gzip archive before removing the plain copy.
Restoration: gzip -dk PATH.sqlite3.gz (into a separate offline directory).
"""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time

ROOTS=('data/backups','data/release_checks','data/audit_20260929')

def signature(path):
    s=path.lstat()
    return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)

def unused(path):
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
        raise ValueError('Requires a single-link regular file')
    if any(p.is_symlink() for p in path.parents):raise ValueError('Symlink parent')
    for suffix in ('-wal','-shm','-journal'):
        side=path.with_name(path.name+suffix)
        if side.exists() or side.is_symlink():raise ValueError('SQLite sidecar exists')
    result=subprocess.run(['/usr/sbin/lsof','-t','--',str(path)],capture_output=True,text=True)
    if result.returncode!=1 or result.stdout.strip() or result.stderr.strip():
        raise ValueError('File open or lsof check unavailable')

def compress(path):
    unused(path)
    stamp=signature(path)
    archive=path.with_name(path.name+'.gz')
    if archive.exists() or archive.is_symlink():raise ValueError('Archive already exists')
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'-',dir=path.parent)
    temporary=Path(name);sha=hashlib.sha256();size=0
    try:
        with os.fdopen(fd,'wb') as out:
            with gzip.GzipFile(fileobj=out,mode='wb',filename='',mtime=0,compresslevel=1) as zipped:
                with path.open('rb') as source:
                    while part:=source.read(4*1024**2):
                        sha.update(part);size+=len(part);zipped.write(part)
            out.flush();os.fsync(out.fileno())
        with gzip.open(temporary,'rb') as restored:
            verified=hashlib.file_digest(restored,'sha256').hexdigest()
        if verified!=sha.hexdigest() or signature(path)!=stamp:raise ValueError('Archive/source verification failed')
        # link publishes exclusively; existing archive can never be overwritten.
        shutil.copystat(path,temporary)
        os.link(temporary,archive);temporary.unlink()
        folder=os.open(path.parent,os.O_RDONLY)
        try:
            os.fsync(folder)
            unused(path)
            if signature(path)!=stamp:raise ValueError('Source changed before removal')
            path.unlink();os.fsync(folder)
        finally:os.close(folder)
        return {'source':str(path),'archive':str(archive),'source_bytes':size,
                'archive_bytes':archive.stat().st_size,'sha256':verified,'verified':True}
    finally:temporary.unlink(missing_ok=True)

def candidates(root,now=None):
    root=Path(root).resolve();now=time.time() if now is None else now
    for name in ROOTS:
        base=root/name
        if not base.exists():continue
        if any(p.is_symlink() for p in (base,*base.parents)):raise ValueError('Symlink directory')
        for folder,dirs,files in os.walk(base,followlinks=False):
            dirs[:]=[d for d in dirs if not (Path(folder)/d).is_symlink()]
            for name in sorted(files):
                path=Path(folder)/name;info=path.lstat()
                if (path.suffix=='.sqlite3' and stat.S_ISREG(info.st_mode) and info.st_nlink==1
                    and info.st_size>=100*1024**2 and now-info.st_mtime>=48*3600):yield path

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1]);parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    for path in candidates(args.root):
        print(json.dumps(compress(path) if args.apply else {'candidate':str(path),'bytes':path.stat().st_size}),flush=True)
