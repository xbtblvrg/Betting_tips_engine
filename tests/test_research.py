import json
import pytest
from betting_engine.research import parse_feed,discover

FEED=b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2609.12345v1</id><title>Sports probability calibration</title><summary>A calibration method for basketball forecasting.</summary><published>2026-09-01T00:00:00Z</published><updated>2026-09-01T00:00:00Z</updated></entry></feed>'''


def test_discovery_indexes_abstracts_without_claiming_full_reading_or_implementation(tmp_path):
    source={'id':'sports','query':'ti:sports'}
    fetch=lambda q:(FEED,'https://export.arxiv.org/api/query?search_query=ti:sports')
    first=discover(source,tmp_path,fetch);second=discover(source,tmp_path,fetch)
    assert first['new_or_changed']==1 and second['new_or_changed']==0
    catalog=json.loads((tmp_path/'research/catalog.json').read_text());p=catalog['papers'][0]
    assert p['reading_scope']=='title_and_abstract_only' and p['implementation_state']=='not_implemented'
    assert p['url']=='https://arxiv.org/abs/2609.12345v1' and p['tags']==['calibration','sports_forecasting']
    assert not catalog['automatic_implementation'] and len(list((tmp_path/'research/raw').glob('*.xml')))==1


def test_revised_paper_retains_identity_and_topics(tmp_path):
    discover({'id':'sports','query':'q'},tmp_path,lambda q:(FEED,'u'))
    revision=FEED.replace(b'v1',b'v2').replace(b'A calibration',b'An improved calibration')
    r=discover({'id':'calibration','query':'q'},tmp_path,lambda q:(revision,'u'))
    papers=json.loads((tmp_path/'research/catalog.json').read_text())['papers']
    assert len(papers)==1 and r['new_or_changed']==1
    assert papers[0]['discovery_topics']==['calibration','sports']


@pytest.mark.parametrize('raw',[b'<!DOCTYPE feed><feed/>',b'not xml',b'<html/>',FEED.replace(b'http://arxiv.org/abs/2609.12345v1',b'javascript:alert(1)')])
def test_untrusted_or_invalid_responses_are_not_successfully_indexed(raw):
    with pytest.raises(Exception):parse_feed(raw)


def test_daily_research_jobs_do_not_duplicate_or_bypass_failed_retries(tmp_path):
    from test_learning import worker
    w=worker(tmp_path);config=w.data.parent/'config';config.mkdir()
    (config/'research_sources.json').write_text(json.dumps({'enabled':True,'sources':[{'id':'sports','query':'q'}]}))
    w.plan_research();w.plan_research()
    jobs=[j for j in w.snapshot()['jobs'] if j['kind']=='research_watch'];assert len(jobs)==1
    with w.store.db() as c:c.execute("UPDATE learning_jobs SET status='retry' WHERE id=?",(jobs[0]['id'],))
    w.plan_research();assert len(w.snapshot()['jobs'])==1


def test_no_free_lunch_is_not_nfl_sports_evidence_and_raw_source_remains(tmp_path):
    feed=FEED.replace(b'Sports probability calibration',b'No Free Lunch NFL theorem').replace(
        b'A calibration method for basketball forecasting.',b'NFL limits general prediction.')
    source={'id':'league-nfl','league':'NFL','query':'q'}
    result=discover(source,tmp_path,lambda q:(feed,'u'))
    catalog=json.loads((tmp_path/'research/catalog.json').read_text())
    assert result['discovered']==0 and catalog['papers']==[]
    assert catalog['sources']['league-nfl']['excluded_count']==1
    assert catalog['excluded_papers'][0]['id']=='2609.12345'
    assert len(list((tmp_path/'research/raw').glob('*.xml')))==1
    legitimate=feed.replace(b'NFL limits general prediction.',b'NFL quarterback prediction in American football.')
    assert discover(source,tmp_path,lambda q:(legitimate,'u'))['discovered']==1
