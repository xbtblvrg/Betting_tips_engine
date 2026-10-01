"""Independent MLB inning models fitted to observed inning targets.

No market prices or full-game probability rescaling enter fitting. The joint
distribution assumes conditional independence; that limitation stays explicit.
"""
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from collections import defaultdict
from functools import cached_property
import hashlib,io,json,math
import joblib,numpy as np
from scipy.stats import poisson,nbinom
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import PoissonRegressor
from .domain import utcnow,parse_time,Game
from .models import feature_history,feature_names
from .contracts import split_market,contract_value,SEGMENT_SCOPES
from .providers import DATA
from .io_utils import atomic_json,atomic_bytes

PROTOCOL='mlb-inning-count-v1'

def dispersion(y,mu):
    return float(np.clip(np.sum((y-mu)**2-y)/max(np.sum(mu**2),1e-9),0,3))

def count_pmf(mu,alpha):
    mu=float(np.clip(mu,.01,20))
    dist=nbinom(1/alpha,1/(1+alpha*mu)) if alpha>1e-8 else poisson(mu)
    end=int(dist.ppf(1-1e-10));x=np.arange(end+1);p=dist.pmf(x)
    if end>250 or not np.isfinite(p).all() or p.sum()<1-1e-8:raise ValueError('Unreliable count-distribution support')
    return x,p/p.sum()

def joint_scores(mu,alpha):
    h,ph=count_pmf(mu[0],alpha[0]);a,pa=count_pmf(mu[1],alpha[1])
    hh,aa=np.meshgrid(h,a,indexing='ij');return hh.ravel(),aa.ravel(),np.outer(ph,pa).ravel()

def joint_logscore(y,mu,alpha):
    scores=np.zeros(len(y))
    for k in (0,1):
        a=alpha[k];m=np.maximum(.01,mu[:,k])
        scores-=nbinom.logpmf(y[:,k],1/a,1/(1+a*m)) if a>1e-8 else poisson.logpmf(y[:,k],m)
    return scores

def evaluations(y,mu,alpha,scope):
    lines=(.5,1.5,2.5) if scope=='inning_1' else (2.5,3.5,4.5,5.5,6.5)
    families=defaultdict(list);bias=defaultdict(list)
    for truth,m in zip(y,mu):
        h,a,w=joint_scores(m,alpha)
        tests=[('three_way',s,None) for s in ('home','draw','away')]
        tests += [('totals','over',v) for v in lines]
        tests += [(k,'over',v) for k in ('home_total','away_total') for v in ((.5,1.5) if scope=='inning_1' else (1.5,2.5,3.5))]
        tests += [('spreads','home',v) for v in (-1.5,-.5,.5,1.5)]
        for kind,side,line in tests:
            value=contract_value(kind,side,line,h,a);p=float(w[value>0].sum())
            actual=float(contract_value(kind,side,line,*truth)>0)
            families[kind].append((p-actual)**2);bias[(kind,side,line)].append(p-actual)
    return {'n':len(y),'joint_log_score':float(joint_logscore(y,mu,alpha).mean()),
        'brier':{k:float(np.mean(v)) for k,v in families.items()},
        'probability_bias':{k:max(abs(float(np.mean(v))) for (family,_,_),v in bias.items() if family==k) for k in families}}

@dataclass
class SegmentForecast:
    game_id:str
    league:str
    scope:str
    home_points:float
    away_points:float
    alpha:tuple
    features:dict
    model_version:str
    warnings:list
    disagreement:float=0.

    @property
    def margin(self):return self.home_points-self.away_points
    @property
    def total(self):return self.home_points+self.away_points
    @cached_property
    def scores(self):return joint_scores((self.home_points,self.away_points),self.alpha)
    @property
    def model_probabilities(self):
        h,a,w=self.scores
        return {'home':float(w[h>a].sum()),'draw':float(w[h==a].sum()),'away':float(w[h<a].sum())}
    def market_probability(self,kind,side,line=None):
        scope,base=split_market(kind)
        if scope!=self.scope:raise ValueError('Forecast and market scopes differ')
        h,a,w=self.scores
        v=contract_value(base,side,line,h,a)
        return float(w[v>0].sum()),float(w[v==0].sum())
    def public(self):return {**{k:v for k,v in self.__dict__.items() if k!='scores'},'margin':self.margin,'total':self.total,'model_probabilities':self.model_probabilities}

class SegmentModel:
    def __init__(self,scope,alpha,family):self.scope=scope;self.alpha=alpha;self.family=family
    def fit(self,x,y):
        self.regressors=[make_pipeline(StandardScaler(),PoissonRegressor(alpha=self.alpha,max_iter=1000)).fit(x,y[:,k]) for k in (0,1)]
        mu=self.predict(x)
        self.dispersions=tuple(dispersion(y[:,k],mu[:,k]) if self.family=='negative_binomial' else 0. for k in (0,1))
        return self
    def predict(self,x):return np.clip(np.column_stack([r.predict(x) for r in self.regressors]),.01,20)
    def forecast(self,g,state):
        x=state.features(g);h,a=self.predict(x.reshape(1,-1))[0]
        return SegmentForecast(g.id,g.league,self.scope,float(h),float(a),self.dispersions,
            dict(zip(feature_names(g.league),map(float,x))),self.report['version'],list(self.report['limitations']))

def training_digest(rows):
    raw=[(g.id,g.start,list(map(float,x)),g.context['segment_scores']) for g,x in rows]
    # Fetch times change without adding statistical evidence.
    slim=[(id,date,x,{k:(v['home'],v['away']) for k,v in scopes.items()}) for id,date,x,scopes in raw]
    return hashlib.sha256(json.dumps(slim,sort_keys=True).encode()).hexdigest()

def paired_gate(old,new,rows,x,y,scope):
    test_from=min(parse_time(g.start) for g,_ in rows)
    if parse_time(old.report['fit_through'])>=test_from:return {'promote':False,'reason':'Champion training overlaps comparison period.'}
    old_losses=joint_logscore(y,old.predict(x),old.dispersions)
    new_losses=joint_logscore(y,new.predict(x),new.dispersions)
    weeks=defaultdict(list)
    for (g,_),delta in zip(rows,new_losses-old_losses):
        d=parse_time(g.start).isocalendar();weeks[(d.year,d.week)].append(float(delta))
    if len(weeks)<12:return {'promote':False,'reason':'Fewer than 12 comparison weeks.'}
    blocks=[np.array(v) for v in weeks.values()];rng=np.random.default_rng(923)
    draws=[np.concatenate([blocks[i] for i in rng.integers(0,len(blocks),len(blocks))]).mean() for _ in range(500)]
    ci=list(map(float,np.quantile(draws,[.025,.975])))
    return {'promote':ci[1]<0,'delta_log_score_ci95':ci,'weeks':len(weeks),'reason':'Paired chronological joint log-score comparison; not a profit test.'}

def train_segments(data_dir=DATA,refresh=True):
    from .segment_data import refresh_scores,read_scores,attach_scores
    from .training_evidence import capture_inputs,bind_archive
    data=Path(data_dir)
    scores,_=refresh_scores(data,historical=True) if refresh else (read_scores(data),[])
    games=[Game(**g) for g in json.loads((data/'games.json').read_text())];attach_scores(games,scores)
    all_rows,_=feature_history(games,'MLB')
    cutoff=utcnow()-timedelta(hours=8)
    all_rows=[(g,x) for g,x in all_rows if parse_time(g.start)<cutoff and g.context.get('segment_scores')]
    out=data/'segment_models';out.mkdir(parents=True,exist_ok=True);results={}
    for scope in SEGMENT_SCOPES:
        rows=[(g,x) for g,x in all_rows if scope in g.context['segment_scores']]
        if len(rows)<1500:raise ValueError(f'{scope}: only {len(rows)} matched completed inning observations')
        dates=sorted({parse_time(g.start).date().isoformat() for g,_ in rows})
        select_date=dates[int(len(dates)*.6)];test_date=dates[int(len(dates)*.8)]
        days=np.array([parse_time(g.start).date().isoformat() for g,_ in rows]);train=days<select_date;select=(days>=select_date)&(days<test_date);test=days>=test_date
        x=np.array([v for _,v in rows]);y=np.array([[g.context['segment_scores'][scope][s] for s in ('home','away')] for g,_ in rows])
        destination=out/f'MLB-{scope}.joblib';champion_raw=destination.read_bytes() if destination.exists() else None
        digest=training_digest(rows);choices=[]
        inputs=capture_inputs(data,[g for g,_ in rows],'MLB',scope,
            matrix=x,targets=y,partitions=['train' if a else 'selection' if b else 'test' for a,b in zip(train,select)],
            recipe={'alpha':[.1,1.,10.],'family':['poisson','negative_binomial']},metadata={'training_digest':digest})
        for alpha in (.1,1.,10.):
            for family in ('poisson','negative_binomial'):
                model=SegmentModel(scope,alpha,family).fit(x[train],y[train])
                loss=float(joint_logscore(y[select],model.predict(x[select]),model.dispersions).mean())
                choices.append((loss,model))
        _,model=min(choices,key=lambda pair:pair[0]);pred=model.predict(x[test])
        evaluation=evaluations(y[test],pred,model.dispersions,scope)
        base_mu=y[train].mean(axis=0);base_alpha=tuple(dispersion(y[train,k],np.full(train.sum(),base_mu[k])) for k in (0,1))
        baseline=evaluations(y[test],np.tile(base_mu,(test.sum(),1)),base_alpha,scope)
        validated=[k for k,b in evaluation['brier'].items() if b<=baseline['brier'][k]+.005 and abs(evaluation['probability_bias'][k])<=.04]
        # A two-way push market derives from the identical three-way score mass.
        if 'three_way' in validated:validated.append('moneyline')
        adequate=int(test.sum())>=300 and evaluation['joint_log_score']<=baseline['joint_log_score']*1.02
        gate={'promote':bool(adequate and validated),'reason':'Chronological segment validation against a frozen league-rate baseline; not proof of profit.',
            'minimum_test_games':300,'maximum_relative_logscore_regression':.02,'maximum_brier_regression':.005,'maximum_probability_bias':.04,
            'validated_markets':validated if adequate else []}
        test_rows=[r for r,keep in zip(rows,test) if keep]
        if champion_raw:
            champion=joblib.load(io.BytesIO(champion_raw));comparison=paired_gate(champion,model,test_rows,x[test],y[test],scope)
            gate['comparison']=comparison;gate['promote']=gate['promote'] and comparison['promote']
        code=Path(__file__).read_bytes();code_hash=hashlib.sha256(code).hexdigest()
        version=f'MLB-{scope}-{PROTOCOL}-{digest[:12]}-{code_hash[:8]}'
        model.report={'league':'MLB','scope':scope,'version':version,'trained_at':utcnow().isoformat(),'fit_through':max(g.start for (g,_),keep in zip(rows,train) if keep),
            'training_games':len(rows),'training_inputs':inputs,'partitions':{'train':int(train.sum()),'selection':int(select.sum()),'test':int(test.sum())},
            'features':list(feature_names('MLB')),'odds_used':False,'code_sha256':code_hash,'training_data_fingerprint':digest,
            'model_family':model.family,'regularization':model.alpha,'dispersion':model.dispersions,
            'test_from':test_date,'test':evaluation,'baseline':baseline,'promotion_gate':gate,
            'validated_markets':gate['validated_markets'],'profit_backtest':None,
            'selection_trials':[{'log_score':loss,'family':m.family,'alpha':m.alpha} for loss,m in choices],
            'limitations':['Fitted to actual completed inning scores; not a scaled full-game forecast.',
                'Conditional independence of home/away runs is an approximation; lineup, weather and full bullpen effects are not quantified.',
                'Chronological retrospective validation uses corrected historical data; executable-price profit is not established.']}
        current=destination.read_bytes() if destination.exists() else None
        if current!=champion_raw:gate.update(promote=False,reason='Champion changed while fitting; retain current model.')
        # Recheck semantic source evidence without reacting to metadata-only refreshes.
        fresh_games=[Game(**g) for g in json.loads((data/'games.json').read_text())];attach_scores(fresh_games,read_scores(data))
        ids={g.id for g,_ in rows}
        latest_rows,_=feature_history(fresh_games,'MLB');latest_rows=[r for r in latest_rows if r[0].id in ids and scope in r[0].context.get('segment_scores',{})]
        if training_digest(latest_rows)!=digest:gate.update(promote=False,reason='Training evidence changed while fitting; retain current model.')
        # Re-running identical evidence can reject the candidate. Keep each
        # attempt separate so it cannot overwrite an approved recovery archive.
        archive=out/'versions'/(version+'-'+utcnow().strftime('%Y%m%dT%H%M%S%fZ'));archive.mkdir(parents=True,exist_ok=False)
        buf=io.BytesIO();joblib.dump(model,buf);atomic_bytes(archive/'model.joblib',buf.getvalue());atomic_bytes(archive/'segments.py',code)
        atomic_json(archive/'report.json',model.report)
        bind_archive(data,archive,model.report)
        if gate['promote']:atomic_bytes(destination,buf.getvalue());atomic_json(out/f'MLB-{scope}.json',model.report)
        results[scope]=model.report
    atomic_json(out/'latest_training.json',{'checked_at':utcnow().isoformat(),'scopes':results})
    return results
