"""Cache versioned primary research for local reading; download is not reading."""
from pathlib import Path
import urllib.request,json,hashlib,datetime
from concurrent.futures import ThreadPoolExecutor
import pymupdf
from bs4 import BeautifulSoup
root=Path(__file__).parent/'online'
root.mkdir(exist_ok=True)
items=[('R01','2303.06021v4'),('R02','2406.16171v5'),('R03','2511.02815v1'),('R04','2511.07703v2'),('R05','2601.23193v1'),('R06','2512.08591v1')]
def fetch(item):
 key,version=item;p=root/key;p.mkdir(exist_ok=True)
 meta={'id':key,'version':version,'retrieved_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'downloaded_not_read'}
 try:
  for name,url in [('abstract.html',f'https://arxiv.org/abs/{version}'),('paper.pdf',f'https://arxiv.org/pdf/{version}')]:
   path=p/name
   if not path.exists():
    req=urllib.request.Request(url,headers={'User-Agent':'SportsResearch/1.0 (personal research)'})
    with urllib.request.urlopen(req,timeout=40) as r:path.write_bytes(r.read())
  soup=BeautifulSoup((p/'abstract.html').read_text(),'html.parser')
  meta['license_urls']=[a.get('href') for a in soup.select('a') if 'license' in a.get_text().lower()]
  meta['url']=f'https://arxiv.org/abs/{version}'
  doc=pymupdf.open(p/'paper.pdf');meta['pages']=len(doc)
  text='\n'.join(f'\n===== {key} PDF page {i+1} =====\n'+page.get_text(sort=True) for i,page in enumerate(doc))
  (p/'full.txt').write_text(text);meta['characters']=len(text);meta['sha256']=hashlib.sha256((p/'paper.pdf').read_bytes()).hexdigest()
 except Exception as e:meta['error']=repr(e)
 (p/'metadata.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n')
 return meta
with ThreadPoolExecutor(max_workers=3) as pool:
 for m in pool.map(fetch,items):print(json.dumps(m,ensure_ascii=False))
