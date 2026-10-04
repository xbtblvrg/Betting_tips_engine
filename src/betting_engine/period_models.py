"""Scope-specific empirical joint score forecasts from independent sports data.

Nearest-neighbour distributions preserve observed home/away score dependence
and discrete scoring. Recipe selection and family validation use separate dates.
They are benchmarks, not a claim of the most advanced or profitable model.
"""
from dataclasses import dataclass
from pathlib import Path
from collections import defaultdict
import hashlib,io,json,math
import joblib,numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.neighbors import NearestNeighbors
from .contracts import contract_value,split_market,PERIOD_SCOPES,SCOPE_LABELS
from .domain import Game,utcnow,parse_time
from .providers import DATA
from .models import feature_history,feature_names
from .io_utils import atomic_json,atomic_bytes

PROTOCOL='empirical-period-v1'

@dataclass
class PeriodForecast:
    game_id:str
    league:str
    scope:str
    home_points:float
    away_points:float
    scores:tuple
    features:dict
    model_version:str
    warnings:list
    disagreement:float=0.
    @property
    def margin(self):return self.home_points-self.away_points
    @property
    def total(self):return self.home_points+self.away_points
    @property
    def model_probabilities(self):
        h,a,w=self.scores
        return {'home':float(w[h>a].sum()),'draw':float(w[h==a].sum()),'away':float(w[h<a].sum())}
    def market_probability(self,kind,side,line=None):
        scope,base=split_market(kind)
        if scope!=self.scope:raise ValueError('Forecast and market scopes differ')
        h,a,w=self.scores;v=contract_value(base,side,line,h,a)
        return float(w[v>0].sum()),float(w[v==0].sum())
    def public(self):
        return {**{k:v for k,v in self.__dict__.items() if k!='scores'},'margin':self.margin,'total':self.total,'model_probabilities':self.model_probabilities}

class PeriodModel:
    def __init__(self,league,scope,k):self.league=league;self.scope=scope;self.k=k
    def fit(self,x,y):
        self.scaler=StandardScaler().fit(x);self.x=self.scaler.transform(x);self.y=np.array(y,dtype=float)
        self.nn=NearestNeighbors(n_neighbors=min(self.k,len(x)),algorithm='brute').fit(self.x)
        self.joint_calibration=None
        return self
    def distributions(self,x):
        distances,indices=self.nn.kneighbors(self.scaler.transform(x))
        # Uniform smoothing retains support from the earlier training set.
        for d,ix in zip(distances,indices):
            weights=np.exp(-d/max(float(np.median(d)),1e-6));weights/=weights.sum()
            w=np.full(len(self.y),.1/len(self.y));w[ix]+=.9*weights
            calibration=getattr(self,'joint_calibration',None)
            if calibration is not None:
                from scipy.special import logsumexp
                logw=np.log(w)+(self.y-calibration['center'])/calibration['scale']@calibration['theta']
                w=np.exp(logw-logsumexp(logw))
            yield self.y[:,0],self.y[:,1],w
    def calibrate_joint(self,x,y):
        """Fit a fixed regularized joint tilt on selection targets, never test rows.

        The positive tilt retains the empirical paired-score support, including
        ties and discrete scoring. It cannot manufacture unseen score outcomes.
        """
        from scipy.optimize import minimize
        from scipy.special import logsumexp
        x=np.asarray(x,dtype=float)
        if x.ndim!=2 or x.shape[1]!=self.x.shape[1] or not np.isfinite(x).all():
            raise ValueError('Calibration requires finite matching sport features')
        targets=np.asarray(y,dtype=float)
        if (targets.shape!=(len(x),2) or not len(targets) or not np.isfinite(targets).all()
                or (targets<0).any() or (targets!=np.floor(targets)).any()):
            raise ValueError('Calibration requires finite observed paired scores')
        if getattr(self,'joint_calibration',None) is not None:
            raise ValueError('Joint calibration already fitted')
        center=self.y.mean(axis=0);scale=np.maximum(self.y.std(axis=0),1.)
        support=(self.y-center)/scale;target=((targets-center)/scale).mean(axis=0)
        logweights=np.log(np.array([w for _,_,w in self.distributions(x)]))
        penalty=.05
        def objective(theta):
            logits=logweights+support@theta
            z=logsumexp(logits,axis=1)
            weights=np.exp(logits-z[:,None])
            loss=float(z.mean()-target@theta+penalty*(theta@theta)/2)
            gradient=(weights@support).mean(axis=0)-target+penalty*theta
            return loss,gradient
        fitted=minimize(objective,np.zeros(2),jac=True,method='L-BFGS-B',bounds=[(-2.,2.)]*2)
        if not fitted.success or not np.isfinite(fitted.x).all():
            raise ValueError('Joint score calibration did not converge')
        self.joint_calibration={'version':'selection-joint-tilt-v1','center':center,
            'scale':scale,'theta':fitted.x,'regularization':penalty,'selection_games':len(targets)}
        return {k:v.tolist() if isinstance(v,np.ndarray) else v for k,v in self.joint_calibration.items()}
    def forecast(self,g,state):
        x=state.features(g);h,a,w=next(self.distributions(x.reshape(1,-1)))
        return PeriodForecast(g.id,g.league,self.scope,float(h@w),float(a@w),(h,a,w),
            dict(zip(feature_names(g.league),map(float,x))),self.report['version'],list(self.report['limitations']))

def probes(y):
    rows=[('three_way',s,None) for s in ('home','draw','away')]
    rows += [('spreads','home',float(v)) for v in (-3.5,-1.5,-.5,.5,1.5,3.5)]
    for k,col in [('totals',y.sum(axis=1)),('home_total',y[:,0]),('away_total',y[:,1])]:
        rows += [(k,'over',float(np.floor(v)+.5)) for v in sorted(set(np.quantile(col,[.2,.4,.6,.8])))]
    rows += [('odd_even','odd',None),('both_score','yes',None)]
    rows += [('double_chance',s,None) for s in ('home_draw','away_draw','home_away')]
    return list(dict.fromkeys(rows))

def evaluate(model,x,y,tests):
    columns=[];baseline=[]
    for kind,side,line in tests:
        columns.append(contract_value(kind,side,line,model.y[:,0],model.y[:,1])>0)
        baseline.append(float(np.mean(columns[-1])))
    target=np.array([contract_value(k,s,l,y[:,0],y[:,1])>0 for k,s,l in tests],dtype=float).T
    matrix=np.array(columns,dtype=float)
    pred=np.array([matrix@w for _,_,w in model.distributions(x)])
    loss=(pred-target)**2;base=(np.array(baseline)[None,:]-target)**2
    families=defaultdict(list)
    for i,(k,_,_) in enumerate(tests):families[k].append(i)
    metrics={k:{'brier':float(loss[:,ix].mean()),'baseline_brier':float(base[:,ix].mean()),
                'max_probability_bias':float(np.abs((pred[:,ix]-target[:,ix]).mean(axis=0)).max())} for k,ix in families.items()}
    return {'n':len(y),'mean_brier':float(np.mean([m['brier'] for m in metrics.values()])),
            'families':metrics},loss.mean(axis=1)

def digest(rows,scope):
    values=[(g.id,g.start,list(map(float,x)),tuple(g.context['segment_scores'][scope][s] for s in ('home','away'))) for g,x in rows]
    return hashlib.sha256(json.dumps(values).encode()).hexdigest()

def train_periods(data_dir=DATA,refresh=True,leagues=('NBA','WNBA','NHL','NFL'),*,calibrate_joint=False,scopes=None):
    from .period_data import refresh_scores,read_scores,attach_scores
    from .training_evidence import capture_inputs,bind_archive
    data=Path(data_dir)
    selected_scopes=tuple(PERIOD_SCOPES) if scopes is None else tuple(scopes)
    if (not selected_scopes or len(set(selected_scopes))!=len(selected_scopes)
            or any(scope not in PERIOD_SCOPES for scope in selected_scopes)):
        raise ValueError('Unknown or duplicate exact period training scope')
    if refresh:
        _,sources=refresh_scores(data,historical=True)
        failed=[f'{r["league"]}/{r["season"]}' for r in sources if not r.get('ok')]
        if failed:
            # Retain champions and let the durable worker retry. Cached targets
            # may support inference, but a failed refresh is not a training win.
            raise RuntimeError('Period training source refresh failed: '+', '.join(failed))
    games=[Game(**g) for g in json.loads((data/'games.json').read_text())];attach_scores(games,read_scores(data))
    folder=data/'period_models';folder.mkdir(exist_ok=True);results={}
    for league in leagues:
        all_rows,_=feature_history(games,league)
        for scope in selected_scopes:
            rows=[r for r in all_rows if scope in r[0].context.get('segment_scores',{})]
            if len(rows)<600:continue
            dates=sorted({parse_time(g.start).date().isoformat() for g,_ in rows})
            selection_date=dates[int(len(dates)*.6)];test_date=dates[int(len(dates)*.8)]
            days=np.array([parse_time(g.start).date().isoformat() for g,_ in rows]);train=days<selection_date;selection=(days>=selection_date)&(days<test_date);test=days>=test_date
            x=np.array([x for _,x in rows]);y=np.array([[g.context['segment_scores'][scope][s] for s in ('home','away')] for g,_ in rows],dtype=float)
            inputs=capture_inputs(data,[g for g,_ in rows],league,scope,
                matrix=x,targets=y,partitions=['train' if a else 'selection' if b else 'test' for a,b in zip(train,selection)],
                recipe={'neighbours':[64,128,256,512],**({'joint_calibration':'selection-joint-tilt-v1'} if calibrate_joint else {})},metadata={'training_digest':digest(rows,scope)})
            tests=probes(y[train]);trials=[]
            for k in (64,128,256,512):
                model=PeriodModel(league,scope,k).fit(x[train],y[train]);metrics,_=evaluate(model,x[selection],y[selection],tests)
                trials.append((metrics['mean_brier'],model))
            _,model=min(trials,key=lambda p:p[0])
            calibration=model.calibrate_joint(x[selection],y[selection]) if calibrate_joint else None
            metrics,loss=evaluate(model,x[test],y[test],tests)
            validated=[k for k,m in metrics['families'].items() if m['brier']<=m['baseline_brier']+.0025 and m['max_probability_bias']<=.04]
            if 'three_way' in validated:validated.append('moneyline')
            source_digest=digest(rows,scope);code_hash=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
            version=f'{league}-{scope}-{PROTOCOL}-{source_digest[:12]}-{code_hash[:8]}'
            target=folder/f'{league}-{scope}.joblib';old=target.read_bytes() if target.exists() else None
            gate={'promote':bool(validated and int(test.sum())>=150),'minimum_test_games':150,'max_probability_bias':.04,
                  'max_brier_regression':.0025,'reason':'Separate chronological recipe selection and held-out family diagnostics; not a profit test.'}
            if old:
                champion=joblib.load(io.BytesIO(old))
                if parse_time(champion.report['fit_through'])>=parse_time(test_date):gate.update(promote=False,reason='Champion fit overlaps comparison period.')
                else:
                    _,prior_loss=evaluate(champion,x[test],y[test],tests)
                    blocks=defaultdict(list)
                    for (g,_),delta in zip([r for r,b in zip(rows,test) if b],loss-prior_loss):
                        d=parse_time(g.start).isocalendar();blocks[(d.year,d.week)].append(float(delta))
                    rng=np.random.default_rng(929);values=[np.array(v) for v in blocks.values()]
                    draws=[np.concatenate([values[i] for i in rng.integers(len(values),size=len(values))]).mean() for _ in range(500)]
                    ci=list(map(float,np.quantile(draws,[.025,.975])));gate.update(comparison_weeks=len(values),delta_brier_ci95=ci)
                    gate['promote']=gate['promote'] and len(values)>=12 and ci[1]<0
            model.report={'league':league,'scope':scope,'version':version,'trained_at':utcnow().isoformat(),
                'fit_through':max(g.start for (g,_),b in zip(rows,train|selection if calibrate_joint else train) if b),
                'train_fit_through':max(g.start for (g,_),b in zip(rows,train) if b),
                'calibration_fit_through':max(g.start for (g,_),b in zip(rows,selection) if b) if calibrate_joint else None,
                'test_from':test_date,
                'training_games':len(rows),'training_inputs':inputs,'partitions':{'train':int(train.sum()),'selection':int(selection.sum()),'test':int(test.sum())},
                'features':list(feature_names(league)),'odds_used':False,'code_sha256':code_hash,'training_data_fingerprint':source_digest,
                'model_family':'Conditional empirical joint scores','joint_calibration':calibration,'neighbours':model.k,'test':metrics,'validated_markets':validated,
                'promotion_gate':gate,'profit_backtest':None,'selection_trials':[{'neighbours':m.k,'brier':v} for v,m in trials],
                'limitations':['Uses observed scores in this exact period, never scaled full-game targets.',
                    'Historical sport features condition an empirical score distribution; player availability is not yet quantified.',
                    'Sparse exact-score tails and unseen score support are not validated. Retrospective scoring is not proof of betting profit.']}
            if (target.read_bytes() if target.exists() else None)!=old:gate.update(promote=False,reason='Champion changed during fitting.')
            check_games=[Game(**g) for g in json.loads((data/'games.json').read_text())];attach_scores(check_games,read_scores(data))
            ids={g.id for g,_ in rows};check_rows,_=feature_history(check_games,league)
            check_rows=[r for r in check_rows if r[0].id in ids and scope in r[0].context.get('segment_scores',{})]
            if digest(check_rows,scope)!=source_digest:gate.update(promote=False,reason='Training evidence changed during fitting.')
            archive=folder/'versions'/(version+'-'+utcnow().strftime('%Y%m%dT%H%M%S%fZ'))
            buf=io.BytesIO();joblib.dump(model,buf);atomic_bytes(archive/'model.joblib',buf.getvalue());atomic_json(archive/'report.json',model.report)
            bind_archive(data,archive,model.report)
            if gate['promote']:atomic_bytes(target,buf.getvalue());atomic_json(target.with_suffix('.json'),model.report)
            results[f'{league}:{scope}']=model.report
    atomic_json(folder/'latest_training.json',{'checked_at':utcnow().isoformat(),'models':results})
    return results
