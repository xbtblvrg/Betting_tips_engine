"""Render requested PDF pages for visual reading; does not mark them reviewed."""
from pathlib import Path
import json,sys
import pymupdf
from PIL import Image,ImageDraw
root=Path(__file__).resolve().parents[1]
inv=json.loads((root/'knowledge/reading_v2/inventory.json').read_text())
item=next(x for x in inv if x['id']=='B25')
src=Path('/Users/belavarga/Betting_tips_books')/item['file']
doc=pymupdf.open(src)
pages=[]
for part in sys.argv[1].split(','):
 a,*b=part.split('-');pages.extend(range(int(a),int(b[0])+1) if b else [int(a)])
out=root/'knowledge/reading_v2/visual'
manifest=root/'knowledge/reading_v2/B25-visual-manifest.json'
records=json.loads(manifest.read_text()) if manifest.exists() else []
known={r['page']:r for r in records}
for p in pages:
 dest=out/f'B25-p{p}.png'
 if not dest.exists():doc[p-1].get_pixmap(matrix=pymupdf.Matrix(1.7,1.7)).save(dest)
 if p not in known:records.append({'page':p,'path':str(dest.relative_to(root)),'reviewed':False})
for k in range(0,len(pages),2):
 canvas=Image.new('RGB',(2200,1500),'white');draw=ImageDraw.Draw(canvas)
 for j,p in enumerate(pages[k:k+2]):
  im=Image.open(out/f'B25-p{p}.png').convert('RGB');im.thumbnail((1090,1460))
  canvas.paste(im,(j*1100,30));draw.text((j*1100+10,5),f'B25 PDF {p} / printed {p-25}',fill='black')
 name=f'B25-sheet-{pages[k]}-{pages[min(k+1,len(pages)-1)]}.jpg'
 canvas.save(out/name,quality=90);print(name)
manifest.write_text(json.dumps(sorted(records,key=lambda r:r['page']),indent=2)+'\n')
