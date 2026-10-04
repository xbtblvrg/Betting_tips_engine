from pathlib import Path
import sys,json
import pymupdf
from PIL import Image,ImageDraw
root=Path(__file__).parent/'online';key=sys.argv[1]
pages=[]
for item in sys.argv[2].split(','):
 a,*b=item.split('-');pages+=list(range(int(a),int(b[0])+1)) if b else [int(a)]
p=root/key/'visual';p.mkdir(exist_ok=True)
doc=pymupdf.open(root/key/'paper.pdf')
for i in pages:doc[i-1].get_pixmap(matrix=pymupdf.Matrix(1.7,1.7)).save(p/f'p{i}.png')
for k in range(0,len(pages),2):
 pair=pages[k:k+2];im=Image.new('RGB',(2200,1500),'white');d=ImageDraw.Draw(im)
 for j,n in enumerate(pair):
  page=Image.open(p/f'p{n}.png').convert('RGB');page.thumbnail((1090,1460));im.paste(page,(j*1100,30));d.text((j*1100+10,5),f'{key} PDF {n}',fill='black')
 out=p/f'sheet-{pair[0]}-{pair[-1]}.jpg';im.save(out,quality=90);print(out)
