"""Versioned training; daily candidates replace a champion only after a paired gate."""
from pathlib import Path
from collections import defaultdict
import json,logging,joblib,argparse,io,math,hashlib
import numpy as np
from .providers import DATA,SportsData
from .domain import Game,LEAGUES,parse_time,utcnow
from .models import LeagueModel, RECIPES
from .io_utils import atomic_json,atomic_bytes

def score_gate(champion,challenger):
    checks={}
    for name in ('margin','total'):
        old=champion.report.get('score_mae',{}).get(name);new=challenger.report.get('score_mae',{}).get(name)
        checks[name]=(isinstance(old,(int,float)) and isinstance(new,(int,float))
                      and math.isfinite(old) and math.isfinite(new) and 0<=new<=old*1.05)
    return {'passed':all(checks.values()),'checks':checks,'maximum_relative_mae_regression':.05}

def promotion_gate(champion,challenger):
    old={r['game_id']:r for r in champion.report.get('test_predictions',[])}
    pairs=[(old[r['game_id']],r) for r in challenger.report.get('test_predictions',[]) if r['game_id'] in old]
    if len(pairs)<150:return {'promote':False,'reason':'Kevés közös időben elkülönített tesztmeccs','paired_games':len(pairs)}
    weeks=defaultdict(list)
    for a,b in pairs:
        if a['result']!=b['result']:return {'promote':False,'reason':'Eltérő tesztcéladat; forrásfelülvizsgálat szükséges'}
        dt=parse_time(b['date']).isocalendar();weeks[(dt.year,dt.week)].append((b['p']-b['result'])**2-(a['p']-a['result'])**2)
    if len(weeks)<12:return {'promote':False,'reason':'Kevesebb mint 12 közös teszthét','paired_games':len(pairs)}
    blocks=[np.array(v) for v in weeks.values()];rng=np.random.default_rng(871)
    draws=[np.concatenate([blocks[i] for i in rng.integers(0,len(blocks),len(blocks))]).mean() for _ in range(1000)]
    lo,hi=map(float,np.quantile(draws,[.025,.975]))
    # Does not claim unseen prospective evidence; no architecture/feature self-rewrite.
    scores=score_gate(champion,challenger)
    return {'promote':hi<0 and scores['passed'],'reason':'Paired weekly Brier comparison and score-regression check; not proof of betting profit.','paired_games':len(pairs),'weeks':len(blocks),'delta_ci95':[lo,hi],'score_guard':scores}

def train(refresh=False,leagues=LEAGUES,controlled=True,recipe_override=None):
    # Legacy callers may still pass False. It must never turn an existing
    # champion replacement into an unchecked initial deployment.
    controlled=True
    from .experiments import training_digest
    from .training_evidence import capture_inputs,bind_archive
    if refresh or not (DATA/'games.json').exists():
        games,_=SportsData().load()
        from .context import SportContext,PitchingContext
        atomic_json(DATA/'context_sources.json',SportContext().enrich(games));atomic_json(DATA/'pitching_sources.json',PitchingContext().enrich(games));atomic_json(DATA/'games.json',[g.dict() for g in games])
    else:games=[Game(**g) for g in json.loads((DATA/'games.json').read_text())]
    out=DATA/'models';out.mkdir(parents=True,exist_ok=True);reports={}
    for league in leagues:
        try:
            destination=out/(league+'.joblib')
            champion_raw=destination.read_bytes() if controlled and destination.exists() else None
            champion=joblib.load(io.BytesIO(champion_raw)) if champion_raw is not None else None
            evidence_digest=training_digest(games,league)
            recipe=(recipe_override or champion.report.get('recipe','baseline')) if champion else (recipe_override or 'baseline')
            fit_at=utcnow()
            inputs=capture_inputs(DATA,games,league,recipe=recipe,as_of=fit_at,metadata={'training_digest':evidence_digest})
            model=LeagueModel(league,recipe).fit(games,as_of=fit_at)
            model.report.setdefault('league',league);model.report.setdefault('recipe',recipe);model.report['training_inputs']=inputs
            gate=promotion_gate(champion,model) if champion else {'promote':True,'reason':'Első / kézzel indított fejlesztői modellkiadás'}
            if league.startswith('NCAA'):
                from .college import initial_gate
                college_gate=initial_gate(model)
                gate=({**gate,'family_validation':college_gate,'promote':gate['promote'] and college_gate['promote']} if champion else college_gate)
            if controlled:
                latest=[Game(**g) for g in json.loads((DATA/'games.json').read_text())]
                current_raw=destination.read_bytes() if destination.exists() else None
                data_stable=training_digest(latest,league)==evidence_digest
                champion_stable=(current_raw is None and champion_raw is None) or (current_raw is not None and champion_raw is not None and hashlib.sha256(current_raw).digest()==hashlib.sha256(champion_raw).digest())
                gate.update(training_evidence_stable=data_stable,champion_stable=champion_stable)
                if not data_stable or not champion_stable:
                    gate.update(promote=False,reason='Training evidence or deployed model changed during fitting; retain the current model.')
            model.report['promotion_gate']=gate
            archive=out/'versions'/(model.version+'-'+utcnow().strftime('%Y%m%dT%H%M%S%fZ'));archive.mkdir(parents=True,exist_ok=False)
            for name in ('models.py','context.py','domain.py'):atomic_bytes(archive/name,(Path(__file__).parent/name).read_bytes())
            buffer=io.BytesIO();joblib.dump(model,buffer);atomic_bytes(archive/'model.joblib',buffer.getvalue());atomic_json(archive/'report.json',model.report)
            bind_archive(DATA,archive,model.report)
            if gate['promote']:atomic_bytes(destination,buffer.getvalue());atomic_json(out/(league+'.json'),model.report)
            reports[league]={k:v for k,v in model.report.items() if k!='test_predictions'}
            print(league,json.dumps({k:reports[league][k] for k in ['training_games','test','baseline','brier_delta_ci95','promotion_gate']},ensure_ascii=False),flush=True)
        except Exception as e:logging.exception('Training failed: %s',league);reports[league]={'error':str(e)}
    atomic_json(out/'summary.json',reports);return reports

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--refresh',action='store_true');parser.add_argument('--controlled',action='store_true',default=True);parser.add_argument('--league',choices=LEAGUES,action='append');parser.add_argument('--recipe',choices=tuple(sorted(RECIPES)),help='Recipe for a challenger; existing promotion checks always apply.');args=parser.parse_args()
    train(args.refresh,args.league or LEAGUES,args.controlled,args.recipe)
