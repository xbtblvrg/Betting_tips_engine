"""Scheduled primary-source abstract discovery, not automatic paper implementation."""
import hashlib,json,re
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request,urlopen
import xml.etree.ElementTree as ET
from .domain import utcnow
from .io_utils import atomic_bytes,atomic_json

API='https://export.arxiv.org/api/query'
NS={'a':'http://www.w3.org/2005/Atom'}
TAGS={'calibration':('calibrat','proper scoring','brier'),
      'sports_forecasting':('sports','basketball','baseball','hockey','football'),
      'player_models':('player','lineup','injur'),
      'ensembles':('ensemble','boosting','mixture'),
      'adaptive_learning':('online learning','concept drift','self-improv','agent'),
      'market_evaluation':('betting','bookmaker','profit','odds')}

LEAGUE_CONTEXT={
    'NBA':r'\bbasketball\b|\bnba\b', 'WNBA':r'\bbasketball\b|\bwnba\b',
    'MLB':r'\bbaseball\b|\bmajor league baseball\b',
    'NHL':r'\bhockey\b|\bnational hockey league\b',
    'NFL':r'\bamerican football\b|\bnational football league\b|\bfootball\b.*\b(touchdown|quarterback|nfl|rushing)\b|\b(touchdown|quarterback|nfl|rushing)\b.*\bfootball\b',
    'MLS':r'\bsoccer\b|\bmajor league soccer\b',
    'NCAAF':r'\bfootball\b', 'NCAAM':r'\bbasketball\b', 'NCAAW':r'\bbasketball\b'}


def topic_relevance(paper,source):
    pattern=LEAGUE_CONTEXT.get(source.get('league'))
    if not pattern:return True
    text=paper['title']+' '+paper['abstract']
    return bool(re.search(pattern,text,re.I))


def parse_feed(raw):
    if len(raw)>2_000_000 or b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('Research response exceeds limits or contains unsupported XML declarations')
    root=ET.fromstring(raw)
    if root.tag!='{http://www.w3.org/2005/Atom}feed':raise ValueError('Expected arXiv Atom feed')
    papers=[]
    for entry in root.findall('a:entry',NS):
        url=entry.findtext('a:id','',NS)
        match=re.fullmatch(r'https?://arxiv.org/abs/([A-Za-z0-9./-]+)',url)
        if not match:raise ValueError('Invalid arXiv paper identity or API error entry')
        version=match[1];id=re.sub(r'v\d+$','',version)
        title=' '.join(entry.findtext('a:title','',NS).split())
        abstract=' '.join(entry.findtext('a:summary','',NS).split())
        if not title or not abstract:raise ValueError('Paper is missing title or abstract')
        searchable=(title+' '+abstract).lower()
        papers.append({'id':id,'version':version,'url':'https://arxiv.org/abs/'+version,
            'title':title,'abstract':abstract,'published':entry.findtext('a:published','',NS),
            'updated':entry.findtext('a:updated','',NS),
            'tags':[tag for tag,words in TAGS.items() if any(w in searchable for w in words)],
            'content_sha256':hashlib.sha256((title+'\n'+abstract).encode()).hexdigest(),
            'reading_scope':'title_and_abstract_only','implementation_state':'not_implemented',
            'fetched_at':utcnow().isoformat()})
    return papers


_LAST_CALL_TS = 0.0


def fetch_feed(query):
    global _LAST_CALL_TS
    import time
    import urllib.error
    url=API+'?'+urlencode({'search_query':query,'start':0,'max_results':12,
                         'sortBy':'submittedDate','sortOrder':'descending'})
    max_retries = 3
    for attempt in range(max_retries):
        elapsed = time.time() - _LAST_CALL_TS
        if elapsed < 3.2:
            time.sleep(3.2 - elapsed)
        _LAST_CALL_TS = time.time()
        try:
            req = Request(url, headers={'User-Agent': 'BettingQuantResearch/1.0 (mailto:research@quant.local)'})
            with urlopen(req, timeout=30) as r:
                raw = r.read(2_000_001)
            return raw, url
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries - 1:
                time.sleep(5.0 * (attempt + 1))
                continue
            raise
        except (TimeoutError, OSError) as e:
            if attempt < max_retries - 1:
                time.sleep(3.0 * (attempt + 1))
                continue
            raise



def discover(source,data_dir,fetch=fetch_feed):
    data=Path(data_dir);folder=data/'research';folder.mkdir(parents=True,exist_ok=True)
    raw,url=fetch(source['query']);received=parse_feed(raw)
    papers=[p for p in received if topic_relevance(p,source)]
    rejected=[{'id':p['id'],'title':p['title'],'url':p['url'],'content_sha256':p['content_sha256'],
               'source':source['id'],'reason':'League acronym without required sport context'}
              for p in received if not topic_relevance(p,source)]
    digest=hashlib.sha256(raw).hexdigest();atomic_bytes(folder/'raw'/(digest+'.xml'),raw)
    path=folder/'catalog.json'
    catalog=json.loads(path.read_text()) if path.exists() else {'papers':[],'sources':{}}
    indexed={p['id']:p for p in catalog['papers']};changed=[]
    # Retain raw responses and explicit exclusions; an acronym collision must
    # not keep being presented as league research after the filter is fixed.
    for identity,old in list(indexed.items()):
        if source['id'] in old.get('discovery_topics',[]) and not topic_relevance(old,source):
            old['discovery_topics']=[t for t in old['discovery_topics'] if t!=source['id']]
            if not old['discovery_topics']:indexed.pop(identity)
            rejected.append({'id':identity,'title':old['title'],'url':old['url'],'content_sha256':old['content_sha256'],
                'source':source['id'],'reason':'Historical league acronym collision; raw source retained'})
    for paper in papers:
        old=indexed.get(paper['id'])
        if not old or old['content_sha256']!=paper['content_sha256']:changed.append(paper['id'])
        paper['discovery_topics']=sorted(set((old or {}).get('discovery_topics',[])+[source['id']]))
        indexed[paper['id']]=paper
    stamp=utcnow().isoformat()
    catalog['sources'][source['id']]={'checked_at':stamp,'url':url,'query':source['query'],
                                    'league':source.get('league'),'result_count':len(papers),'received_count':len(received),
                                    'excluded_count':len(received)-len(papers),'response_sha256':digest}
    excluded={(p['source'],p['id']):p for p in catalog.get('excluded_papers',[])}
    excluded.update({(p['source'],p['id']):p for p in rejected})
    catalog['excluded_papers']=list(excluded.values())
    catalog.update(checked_at=stamp,papers=sorted(indexed.values(),key=lambda p:p['updated'],reverse=True),
                   automatic_implementation=False,reading_scope='Titles and abstracts only; methods require full-text review and validation.')
    atomic_json(path,catalog)
    return {'source':source['id'],'checked_at':stamp,'discovered':len(papers),'new_or_changed':len(changed),
            'changed_paper_ids':changed,'source_url':url,'response_sha256':digest,
            'conclusion':f'Indexed {len(papers)} paper abstracts; {len(changed)} new or changed. Full-text review and implementation remain separate.',
            'automatic_implementation':False,'profit_established':False}
