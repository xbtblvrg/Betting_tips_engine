"""Copy trusted local sport models for reproducible isolated model studies.

Only the project's own deployed artifacts may be loaded here. Never use this
exporter on uploaded/untrusted pickle files. Wager/account data is not read.
"""
import hashlib
from io import BytesIO
import json,re
from pathlib import Path

import joblib

from .domain import LEAGUES, utcnow
from .io_utils import atomic_json

SOURCE_FILES=('models.py','context.py','domain.py','contracts.py','experiments.py','train.py','college.py')
STUDY_FILES=('manifest.json','development.json','result.json','challenger_report.json')
MAX_STUDIES_PER_LEAGUE=32
MAX_EVIDENCE_BYTES=20*1024*1024
MAX_TRAINING_EXPORT_BYTES=256*1024*1024


def _read_evidence(root,path,limit=MAX_EVIDENCE_BYTES):
    """Only bounded regular files under the actual project; no symlink hops."""
    path=Path(path)
    if not path.resolve().is_relative_to(root):raise ValueError('Evidence source leaves the project')
    current=path
    while current!=root:
        if current.is_symlink():raise ValueError('Evidence source uses a symlink')
        current=current.parent
    if not path.is_file() or path.stat().st_size>limit:raise ValueError('Evidence source missing, nonregular or too large')
    with path.open('rb') as source:body=source.read(limit+1)
    if len(body)>limit:raise ValueError('Evidence source grew beyond size bound')
    return body


def _source_files(root,folder,report):
    files={name:_read_evidence(root,folder/name) for name in ('models.py','context.py')}
    expected=report.get('code_sha256')
    if not isinstance(expected,str) or hashlib.sha256(files['models.py']+files['context.py']).hexdigest()!=expected:
        raise ValueError('Archived training code hash does not match its model report')
    for name in SOURCE_FILES:
        if name not in files and (folder/name).exists():files[name]=_read_evidence(root,folder/name)
    return files


def _write_bundle(destination,relative,files):
    folder=destination/relative;folder.mkdir(parents=True,exist_ok=False)
    records=[]
    for name,body in files.items():
        (folder/name).write_bytes(body)
        records.append({'path':str(relative/name),'sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body)})
    return records


def _export_training_inputs(root,destination,reference,league,manifest):
    from .training_evidence import verify_inputs
    relative=reference.get('path','')
    if not re.fullmatch(r'training_inputs/[0-9a-f]{64}',relative):raise ValueError('Unsafe training input reference')
    snapshot=root/'data'/relative
    # Bound reads before replay verification/decompression as well as copying.
    files={name:_read_evidence(root,snapshot/name,128*1024*1024) for name in ('inputs.json.gz','manifest.json')}
    payload=verify_inputs(root/'data',reference)
    if payload['league']!=league or payload['scope']!='full_game':raise ValueError('Training snapshot scope mismatch')
    files.update({name:_read_evidence(root,snapshot/name) for name in payload['sources']})
    target=destination/relative
    records=[]
    if target.exists():
        verify_inputs(destination,reference)
        for name,body in files.items():
            if (target/name).read_bytes()!=body:raise ValueError('Exported snapshot byte conflict')
            records.append({'path':str(Path(relative)/name),'sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body)})
    else:
        size=sum(map(len,files.values()))
        if manifest.get('training_input_bytes',0)+size>MAX_TRAINING_EXPORT_BYTES:raise ValueError('Training snapshot export exceeds total byte bound')
        records=_write_bundle(destination,Path(relative),files)
        manifest['training_input_bytes']=manifest.get('training_input_bytes',0)+size
        verify_inputs(destination,reference)
    return {'status':'verified','reference':reference,'files':records,
        'claim':'Exact captured inputs for this fit; not proof of historical availability.'}


def _export_studies(root,destination,manifest):
    """Allowlisted study evidence only; never recurse into arbitrary run files."""
    sources={}
    for league in LEAGUES:
        folder=root/'data/experiments'/league
        if not folder.exists():continue
        if folder.is_symlink() or not folder.resolve().is_relative_to(root):
            manifest['source_exclusions'].append({'league':league,'kind':'experiments','reason':'Unsafe experiment directory'})
            continue
        trials=sorted((p for p in folder.iterdir() if p.is_dir()),key=lambda p:p.name)
        if len(trials)>MAX_STUDIES_PER_LEAGUE:
            manifest['source_exclusions'].append({'league':league,'kind':'experiments','reason':'Archive count exceeds export bound','omitted':len(trials)-MAX_STUDIES_PER_LEAGUE})
        for trial in trials[:MAX_STUDIES_PER_LEAGUE]:
            try:
                if not re.fullmatch(r'[0-9a-f]{24}',trial.name):raise ValueError('Unexpected experiment directory identity')
                files={name:_read_evidence(root,trial/name) for name in STUDY_FILES}
                metadata=json.loads(files['manifest.json']);result=json.loads(files['result.json']);report=json.loads(files['challenger_report.json'])
                if (metadata.get('league')!=league or result.get('league')!=league or report.get('league')!=league
                        or result.get('study_id')!=trial.name or result.get('training_digest')!=metadata.get('training_digest')
                        or result.get('model_version')!=report.get('version') or not report.get('version')
                        or metadata.get('protocol')!='chronological-recipe-search-v1' or result.get('protocol')!=metadata.get('protocol')):
                    raise ValueError('Experiment manifest/result/report identity mismatch')
                development=json.loads(files['development.json'])
                if not isinstance(development,list) or development!=result.get('development'):
                    raise ValueError('Experiment recipe trial history mismatch')
                files.update(_source_files(root,trial,report))
                relative=Path('experiments')/league/trial.name
                records=_write_bundle(destination,relative,files)
                snapshots={}
                for kind,reference in (('selection',metadata.get('selection_training_inputs')),('challenger',report.get('training_inputs'))):
                    if reference is None:
                        snapshots[kind]={'status':'not_supplied'};continue
                    try:snapshots[kind]=_export_training_inputs(root,destination,reference,league,manifest)
                    except Exception as exc:
                        snapshots[kind]={'status':'rejected','reason':str(exc)[:300]}
                        manifest['source_exclusions'].append({'league':league,'study_id':trial.name,'kind':'training_inputs:'+kind,'reason':str(exc)[:300]})
                manifest['experiments'].append({'league':league,'study_id':trial.name,'model_version':report['version'],
                    'comparison_model':metadata.get('comparison_model'),'training_digest':metadata.get('training_digest'),
                    'training_code_sha256':report['code_sha256'],'files':records,'training_snapshots':snapshots,
                    'scope':'Archived recipe study and code, with separately verified input availability; not an untouched holdout.'})
                sources[(league,report['version'])]={'path':str(relative),'files':records,'training_code_sha256':report['code_sha256'],'source':'verified_recipe_archive'}
            except Exception as exc:
                manifest['source_exclusions'].append({'league':league,'kind':'experiment','study_id':trial.name,'reason':str(exc)[:300]})
    return sources


def _export_model_source(root,destination,row,report,experiment_sources,manifest):
    row['training_snapshot']={'status':'not_supplied','reason':'No verified original training-input snapshot is in this export. Current games.json may contain later corrections or enrichment.'}
    version=report['version']
    try:
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,180}',version):raise ValueError('Unsafe model version path')
        if report.get('training_inputs'):
            reference=report['training_inputs']
            snapshot=root/'data'/reference['path']
            _source_files(root,snapshot,report)
            row['training_snapshot']=_export_training_inputs(root,destination,reference,row['league'],manifest)
            row['training_source']={'status':'verified','path':reference['path'],'files':row['training_snapshot']['files'],
                'source':'immutable_training_input_archive','training_code_sha256':report['code_sha256']}
            return
        folder=root/'data/models/versions'/version
        if not folder.exists():
            matches=[p.parent for p in (root/'data/models/versions').glob(version+'-*/report.json')
                     if json.loads(_read_evidence(root,p))==report]
            if matches:folder=sorted(matches)[-1]
        if folder.exists():
            archived_report=_read_evidence(root,folder/'report.json');archived=json.loads(archived_report)
            if any(archived.get(k)!=report.get(k) for k in ('league','version','code_sha256')):
                raise ValueError('Version archive does not match deployed report identity')
            files=_source_files(root,folder,report);files['report.json']=archived_report
            relative=Path('source_archives')/row['league']/version
            records=_write_bundle(destination,relative,files)
            row['training_source']={'status':'verified','path':str(relative),'files':records,
                'source':'exact_model_version_archive','training_code_sha256':report['code_sha256']}
        elif (row['league'],version) in experiment_sources:
            evidence=experiment_sources[(row['league'],version)]
            if evidence['training_code_sha256']!=report.get('code_sha256'):raise ValueError('Experiment source differs from deployed training code')
            row['training_source']={'status':'verified',**evidence}
        else:row['training_source']={'status':'not_available','reason':'No exact verified model-version or recipe archive was found.'}
    except Exception as exc:
        if report.get('training_inputs'):row['training_snapshot']={'status':'rejected','reason':str(exc)[:300]}
        row['training_source']={'status':'rejected','reason':str(exc)[:300]}
        manifest['source_exclusions'].append({'league':row['league'],'kind':'model_source','version':version,'reason':str(exc)[:300]})


def export_frozen_models(root, destination):
    root = Path(root).resolve()
    destination = Path(destination)
    # Every worker preparation uses a fresh directory. Existing input evidence
    # must never be silently replaced or mixed with another deployment snapshot.
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {'checked_at': utcnow().isoformat(), 'models': [], 'excluded': [],'experiments':[],'source_exclusions':[],
                'scope': 'Frozen local sport models, reports and verified archived training/evaluation code with recipe study evidence; retrospective evidence, not profit or new holdout validation.',
                'training_input_policy':'Current sports history is not the original training snapshot. Do not alter it to force a fingerprint match; preserve unresolved source revisions.',
                'archive_limits':{'studies_per_league':MAX_STUDIES_PER_LEAGUE,'bytes_per_evidence_file':MAX_EVIDENCE_BYTES}}
    experiment_sources=_export_studies(root,destination,manifest)
    for league in LEAGUES:
        artifact = root / 'data/models' / (league + '.joblib')
        sidecar = artifact.with_suffix('.json')
        if not artifact.exists() and not sidecar.exists():
            continue
        try:
            model_bytes, report_bytes = _read_evidence(root,artifact,128*1024*1024),_read_evidence(root,sidecar)
            model, report = joblib.load(BytesIO(model_bytes)), json.loads(report_bytes)
            if (model.report != report or model.league != league or report.get('league') != league
                    or model.version != report.get('version') or not model.version):
                raise ValueError('Frozen model/report identity mismatch')
            model_name, report_name = league + '.joblib', league + '.json'
            (destination / model_name).write_bytes(model_bytes)
            (destination / report_name).write_bytes(report_bytes)
            row={'league': league, 'version': model.version,
                'artifact': model_name, 'report': report_name,
                'artifact_sha256': hashlib.sha256(model_bytes).hexdigest(),
                'report_sha256': hashlib.sha256(report_bytes).hexdigest(),
                'report_matches_frozen_model': True}
            _export_model_source(root,destination,row,report,experiment_sources,manifest)
            manifest['models'].append(row)
        except Exception as exc:
            # One invalid artifact cannot prevent other leagues' research.
            manifest['excluded'].append({'league': league, 'reason': str(exc)[:300]})
    atomic_json(destination / 'manifest.json', manifest)
    return manifest
