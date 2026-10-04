"""Private source extraction for the restarted, books-first reading pass."""
from pathlib import Path
import hashlib
import json
import posixpath
import shutil
import xml.etree.ElementTree as ET
import zipfile
import fitz
import mobi
from bs4 import BeautifulSoup

ROOT = Path('/Users/belavarga/Betting_tips_books')
OUT = Path('/Users/belavarga/Betting_tips_engine/knowledge/reading_v2')
OUT.mkdir(exist_ok=True)


def epub_units(path):
    with zipfile.ZipFile(path) as z:
        container = ET.fromstring(z.read('META-INF/container.xml'))
        package = next(x.attrib['full-path'] for x in container.iter() if x.tag.endswith('rootfile'))
        doc = ET.fromstring(z.read(package))
        manifest = {x.attrib['id']: x.attrib['href'] for x in doc.iter() if x.tag.endswith('}item')}
        title = [x.text for x in doc.iter() if x.tag.endswith('}title')]
        units = []
        for item in doc.iter():
            if not item.tag.endswith('}itemref'):
                continue
            href = manifest[item.attrib['idref']]
            target = posixpath.normpath(posixpath.join(posixpath.dirname(package), href.split('#')[0]))
            soup = BeautifulSoup(z.read(target), 'html.parser')
            for node in soup(['script', 'style']):
                node.decompose()
            text = soup.get_text('\n', strip=True)
            units.append((target, text))
        return units, {'titles': title}


inventory_path = OUT / 'inventory.json'
records = json.loads(inventory_path.read_text()) if inventory_path.exists() else []
seen = {r['sha256']: r['id'] for r in reversed(records)}
known_files = {r['file']: r for r in records}
next_number = max((int(r['id'][1:]) for r in records), default=0) + 1
added = []
for source in sorted(ROOT.iterdir(), key=lambda p:p.name.lower()):
    if not source.is_file() or source.name.startswith('.'):
        continue
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if source.name in known_files:
        if digest != known_files[source.name]['sha256']:
            raise RuntimeError(f'Existing source changed; preserve and review separately: {source.name}')
        continue
    book_id = f'B{next_number:02d}'
    next_number += 1
    record = {'id': book_id, 'file': source.name, 'sha256': digest, 'status': 'extracted_not_read'}
    if digest in seen:
        record['duplicate_of'] = seen[digest]
        records.append(record)
        added.append(record)
        continue
    seen[digest] = book_id
    path = source
    temp = None
    try:
        if source.suffix.lower() in ('.mobi', '.azw3'):
            temp, extracted = mobi.extract(str(source))
            path = Path(extracted)
        if path.suffix.lower() == '.pdf':
            with fitz.open(path) as doc:
                units = [(f'PDF page {i+1}', p.get_text(sort=True)) for i,p in enumerate(doc)]
                meta = {'metadata': doc.metadata, 'toc': doc.get_toc()}
        elif path.suffix.lower() == '.epub':
            units, meta = epub_units(path)
        else:
            soup = BeautifulSoup(path.read_bytes(), 'html.parser')
            units = [('HTML document', soup.get_text('\n', strip=True))]
            meta = {}
        folder = OUT / book_id
        folder.mkdir(exist_ok=True)
        index = []
        full = []
        for i,(locator,text) in enumerate(units,1):
            (folder/f'{i:04d}.txt').write_text(text, encoding='utf-8')
            index.append({'unit':i, 'locator':locator, 'words':len(text.split()), 'chars':len(text)})
            full.append(f'\n\n===== {book_id} UNIT {i}: {locator} =====\n\n{text}')
        (folder/'full.txt').write_text(''.join(full), encoding='utf-8')
        (folder/'index.json').write_text(json.dumps(index, ensure_ascii=False, indent=2))
        record.update(units=len(units), words=sum(x['words'] for x in index),
                      sparse_units=[x['unit'] for x in index if x['chars']<80], **meta)
    except Exception as exc:
        record.update(status='extraction_failed',error=str(exc))
    finally:
        if temp:
            shutil.rmtree(temp)
    records.append(record)
    added.append(record)
inventory_path.write_text(json.dumps(records, ensure_ascii=False, indent=2))
for r in added:
    print(json.dumps({k:v for k,v in r.items() if k not in ('metadata','toc','sha256')}, ensure_ascii=False))
