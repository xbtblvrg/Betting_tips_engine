"""Deliver primary research sequentially. Delivery is not a reading assertion."""
from pathlib import Path
import json,sys,re
root=Path(__file__).parent/'online';key=sys.argv[1]
source=(root/key/'full.txt').read_text()
p=root/'delivery_cursor.json';state=json.loads(p.read_text()) if p.exists() else {}
start=int(sys.argv[2]) if len(sys.argv)>2 else state.get(key,0)
pos=start;parts=[];n=0
for line in source[start:].splitlines(keepends=True):
 dense=re.sub(r'[^\S\n]+',' ',line).strip()
 if parts and n+len(dense)>24000:break
 if dense:parts.append(dense);n+=len(dense)+1
 pos+=len(line)
print(f'{key} {start}:{pos}/{len(source)}');print('\n'.join(parts));print(f'END {key} {pos}/{len(source)}')
state[key]=pos;p.write_text(json.dumps(state,indent=2))
