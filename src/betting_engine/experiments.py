"""Predeclared recipe search on earlier data, followed by one champion test."""
from pathlib import Path
from datetime import datetime,timedelta,timezone
import hashlib,io,json,joblib,math
from .models import LeagueModel,RECIPES
from .domain import Game,parse_time,utcnow
from .providers import DATA
from .io_utils import atomic_json,atomic_bytes
from .train import score_gate

PROTOCOL='chronological-recipe-search-v1'
HYPOTHESES={
 'elo_identity':'Use odds-independent Elo without fitted calibration; select on earlier data and retain the paired promotion gate.',
 'baseline':'Reproduce the current model family as the development control.',
 'flexible_calibration':'Reduce shrinkage in probability calibration when forecasts cluster too tightly.',
 'strong_calibration':'Reduce calibration variance in a small or unstable sample.',
 'regularized':'Reduce variance in team features and score regression.',
 'richer_interactions':'Represent more nonlinear interactions with stronger tree regularization.',
 'elo_anchor':'Test an interpretable Elo-only probability component with separate calibration.',
 'identity_calibration':'Test the original clipped ensemble probabilities without fitted recalibration; select only on earlier development outcomes.',
}

TRAINING_IDENTITY_VERSION='semantic-training-evidence-v2'


def _work_start(value):
    stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
    # Existing date-only/naive inputs keep their representation. Only aware
    # equivalent instants are normalized; the model still owns interpretation.
    return stamp.astimezone(timezone.utc).isoformat() if stamp.tzinfo is not None else value


def _work_score(value):
    if type(value) not in (int,float) or not math.isfinite(value) or value<0 or value!=int(value):
        raise ValueError('Training scores must be finite nonnegative integers')
    return int(value)


def training_digest(games,league):
    """Semantic work identity, separate from immutable raw-input fingerprints.

    Equivalent UTC spellings and integral score types are not new evidence.
    Meaningful sports inputs remain visible. Existing model as_of/result
    availability checks are untouched. Raw archives and past study digests are
    never rewritten, and this identity does not depend on wall-clock time.
    """
    rows=[]
    for g in games:
        if g.league!=league or g.state!='final' or g.context.get('resumed_game'):
            continue
        rows.append((g.id,_work_start(g.start),_work_score(g.home_score),_work_score(g.away_score),g.home_id,g.away_id,g.neutral,g.game_type,g.context.get('box'),
           sorted(g.context.get('pitching_observations') or [],key=lambda r:json.dumps(r,sort_keys=True,allow_nan=False)),
           (g.context.get('home_pitcher') or {}).get('id'),(g.context.get('away_pitcher') or {}).get('id')))
    rows.sort(key=lambda r:(r[1],r[0]))
    return hashlib.sha256(json.dumps({'version':TRAINING_IDENTITY_VERSION,'rows':rows},sort_keys=True,allow_nan=False).encode()).hexdigest()

def choose_recipe(rows):
    eligible=[r for r in rows if not r.get('error') and r.get('brier') is not None]
    if len(eligible)!=len(RECIPES):raise ValueError('Incomplete experiment; resolve failed recipes before comparison')
    return min(eligible,key=lambda r:(r['brier'],r['log_loss'],r['recipe']))['recipe']


def study_identity(league,digest):
    """One shared queue/archive identity for exact protocol, recipes and inputs.

    Keep the original archive-key formula so earlier immutable study receipts
    remain addressable. No timestamps or mutable execution state enter this key.
    """
    return hashlib.sha256((PROTOCOL+league+digest+json.dumps(RECIPES,sort_keys=True)).encode()).hexdigest()[:24]

def earlier_history(games,cutoff):
    def available(game):
        start=parse_time(game.start)
        return max(start+timedelta(hours=8),start.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1))
    return [g for g in games if parse_time(g.start)<cutoff and available(g)<=cutoff]

def run_study(league,data_dir=DATA,expected_digest=None,expected_study_id=None):
    from .train import promotion_gate
    from .training_evidence import capture_inputs,bind_archive
    data=Path(data_dir);games=[Game(**g) for g in json.loads((data/'games.json').read_text())]
    digest=training_digest(games,league)
    if expected_digest is not None and digest!=expected_digest:
        return {'state':'superseded','reason':'Historical source changed before execution; queue a study for the new data.','profit_established':False}
    key=study_identity(league,digest)
    if expected_study_id is not None and key!=expected_study_id:
        return {'state':'superseded','reason':'Declared experiment protocol or recipes changed before execution; queue the current specification.','profit_established':False}
    folder=data/'experiments'/league/key;folder.mkdir(parents=True,exist_ok=True)
    result_path=folder/'result.json'
    if result_path.exists():return json.loads(result_path.read_text())
    champion_path=data/'models'/f'{league}.joblib';champion=joblib.load(champion_path)
    cutoff=parse_time(champion.report['test_from'])
    # Hard partition, not just a model flag: no final comparison outcome is
    # passed to the recipe-selection fits.
    earlier=earlier_history(games,cutoff)
    manifest={'protocol':PROTOCOL,'league':league,'training_digest':digest,'training_identity_version':TRAINING_IDENTITY_VERSION,'created_at':utcnow().isoformat(),
              'recipes':RECIPES,'hypotheses':HYPOTHESES,'selection_cutoff':cutoff.isoformat(),
              'comparison_model':champion.report['version'],
              'acceptance':'Select on earlier development data; paired outer Brier gate plus <=5% score-MAE regression.',
              'evaluation_status':'Retrospective development experiment; not proof of live profitability.'}
    selection_inputs=capture_inputs(data,earlier,league,recipe=RECIPES,as_of=cutoff,metadata={'purpose':'recipe_selection','training_digest':digest})
    manifest['selection_training_inputs']=selection_inputs
    atomic_json(folder/'manifest.json',manifest)
    rows=[]
    for recipe in RECIPES:
        model=LeagueModel(league,recipe).fit(earlier,as_of=cutoff)
        row={'recipe':recipe,'hypothesis':HYPOTHESES[recipe],**{k:model.report['test'][k] for k in ('n','brier','log_loss')},'last_game':model.report['last_game']}
        rows.append(row);atomic_json(folder/'development.json',rows)
    selected=choose_recipe(rows)
    fit_at=utcnow()
    comparison_inputs=capture_inputs(data,games,league,recipe=selected,as_of=fit_at,metadata={'purpose':'selected_challenger','training_digest':digest})
    challenger=LeagueModel(league,selected).fit(games,as_of=fit_at)
    challenger.report.setdefault('league',league);challenger.report.setdefault('recipe',selected);challenger.report['training_inputs']=comparison_inputs
    gate=promotion_gate(champion,challenger);scores=score_gate(champion,challenger)
    if league.startswith('NCAA'):
        from .college import initial_gate
        family_gate=initial_gate(challenger)
        gate={**gate,'family_validation':family_gate,'promote':gate['promote'] and family_gate['promote']}
    promote=gate['promote'] and scores['passed']
    result={'state':'completed','league':league,'protocol':PROTOCOL,'study_id':key,'training_digest':digest,'training_identity_version':TRAINING_IDENTITY_VERSION,
            'selected_recipe':selected,'development':rows,'comparison_model':champion.report['version'],
            'model_version':challenger.version,'promotion_gate':{**gate,'promote':promote,'score_guard':scores},
            'finished_at':utcnow().isoformat(),'profit_established':False,
            'next_action':'Monitor prospective outcomes of the promoted candidate.' if promote else 'Retain the deployed model; record hypothesis results and reevaluate when historical evidence changes.'}
    # Source can change during the study; do not overwrite a newly promoted
    # champion from another process with a comparison against an older one.
    current=joblib.load(champion_path)
    if current.report['version']!=champion.report['version']:
        result['promotion_gate']['promote']=False;result['next_action']='Champion changed during experiment; retain it and compare again.'
    latest=[Game(**g) for g in json.loads((data/'games.json').read_text())]
    if training_digest(latest,league)!=digest:
        result['promotion_gate']['promote']=False;result['next_action']='Historical data changed during experiment; compare the updated evidence.'
    challenger.report['promotion_gate']=result['promotion_gate'];challenger.report['experiment']=dict(manifest,study_id=key,selected_recipe=selected)
    buffer=io.BytesIO();joblib.dump(challenger,buffer)
    atomic_bytes(folder/'challenger.joblib',buffer.getvalue());atomic_json(folder/'challenger_report.json',challenger.report)
    bind_archive(data,folder,challenger.report)
    for name in ('models.py','context.py','domain.py','contracts.py','experiments.py','train.py','college.py'):
        atomic_bytes(folder/name,(Path(__file__).parent/name).read_bytes())
    if result['promotion_gate']['promote']:
        atomic_bytes(champion_path,buffer.getvalue());atomic_json(data/'models'/f'{league}.json',challenger.report)
    atomic_json(result_path,result);return result
