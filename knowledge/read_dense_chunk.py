"""Sequential full-source delivery with PDF layout whitespace collapsed only.

Offsets remain in the unmodified full.txt. Delivery is not a reading claim.
"""
import json
import re
import sys
from pathlib import Path

root = Path(__file__).parent / 'reading_v2'
book = sys.argv[1]
source = (root / book / 'full.txt').read_text()
state = root / 'delivery_cursor.json'
cursors = json.loads(state.read_text()) if state.exists() else {}
start = int(sys.argv[2]) if len(sys.argv)>2 else cursors.get(book,0)
pos = start
parts = []
length = 0
for line in source[start:].splitlines(keepends=True):
    dense = re.sub(r'[^\S\n]+', ' ', line).strip()
    if parts and length+len(dense)>27000:
        break
    if dense:
        parts.append(dense)
        length += len(dense)+1
    pos += len(line)
print(f'{book} original characters {start}:{pos}/{len(source)}; only layout whitespace collapsed')
print('\n'.join(parts))
cursors[book] = pos
state.write_text(json.dumps(cursors,indent=2))
print(f'END {book} {pos}/{len(source)}')
