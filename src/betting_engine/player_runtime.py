"""Durable player evidence jobs; parsing is never recommendation approval."""
from collections import Counter, defaultdict
import gzip
import hashlib
from io import BytesIO
import json
from pathlib import Path
import re

from .domain import utcnow
from .io_utils import atomic_bytes, atomic_json


def digest(body):
    return hashlib.sha256(body).hexdigest()


def refresh_history(data):
    from .player_archive_sources import refresh_verified_player_history
    year=utcnow().year
    # Winter-sport season labels advance in autumn. Source/game finality and
    # observation times still exclude future results; do not lose that season.
    seasons=list(range(year-2,year+2))
    result=refresh_verified_player_history(data,Path(data)/'players/verified_history',
        seasons=seasons)
    atomic_json(Path(data)/'players/verified_history/status.json',
        {**result,'checked_at':result['created_at'],'seasons':seasons,
         'season_policy':'Two prior calendar years, current year, and next winter-sport season if final source records exist.'})
    return result


def prepare_player_sources(root,workspace):
    """Bounded public source bytes and one verified snapshot, never all runs."""
    from .player_archive_sources import export_verified_player_sources
    root=Path(root);workspace=Path(workspace);history=root/'data/players/verified_history'
    target=workspace/'data/players/source_evidence'
    if not (history/'latest.json').exists():
        result={'state':'not_supplied','reason':'Verified player history job has not completed.',
                'production_props_enabled':False}
        atomic_json(target/'manifest.json',result);return result
    try:
        result=export_verified_player_sources(root/'data',history,target,
            max_total_bytes=384*1024*1024,max_sources=256)
        run_id=result['history_run_id'];source=history/'runs'/run_id
        manifest_body=(source/'manifest.json').read_bytes();manifest=json.loads(manifest_body)
        if manifest['run_id']!=run_id:raise ValueError('History export identity changed')
        dest=workspace/'data/players/verified_history';folder=dest/'runs'/run_id
        for name,receipt in manifest['files'].items():
            if name not in ('observations.parquet','sources.json','game-identities.json','quarantine.json'):
                raise ValueError('Unexpected player evidence file')
            path=source/name
            if path.is_symlink():raise ValueError('Player evidence symlink')
            body=path.read_bytes()
            if len(body)!=receipt['bytes'] or digest(body)!=receipt['sha256']:
                raise ValueError('Player evidence changed after source export')
            atomic_bytes(folder/name,body)
        atomic_bytes(folder/'manifest.json',manifest_body)
        atomic_json(dest/'latest.json',{'run_id':run_id,'manifest_sha256':digest(manifest_body)})
        return result
    except (OSError,ValueError,KeyError,TypeError) as exc:
        # Keep other tasks usable while making rejected/missing source evidence explicit.
        result={'state':'rejected','reason':str(exc)[:300],'production_props_enabled':False}
        atomic_json(workspace/'data/players/source-evidence-error.json',result);return result


def _body(data,meta):
    relative=meta.get('archive','')
    if not re.fullmatch(r'stake_api_archive/[0-9a-f]{64}\.json\.gz',relative):
        raise ValueError('Invalid Stake source archive path')
    source=data/relative
    if source.is_symlink() or source.parent.is_symlink():raise ValueError('Unsafe Stake archive')
    if source.stat().st_size>16*1024*1024:raise ValueError('Oversized Stake source')
    with gzip.GzipFile(fileobj=BytesIO(source.read_bytes())) as stream:
        body=stream.read(64*1024*1024+1)
    if len(body)>64*1024*1024 or digest(body)!=meta['sha256']:
        raise ValueError('Stake source length/hash mismatch')
    if meta.get('bytes') is not None and len(body)!=meta['bytes']:
        raise ValueError('Stake source recorded length mismatch')
    return body


def audit_prop_catalog(data,*,catalog_body=None):
    """Interpret both full endpoint bodies; retain unresolved identity and scope."""
    from .player_prop_contracts import normalize_player_props
    data=Path(data);catalog_body=catalog_body if catalog_body is not None else (data/'market_catalog/latest.json').read_bytes();catalog=json.loads(catalog_body)
    relative=catalog.get('endpoint_archive','')
    if not re.fullmatch(r'market_catalog/versions/[A-Za-z0-9]+/endpoints\.json',relative):
        raise ValueError('Invalid endpoint catalogue path')
    path=data/relative
    if path.is_symlink() or not path.resolve().is_relative_to((data/'market_catalog').resolve()):
        raise ValueError('Unsafe endpoint catalogue')
    endpoint_body=path.read_bytes();endpoints=json.loads(endpoint_body)
    reports=[];errors=[];counts=defaultdict(lambda: {'fixtures':set(),'contracts':{},'source_responses':0})
    for fixture in endpoints['fixtures']:
        league=fixture['league'];record=counts[league];record['fixtures'].add(fixture['fixture_id'])
        for issue in fixture.get('errors',[]):
            if issue.get('reason')!='stake_fixture_not_active':
                errors.append({'league':league,'fixture_id':fixture['fixture_id'],'source_issue':issue})
        if not fixture.get('identity_verified'):
            errors.append({'league':league,'fixture_id':fixture['fixture_id'],'reason':'Unverified fixture identity'});continue
        responses=fixture.get('responses',{})
        for missing in {'fixtures','odds'}-responses.keys():
            errors.append({'league':league,'fixture_id':fixture['fixture_id'],'endpoint':missing,'reason':'Missing full endpoint response'})
        for endpoint,meta in responses.items():
            try:
                body=_body(data,meta);payload=json.loads(body)
                evidence={**meta,'league':league,'fixture_id':fixture['fixture_id'],
                    'fixture_slug':payload.get('fixture',{}).get('slug')}
                report=normalize_player_props(body,evidence,league=league)
                reports.append({'endpoint':endpoint,**report});record['source_responses']+=1
                for issue in report['issues']:
                    errors.append({'league':league,'fixture_id':fixture['fixture_id'],'endpoint':endpoint,'parser_issue':issue})
                for contract in report['contracts']:
                    # Distinct contracts are not multiplied by tabs or two responses.
                    states=record['contracts'].setdefault(contract['contract_id'],set())
                    states.add(contract['normalization'])
            except (OSError,ValueError,KeyError,TypeError) as exc:
                errors.append({'league':league,'fixture_id':fixture['fixture_id'],'endpoint':endpoint,'reason':str(exc)[:300]})
    errors.extend({'catalogue_issue':issue} for issue in catalog.get('issues',[]))
    summary={'checked_at':utcnow().isoformat(),'catalogue_at':catalog['checked_at'],
        'catalogue_sha256':digest(catalog_body),'endpoints_sha256':digest(endpoint_body),
        'state':'partial' if errors else 'audited','errors':errors,'production_props_enabled':False,
        'claim':'Archived feed interpretation; not current executable quotes, model approval or bet instructions.',
        'leagues':{league:{'fixtures':len(row['fixtures']),'source_responses':row['source_responses'],
            'unique_observed_contracts':len(row['contracts']),
            'normalized_contracts':sum(states=={'normalized'} for states in row['contracts'].values()),
            'unresolved_contracts':sum(states!={'normalized'} for states in row['contracts'].values())}
            for league,row in sorted(counts.items())}}
    run_id=utcnow().strftime('%Y%m%dT%H%M%S%fZ')
    relative=Path('player_props/versions')/run_id
    atomic_json(data/relative/'contracts.json',{'summary':summary,'responses':reports})
    summary['contracts_archive']=str(relative/'contracts.json')
    atomic_json(data/'player_props/latest.json',summary)
    return summary
