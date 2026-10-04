"""Weekly game-balanced anchoring with frozen later-issued holdouts.

This learning-worker workflow never changes original decisions or sportsbooks.
The active artifact changes only after a predeclared prospective comparison.
"""
from collections import defaultdict,OrderedDict
from copy import deepcopy
from contextlib import contextmanager
import fcntl
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import math
import statistics
from .contracts import split_market
from .io_utils import atomic_json
from .market_anchor import logit_blend

VERSION='prospective-anchor-weights-v1'
MIN_GAMES=30
_VALIDATED_CACHE=OrderedDict()
MAX_INPUT_BYTES=64*1024*1024
RUNTIME_KEY='_market_weight_snapshot'
SEMANTICS={'three_way':'exclusive_three_way_normalized_v1','double_chance':'derived_double_chance_union_v1','winner_total':'source-bound-normalized-logit-v1','exact_total':'source-bound-normalized-logit-v1'}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def stamp(value):
    if not isinstance(value,str):raise ValueError('timestamp_missing')
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None:raise ValueError('timestamp_timezone_missing')
    return result.astimezone(timezone.utc)


def segment(league,kind,line=None):
    scope,family=split_market(kind)
    # Fractional Asian legs are a different target from a binary final result.
    # Keep their learning identity separate; no binary-label extrapolation.
    if line is not None and abs(float(line)*2-round(float(line)*2))>1e-8:
        family+=':quarter_line'
    return '|'.join((league,scope,family,SEMANTICS.get(family,'conditional_binary_v1')))


def prediction(row,weight):
    partition=row.get('partition')
    if partition:
        values={k:logit_blend(p,partition['market'][k],weight,allow_endpoint_model=True)
                for k,p in partition['model'].items()}
        return math.fsum(values[k] for k in partition.get('selection',[row['side']]))/math.fsum(values.values())
    return logit_blend(row['model_probability'],row['market_probability'],weight,allow_endpoint_model=True)


def logloss(row,weight):
    p=min(1-1e-12,max(1e-12,prediction(row,weight)));y=row['outcome']
    return -y*math.log(p)-(1-y)*math.log1p(-p)


def game_losses(rows,weight):
    groups=defaultdict(list)
    for row in rows:
        if row.get('outcome') in (0,1):groups[row['game_id']].append(logloss(row,weight))
    return {game:statistics.fmean(values) for game,values in groups.items()}


def observations(decisions,as_of):
    """Thin frozen original inputs; never backdate an outcome's availability."""
    rows=[];reasons=defaultdict(int);seen=set()
    for decision in decisions:
        try:
            tip=decision['payload'];anchor=tip['market_anchor'];identifier=decision['tip_id']
            if identifier in seen:raise ValueError('duplicate_original')
            seen.add(identifier)
            if tip.get('decision_guard',{}).get('passed') is not True:raise ValueError('guard_unverified')
            if anchor.get('status')!='usable':raise ValueError('anchor_unusable')
            issued=stamp(decision['issued_at']);trained=stamp(tip['model_trained_at']);start=stamp(tip['start'])
            if not trained<=issued<start or issued>as_of:raise ValueError('nonprospective_model_input')
            if not -5<=(issued-stamp(tip['quote_at'])).total_seconds()<=300:raise ValueError('quote_not_issuance_fresh')
            key=segment(tip['league'],tip['market'],tip.get('line'))
            if ':quarter_line|' in key:raise ValueError('fractional_settlement_target_required')
            p=float(anchor['model_probability']);q=float(anchor['market_probability'])
            if not 0<=p<=1 or not 0<q<1:raise ValueError('invalid_probability')
            row={'tip_id':identifier,'game_id':tip['game_id'],'segment':key,'model_version':tip['model_version'],
                 'issued_at':issued.isoformat(),'start':start.isoformat(),'model_trained_at':trained.isoformat(),
                 'model_probability':p,'market_probability':q,'side':tip['side'],'outcome':None,
                 'resolved':False,'settled_at':None}
            _,base=split_market(tip['market'])
            if base in ('three_way','double_chance'):
                if anchor.get('probability_semantics')!=SEMANTICS[base]:raise ValueError('legacy_incompatible_partition_semantics')
                model=anchor['model_probabilities'];market=anchor['exclusive_probabilities']
                sides={'home_draw':['home','draw'],'away_draw':['away','draw'],'home_away':['home','away']}
                selection=sides[tip['side']] if base=='double_chance' else [tip['side']]
                if set(model)!=set(market) or set(model)!={'home','draw','away'} or any(not 0<=v<=1 for v in model.values()) or any(not 0<v<1 for v in market.values()) or abs(sum(model.values())-1)>1e-8 or abs(sum(market.values())-1)>1e-8:raise ValueError('invalid_partition')
                if abs(math.fsum(model[k] for k in selection)-p)>1e-10 or abs(math.fsum(market[k] for k in selection)-q)>1e-10:raise ValueError('selected_partition_probability_mismatch')
                row['partition']={'model':model,'market':market,'selection':selection}
            if anchor.get('composite_blend'):
                part=anchor['composite_blend'];model=part['model_probabilities'];market=part['market_probabilities']
                if set(model)!=set(market) or tip['side'] not in model or any(not 0<=v<=1 for v in model.values()) or any(not 0<v<1 for v in market.values()) or abs(sum(model.values())-1)>1e-8 or abs(sum(market.values())-1)>1e-8:raise ValueError('invalid_partition')
                row['partition']={'model':model,'market':market}
            if decision.get('status')!='open':
                settled=stamp((decision.get('outcome_evidence') or {})['settled_at'])
                if settled<issued:raise ValueError('outcome_before_issuance')
                if settled<=as_of:
                    row.update(resolved=True,settled_at=settled.isoformat(),outcome=1 if decision['status']=='won' else 0 if decision['status']=='lost' else None)
            rows.append(row)
        except (KeyError,TypeError,ValueError,OverflowError) as exc:reasons[str(exc)[:100]]+=1
    return sorted(rows,key=lambda r:(r['issued_at'],r['tip_id'])),dict(reasons)


def fit_candidate(rows,as_of,baseline_weight=.15,minimum_games=MIN_GAMES):
    rows=[r for r in rows if r.get('outcome') in (0,1) and stamp(r['settled_at'])<=as_of]
    versions={r['model_version'] for r in rows}
    if len(versions)!=1:return None
    games={r['game_id'] for r in rows}
    if len(games)<minimum_games:return None
    from .market_anchor import fit_model_weight
    fitted=fit_model_weight(rows,minimum_games=minimum_games,prediction_fn=prediction)
    if fitted['state']!='candidate':return None
    best=fitted['weight']
    return {'version':VERSION,'state':'collecting_prospective','created_at':as_of.isoformat(),
            'segment':rows[0]['segment'],'model_version':next(iter(versions)),
            'model_weight':best,'baseline_weight':baseline_weight,'minimum_games':minimum_games,
            'training_game_ids':sorted(games),'training_input_sha256':digest(rows),
            'training_log_loss':statistics.fmean(game_losses(rows,best).values()),
            'holdout_game_ids':None,'evaluation':None}


def evaluate_candidate(candidate,rows,as_of):
    """Fix first later-issued games before scoring; never select by outcomes."""
    eligible=[r for r in rows if r['segment']==candidate['segment'] and r['model_version']==candidate['model_version']
        and stamp(r['issued_at'])>stamp(candidate['created_at']) and r['game_id'] not in candidate['training_game_ids']]
    games=list(dict.fromkeys(r['game_id'] for r in eligible))
    target=candidate['minimum_games']
    if candidate.get('holdout_game_ids') is None and len(games)>=target:candidate['holdout_game_ids']=games[:target]
    fixed=candidate.get('holdout_game_ids')
    if not fixed:return {'state':'collecting_prospective','observed_games':len(games),'required_games':target}
    holdout=[r for r in eligible if r['game_id'] in fixed]
    if not holdout or {r['game_id'] for r in holdout}!=set(fixed) or any(not r['resolved'] for r in holdout):
        return {'state':'awaiting_holdout_outcomes','holdout_game_ids':fixed}
    scored=[r for r in holdout if r['outcome'] in (0,1)]
    candidate_losses=game_losses(scored,candidate['model_weight']);base=game_losses(scored,candidate['baseline_weight']);market=game_losses(scored,0.)
    if len(candidate_losses)<target:return {'state':'rejected','reason':'Insufficient decisive independent holdout games','games':len(candidate_losses),'holdout_input_sha256':digest(holdout)}
    def compare(reference):
        deltas=[candidate_losses[g]-reference[g] for g in sorted(candidate_losses)]
        mean=statistics.fmean(deltas);se=statistics.stdev(deltas)/math.sqrt(len(deltas))
        return {'mean_delta':mean,'upper95':mean+1.96*se}
    prior=compare(base);reference=compare(market)
    promote=prior['upper95']<0 and reference['upper95']<0
    return {'state':'promoted' if promote else 'rejected','games':len(candidate_losses),
            'evaluated_at':as_of.isoformat(),'holdout_input_sha256':digest(holdout),
            'candidate_log_loss':statistics.fmean(candidate_losses.values()),
            'baseline_log_loss':statistics.fmean(base.values()),'market_log_loss':statistics.fmean(market.values()),
            'versus_baseline':prior,'versus_market':reference,'promote':promote,
            'basis':'Fixed later-issued independent-game holdout; paired game-mean log-loss, normal 95% upper bound below zero versus both current blend and devig market.'}


def _archive(folder,value):
    sha=digest(value);path=folder/(sha+'.json')
    if not path.exists():atomic_json(path,value)
    elif digest(json.loads(path.read_text()))!=sha:raise ValueError('anchor_archive_corrupt')
    return sha


def update_weights(data_dir,decisions,as_of,*,minimum_games=MIN_GAMES,fallback_weight=.15,committed_at=None):
    """Called by one existing durable learning worker under its process lock."""
    if not 0<=fallback_weight<=1:raise ValueError('invalid_fallback_weight')
    folder=Path(data_dir)/'anchor_learning';folder.mkdir(parents=True,exist_ok=True)
    archive=folder/'archive';archive.mkdir(exist_ok=True)
    state_path=folder/'state.json'
    state=json.loads(state_path.read_text()) if state_path.exists() else {'version':VERSION,'candidates':{},'weights':{}}
    if state.get('version')!=VERSION:raise ValueError('anchor_state_version')
    rows,reasons=observations(decisions,as_of)
    frozen={'as_of':as_of.isoformat(),'rows':rows,'excluded':reasons,'fallback_weight':fallback_weight,
            'source_code':{'anchor_learning.py':Path(__file__).read_text(),'market_anchor.py':Path(__file__).with_name('market_anchor.py').read_text()}}
    input_sha=digest(frozen)
    if state.get('last_input_archive')==input_sha and state.get('last_result'):
        active={'version':VERSION,'weights':state['weights']};active['snapshot_sha256']=digest(active)
        publish_snapshot(folder,active)
        return state['last_result']
    _archive(archive,frozen)
    groups=defaultdict(list)
    for row in rows:groups[(row['segment'],row['model_version'])].append(row)
    results=[];new_candidates=[]
    for key,group in sorted(groups.items()):
        identity='|'.join(key);prior=state['candidates'].get(identity)
        if prior and prior['state'] not in ('promoted','rejected'):
            baseline=state['weights'].get(identity,{}).get('model_weight',fallback_weight)
            evaluation=({'state':'rejected','reason':'Current baseline changed; next fit needs a new later-issued holdout.'} if baseline!=prior['baseline_weight'] else evaluate_candidate(prior,group,as_of));prior['evaluation']=evaluation
            prior['state']=evaluation['state'];prior['evaluation_archive']=input_sha
            if evaluation['state']=='promoted':
                proof={**prior,'promotion_input_archive':input_sha};proof_sha=_archive(archive,proof)
                state['weights'][identity]={'segment':key[0],'model_version':key[1],'model_weight':prior['model_weight'],
                    'promoted_at':as_of.isoformat(),'proof_sha256':proof_sha}
            results.append({'segment':key[0],'model_version':key[1],**evaluation})
            continue
        # Do not repeat unchanged rejected evidence. A later settled generation
        # can fit a new candidate, whose own holdout starts only after this fit.
        settled=[r for r in group if r['outcome'] in (0,1)]
        current_digest=digest(settled)
        if prior and prior['training_input_sha256']==current_digest:continue
        baseline=state['weights'].get(identity,{}).get('model_weight',fallback_weight)
        candidate=fit_candidate(settled,as_of,baseline,minimum_games)
        if candidate:
            candidate['training_as_of']=as_of.isoformat()
            candidate['input_archive']=input_sha;state['candidates'][identity]=candidate
            new_candidates.append(identity)
            results.append({'segment':key[0],'model_version':key[1],'state':candidate['state'],'training_games':len(candidate['training_game_ids'])})
    state['checked_at']=as_of.isoformat();state['last_input_archive']=input_sha
    active={'version':VERSION,'weights':state['weights']}
    active['snapshot_sha256']=digest(active)
    result={'version':VERSION,'checked_at':as_of.isoformat(),'input_archive':input_sha,'accepted_rows':len(rows),
            'excluded':reasons,'segments':results,'approved_weights':len(state['weights']),'snapshot_sha256':active['snapshot_sha256']}
    state['last_result']=result
    from .domain import utcnow
    with weight_epoch(folder.parent,exclusive=True):
        actual_commit=committed_at or utcnow()
        if actual_commit<as_of:raise ValueError('fit_commit_precedes_training_cutoff')
        for identity in new_candidates:state['candidates'][identity]['created_at']=actual_commit.isoformat()
        atomic_json(state_path,state)
        atomic_json(folder/'active.json',active)
    return result


def load_snapshot(data_dir):
    path=Path(data_dir)/'anchor_learning/active.json'
    if not path.exists():return None
    def identity(file):
        stat=file.stat();return (stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)
    cache_key=str(path.resolve())
    cached=_VALIDATED_CACHE.get(cache_key)
    if cached and all(identity(Path(file))==saved for file,saved in cached['files'].items()):
        _VALIDATED_CACHE.move_to_end(cache_key)
        return deepcopy(cached['value'])
    files={str(path):identity(path)};read_bytes=0
    if path.stat().st_size>2*1024*1024:raise ValueError('anchor_snapshot_size')
    value=json.loads(path.read_text());body={k:v for k,v in value.items() if k!='snapshot_sha256'}
    if value.get('version')!=VERSION or digest(body)!=value.get('snapshot_sha256'):raise ValueError('anchor_snapshot_digest')
    archives={}
    def source(sha):
        nonlocal read_bytes
        if len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha):raise ValueError('anchor_input_identity')
        if sha not in archives:
            file=path.parent/'archive'/(sha+'.json')
            size=file.stat().st_size;read_bytes+=size
            if size>32*1024*1024 or read_bytes>MAX_INPUT_BYTES:raise ValueError('anchor_input_size')
            files[str(file)]=identity(file)
            obj=json.loads(file.read_text())
            if files[str(file)]!=identity(file):raise ValueError('anchor_input_changed_during_read')
            if digest(obj)!=sha:raise ValueError('anchor_input_digest')
            archives[sha]=obj
        return archives[sha]
    for key,row in value.get('weights',{}).items():
        sha=row['proof_sha256']
        if len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha):raise ValueError('anchor_proof_identity')
        proof_path=path.parent/'archive'/(sha+'.json')
        if proof_path.stat().st_size>1024*1024:raise ValueError('anchor_proof_size')
        files[str(proof_path)]=identity(proof_path)
        proof=json.loads(proof_path.read_text());evaluation=proof.get('evaluation',{})
        if files[str(proof_path)]!=identity(proof_path):raise ValueError('anchor_proof_changed_during_read')
        if digest(proof)!=sha or proof.get('state')!='promoted' or evaluation.get('promote') is not True or evaluation.get('versus_baseline',{}).get('upper95',1)>=0 or evaluation.get('versus_market',{}).get('upper95',1)>=0 or key!='|'.join((proof['segment'],proof['model_version'])) or row['model_weight']!=proof['model_weight']:
            raise ValueError('anchor_promotion_proof')
        training=source(proof['input_archive']);later=source(proof['promotion_input_archive'])
        train_rows=[r for r in training['rows'] if r['segment']==proof['segment'] and r['model_version']==proof['model_version'] and r['outcome'] in (0,1)]
        refit=fit_candidate(train_rows,stamp(proof['training_as_of']),proof['baseline_weight'],proof['minimum_games'])
        if refit is None or refit['training_input_sha256']!=proof['training_input_sha256'] or refit['model_weight']!=proof['model_weight']:
            raise ValueError('anchor_fit_reproduction')
        replay=dict(proof);check=evaluate_candidate(replay,later['rows'],stamp(evaluation['evaluated_at']))
        if check!=evaluation:raise ValueError('anchor_holdout_reproduction')
    if any(identity(Path(file))!=saved for file,saved in files.items()):raise ValueError('anchor_snapshot_changed_during_read')
    _VALIDATED_CACHE[cache_key]={'files':files,'value':deepcopy(value)}
    _VALIDATED_CACHE.move_to_end(cache_key)
    while len(_VALIDATED_CACHE)>4:_VALIDATED_CACHE.popitem(last=False)
    return value


def runtime_settings(settings,data_dir):
    result={k:v for k,v in settings.items() if k!=RUNTIME_KEY}
    snapshot=load_snapshot(data_dir)
    if snapshot and snapshot['weights']:result[RUNTIME_KEY]=snapshot
    return result


def effective_weight(settings,league,kind,line,model_version):
    key=segment(league,kind,line)+'|'+str(model_version)
    snapshot=settings.get(RUNTIME_KEY) or {};row=snapshot.get('weights',{}).get(key)
    if row:
        weight=float(row['model_weight'])
        if not math.isfinite(weight) or not 0<=weight<=1:raise ValueError('learned_anchor_weight_invalid')
        return weight,{'version':VERSION,'segment':row['segment'],'model_version':model_version,
                       'proof_sha256':row['proof_sha256'],'snapshot_sha256':snapshot['snapshot_sha256']}
    return float(settings.get('market_model_weight',.15)),None


def anchor_settings(settings):
    result={k:settings.get(k,d) for k,d in (('market_anchor_mode','shadow'),('market_model_weight',.15),('market_disagreement_threshold',.30))}
    if settings.get(RUNTIME_KEY):result['learned_weights_sha256']=settings[RUNTIME_KEY]['snapshot_sha256']
    return result


@contextmanager
def weight_epoch(data_dir,*,exclusive=False):
    path=Path(data_dir)/'.anchor-weight-epoch.lock';path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:yield
        finally:fcntl.flock(lock,fcntl.LOCK_UN)


def publish_snapshot(folder,active):
    with weight_epoch(folder.parent,exclusive=True):atomic_json(folder/'active.json',active)


def iter_decisions(store):
    """Stream full original rows one at a time; retain only thin fitting inputs."""
    with store.db() as db:
        for row in db.execute('SELECT tip_id,issued_at,payload,status,outcome_evidence FROM system_decisions ORDER BY issued_at,tip_id'):
            yield {**dict(row),'payload':json.loads(row['payload']),
                   'outcome_evidence':json.loads(row['outcome_evidence']) if row['outcome_evidence'] else None}
