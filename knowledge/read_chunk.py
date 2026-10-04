"""Emit consecutive bounded source chunks; delivery is not a read-status claim."""
import json
from pathlib import Path
import sys

root = Path(__file__).parent / 'reading_v2'
book = sys.argv[1]
path = root / book / 'full.txt'
text = path.read_text()
state = root / 'delivery_cursor.json'
cursors = json.loads(state.read_text()) if state.exists() else {}
start = int(sys.argv[2]) if len(sys.argv) > 2 else cursors.get(book, 0)
end = min(start + 27000, len(text))
if end < len(text):
    cut = text.rfind('\n', start, end)
    if cut > start:
        end = cut + 1
print(f'{book} characters {start}:{end}/{len(text)}; sequential source text follows')
print(text[start:end])
cursors[book] = end
state.write_text(json.dumps(cursors, indent=2))
print(f'END {book} {end}/{len(text)}')
