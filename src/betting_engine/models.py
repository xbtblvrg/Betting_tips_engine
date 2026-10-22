"""Chronological, odds-independent team model and out-of-time evaluation.

Initial research model: not a player-level or in-play model. The report preserves
its limitations; additional features must pass the same chronological evaluation.
"""
from __future__ import annotations
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
import hashlib,json,math,heapq
from pathlib import Path
import numpy as np
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression,Ridge
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import log_loss,brier_score_loss,mean_absolute_error
from .domain import Game,parse_time,utcnow
from .context import STAT_PRIORS

FEATURES=('elo_difference','home_attack','home_defense','away_attack','away_defense','recent_margin_difference','home_rest','away_rest','rest_difference','home_games','away_games','neutral','playoff','league_scoring')
# Explicit allowlist: neither odds nor lines nor market-derived ratings are input.
PRIORS={'NBA':112.,'WNBA':80.,'MLB':4.5,'NHL':3.,'NFL':23.,'NCAAF':28.,'NCAAM':72.,'NCAAW':67.,'MLS':1.4,'EPL':1.4,'UCL':1.45,'ATP':22.5,'WTA':21.0,'CHALLENGER':22.0}
WINDOW={'NBA':20,'WNBA':15,'MLB':30,'NHL':20,'NFL':8,'NCAAF':8,'NCAAM':20,'NCAAW':20,'MLS':15,'EPL':15,'UCL':10,'ATP':15,'WTA':15,'CHALLENGER':15}
HOME_ADVANTAGE={'NFL':48.,'NCAAF':80.,'NCAAM':70.,'NCAAW':70.,'NBA':60.,'WNBA':60.,'MLB':25.,'NHL':35.,'MLS':65.,'EPL':65.,'UCL':50.,'ATP':0.,'WTA':0.,'CHALLENGER':0.}
PITCHER_FEATURES=('starter_earned_runs_per_nine','starter_whip','starter_strikeout_fraction','starter_walk_fraction','starter_home_run_fraction','starter_observed_batters','starter_known')

# Fixed, reviewable hypotheses. Recipe selection belongs to the earlier
# development period, never to the final champion-comparison outcomes.
RECIPES={
    'baseline':{'logistic_c':.3,'calibration_c':.1,'leaves':7,'l2':8.,'ridge':80.},
    'flexible_calibration':{'logistic_c':.3,'calibration_c':1.,'leaves':7,'l2':8.,'ridge':80.},
    'strong_calibration':{'logistic_c':.3,'calibration_c':.03,'leaves':7,'l2':8.,'ridge':80.},
    'regularized':{'logistic_c':.1,'calibration_c':.3,'leaves':7,'l2':24.,'ridge':200.},
    'richer_interactions':{'logistic_c':.3,'calibration_c':.3,'leaves':15,'l2':16.,'ridge':40.},
    'elo_anchor':{'logistic_c':.3,'calibration_c':1.,'leaves':7,'l2':8.,'ridge':80.},
    'identity_calibration':{'logistic_c':.3,'calibration_c':.1,'leaves':7,'l2':8.,'ridge':80.,'calibration_mode':'identity'},
    # A transparent odds-independent fallback.  The deployed NFL report
    # showed the historical ensemble regressing against its own Elo
    # comparator, so this recipe keeps the exact Elo probability and skips
    # outcome-fitted calibration.  It is promoted through the normal paired
    # gate; it is not an EvidenceGuard bypass.
    'elo_identity':{'logistic_c':.3,'calibration_c':.1,'leaves':7,'l2':8.,'ridge':80.,'calibration_mode':'identity'},
}

def feature_names(league):
    return FEATURES+tuple(f'{side}_{name}' for side in ('home','away') for name in STAT_PRIORS[league])+('home_box_observations','away_box_observations')+(tuple(f'{side}_{name}' for side in ('home','away') for name in PITCHER_FEATURES) if league=='MLB' else ())

def clipped(p):return np.clip(np.asarray(p,dtype=float),.005,.995)
def logit(p):
    p=clipped(p);return np.log(p/(1-p))

def metrics(y,p):
    p=clipped(p);y=np.asarray(y)
    bins=[]
    for low in np.arange(0,1,.1):
        mask=(p>=low)&(p<low+.1)
        if mask.any():bins.append({'low':round(float(low),1),'n':int(mask.sum()),'predicted':float(p[mask].mean()),'observed':float(y[mask].mean())})
    return {'n':len(y),'log_loss':float(log_loss(y,p,labels=[0,1])),'brier':float(brier_score_loss(y,p)),'accuracy':float(np.mean((p>=.5)==y)),'calibration':bins}

class FormState:
    def __init__(self,league):
        self.league=league;self.teams={};self.pitchers={};self.base=PRIORS[league];self.n=0
    def team(self,key):
        if key not in self.teams:self.teams[key]={'elo':1500.,'for':self.base,'against':self.base,'margin':0.,'n':0,'last':None,'stats':dict(STAT_PRIORS[self.league]),'box_n':0}
        return self.teams[key]
    def features(self,g):
        h,a=self.team(g.home_id),self.team(g.away_id);dt=parse_time(g.start)
        rest=lambda t: max(0.,(dt-parse_time(t['last'])).total_seconds()/86400) if t['last'] else 30.
        hr,ar=rest(h),rest(a)
        def form(t,r):
            shrink=.5 if r>60 else 1.
            return ((t['elo']-1500)*shrink+1500,self.base+(t['for']-self.base)*shrink,self.base+(t['against']-self.base)*shrink,t['margin']*shrink)
        he,hf,ha,hm=form(h,hr);ae,af,aa,am=form(a,ar)
        vals=[(he-ae)/400,hf,ha,af,aa,hm-am,min(hr,14),min(ar,14),min(hr,14)-min(ar,14),math.log1p(h['n']),math.log1p(a['n']),float(g.neutral),float(g.game_type=='playoff'),self.base]
        for t,r in ((h,hr),(a,ar)):
            vals.extend(prior+(t['stats'][key]-prior)*(.5 if r>60 else 1.) for key,prior in STAT_PRIORS[self.league].items())
        vals.extend([math.log1p(h['box_n']),math.log1p(a['box_n'])])
        if self.league=='MLB':
            for side in ('home','away'):
                pid=str((g.context.get(side+'_pitcher') or {}).get('id',''))
                t=self.pitchers.get(pid,{})
                elapsed=(dt-parse_time(t['last'])).days if t.get('last') else 0
                decay=.5 if elapsed>100 else 1.
                get=lambda k:t.get(k,0)*decay
                outs=get('outs')+60;bf=get('battersFaced')+90
                vals.extend([(get('earnedRuns')+10)*27/outs,(get('hits')+get('baseOnBalls')+27)*3/outs,(get('strikeOuts')+20)/bf,(get('baseOnBalls')+7.2)/bf,(get('homeRuns')+2.7)/bf,math.log1p(get('battersFaced')),float(bool(pid))])
        return np.asarray(vals,dtype=float)
    def update(self,g):
        if g.state!='final':raise ValueError('Only final scores update form')
        h,a=self.team(g.home_id),self.team(g.away_id);hs,ass=g.home_score,g.away_score
        for t in (h,a):
            if t['last'] and (parse_time(g.start)-parse_time(t['last'])).days>60:
                t['elo']=1500+(t['elo']-1500)*.5
                for key in ('for','against'):t[key]=self.base+(t[key]-self.base)*.5
                t['margin']*=.5
                for key,prior in STAT_PRIORS[self.league].items():t['stats'][key]=prior+(t['stats'][key]-prior)*.5
        hfa = 0. if g.neutral else HOME_ADVANTAGE.get(self.league, 50.)
        ph=1/(1+10**((a['elo']-h['elo']-hfa)/400))
        actual=1. if hs>ass else (0. if hs<ass else .5)
        k=20 if self.league!='NFL' else 32
        h['elo']+=k*(actual-ph);a['elo']-=k*(actual-ph)
        alpha=2/(WINDOW[self.league]+1)
        for t,sc,opp in [(h,hs,ass),(a,ass,hs)]:
            t['for']+=(sc-t['for'])*alpha;t['against']+=(opp-t['against'])*alpha;t['margin']+=(sc-opp-t['margin'])*alpha;t['n']+=1;t['last']=g.start
        for side,t in (('home',h),('away',a)):
            observed=g.context.get('box',{}).get(side,{})
            if observed and all(k in observed for k in STAT_PRIORS[self.league]):
                for key in STAT_PRIORS[self.league]:t['stats'][key]+=alpha*(observed[key]-t['stats'][key])
                t['box_n']+=1
        if self.league=='MLB':
            for row in g.context.get('pitching_observations',[]):
                pid=row['pitcher_id'];t=self.pitchers.setdefault(pid,{})
                decay=.5 if t.get('last') and (parse_time(g.start)-parse_time(t['last'])).days>100 else .95
                for key in ('outs','earnedRuns','hits','strikeOuts','baseOnBalls','homeRuns','battersFaced'):t[key]=t.get(key,0)*decay+row[key]
                t['last']=g.start
        self.n+=1;self.base+=((hs+ass)/2-self.base)/min(self.n+30,500)

def feature_history(games,league,as_of=None):
    cutoff=as_of or utcnow();state=FormState(league);rows=[];pending=[]
    history=sorted((g for g in games if g.league==league and g.state=='final' and g.game_type!='preseason' and parse_time(g.start)<cutoff and not g.context.get('resumed_game')),key=lambda g:(parse_time(g.start),g.id))
    for g in history:
        start=parse_time(g.start)
        while pending and pending[0][0]<=start:
            _,_,_,observed=heapq.heappop(pending);state.update(observed)
        rows.append((g,state.features(g)))
        # Final timestamps are missing from these retrospective datasets. Use a
        # conservative 8-hour delay and never same-UTC-day outcomes. This also
        # blocks cross-midnight leakage from games that had not finished yet.
        next_day=start.replace(hour=0,minute=0,second=0,microsecond=0)+timedelta(days=1)
        available=max(start+timedelta(hours=8),next_day)
        heapq.heappush(pending,(available,start,g.id,g))
    while pending and pending[0][0]<=cutoff:
        _,_,_,observed=heapq.heappop(pending);state.update(observed)
    return rows,state

@dataclass
class Forecast:
    game_id:str
    league:str
    home_probability:float
    away_probability:float
    draw_probability:float
    home_points:float
    away_points:float
    margin:float
    total:float
    model_probabilities:dict
    disagreement:float
    features:dict
    model_version:str
    warnings:list
    residuals:np.ndarray
    def public(self):
        return {k:v for k,v in self.__dict__.items() if k!='residuals'}
    def market_probability(self,kind,side,line=None):
        if ':' in kind:raise ValueError('A full-game forecast cannot price a segment contract')
        if kind=='moneyline':
            p=self.home_probability if side=='home' else self.away_probability
            return min(1.0, max(0.0, float(p))), min(max(0.0, 1.0 - p), max(0.0, float(self.draw_probability)))
        home,away,weights=score_distribution(self.margin,self.total,self.residuals,self.home_probability,self.draw_probability,self.league)
        from .contracts import contract_value
        if line is not None and round(float(line) * 2) != float(line) * 2 and kind in ('spreads', 'totals'):
            from .contracts import decompose_quarter_line
            l1, l2 = decompose_quarter_line(line)
            v1 = contract_value(kind, side, l1, home, away)
            v2 = contract_value(kind, side, l2, home, away)
            win_frac = ((v1 > 0).astype(float) + (v2 > 0).astype(float)) / 2.0
            push_frac = ((v1 == 0).astype(float) + (v2 == 0).astype(float)) / 2.0
            p = min(1.0, max(0.0, float((weights * win_frac).sum())))
            push = min(max(0.0, 1.0 - p), max(0.0, float((weights * push_frac).sum())))
            return p, push
        value=contract_value(kind,side,line,home,away)
        p = min(1.0, max(0.0, float(weights[value>0].sum())))
        push = min(max(0.0, 1.0 - p), max(0.0, float(weights[value==0].sum())))
        return p, push

def score_distribution(margin,total,residuals,home_probability,draw_probability,league):
    """Integer, nonnegative paired scores, reconciled to calibrated win mass.

    Conditional shape is empirical; calibration of ML is not evidence of
    calibration for every alternate spread/total. Report these separately.
    """
    margins=margin+residuals[:,0];totals=total+residuals[:,1]
    home=np.maximum(0,np.rint((totals+margins)/2));away=np.maximum(0,np.rint((totals-margins)/2))
    weights=np.ones(len(home),dtype=float)
    if league == 'NHL':
        diff = home - away
        h_lead1 = diff == 1
        a_lead1 = diff == -1
        h_lead2 = diff == 2
        a_lead2 = diff == -2
        tied = diff == 0
        other = (~h_lead1) & (~a_lead1) & (~h_lead2) & (~a_lead2) & (~tied)
        new_home, new_away, new_weights = [home[other]], [away[other]], [weights[other]]
        if np.any(h_lead1):
            h1, a1, w1 = home[h_lead1], away[h_lead1], weights[h_lead1]
            new_home.append(h1 + 1); new_away.append(a1); new_weights.append(w1 * 0.2184)
            new_home.append(h1 + 1); new_away.append(a1 + 1); new_weights.append(w1 * (0.0858 * 0.52))
            new_home.append(h1); new_away.append(a1 + 2); new_weights.append(w1 * (0.0858 * 0.48))
            new_home.append(h1); new_away.append(a1); new_weights.append(w1 * 0.6958)
        if np.any(a_lead1):
            h1, a1, w1 = home[a_lead1], away[a_lead1], weights[a_lead1]
            new_home.append(h1); new_away.append(a1 + 1); new_weights.append(w1 * 0.2184)
            new_home.append(h1 + 2); new_away.append(a1); new_weights.append(w1 * (0.0858 * 0.52))
            new_home.append(h1 + 1); new_away.append(a1 + 1); new_weights.append(w1 * (0.0858 * 0.48))
            new_home.append(h1); new_away.append(a1); new_weights.append(w1 * 0.6958)
        if np.any(h_lead2):
            h2, a2, w2 = home[h_lead2], away[h_lead2], weights[h_lead2]
            new_home.append(h2 + 1); new_away.append(a2); new_weights.append(w2 * 0.24)
            new_home.append(h2); new_away.append(a2 + 1); new_weights.append(w2 * 0.08)
            new_home.append(h2); new_away.append(a2); new_weights.append(w2 * 0.68)
        if np.any(a_lead2):
            h2, a2, w2 = home[a_lead2], away[a_lead2], weights[a_lead2]
            new_home.append(h2); new_away.append(a2 + 1); new_weights.append(w2 * 0.24)
            new_home.append(h2 + 1); new_away.append(a2); new_weights.append(w2 * 0.08)
            new_home.append(h2); new_away.append(a2); new_weights.append(w2 * 0.68)
        if np.any(tied):
            ht, at, wt = home[tied], away[tied], weights[tied]
            new_home.append(ht + 1); new_away.append(at); new_weights.append(wt * 0.52)
            new_home.append(ht); new_away.append(at + 1); new_weights.append(wt * 0.48)
        home = np.concatenate(new_home)
        away = np.concatenate(new_away)
        weights = np.concatenate(new_weights)
    elif league not in ('NFL', 'MLS', 'EPL', 'UCL'):
        ties=home==away;h0=home[ties];a0=away[ties]
        home=np.concatenate([home[~ties],h0+1,h0]);away=np.concatenate([away[~ties],a0,a0+1]);weights=np.concatenate([weights[~ties],np.full(len(h0),.5),np.full(len(h0),.5)])
    # Small explicit support regularizer for absent outcome classes, not invented observations.
    center=max(1,int(round(total/2)))
    for h,a,p in ((center+1,center,home_probability),(center,center+1,1-home_probability-draw_probability),(center,center,draw_probability)):
        mask=(home>away) if h>a else ((home<away) if h<a else home==away)
        if p>0 and not mask.any():home=np.append(home,h);away=np.append(away,a);weights=np.append(weights,1.)
    for mask,p in ((home>away,home_probability),(home<away,1-home_probability-draw_probability),(home==away,draw_probability)):
        mass=weights[mask].sum()
        if mass:weights[mask]*=max(0,p)/mass
    return home,away,weights/weights.sum()

class EloClassifier:
    """Fixed transparent rating baseline, also eligible in the validation blend."""
    def fit(self,x,y):return self
    def predict_proba(self,x):
        x=np.asarray(x);p=clipped(1/(1+10**(-x[:,0]-.125*(1-x[:,11]))))
        return np.column_stack([1-p,p])


class IdentityLogitCalibrator:
    """Predeclared uncalibrated control; no outcome-dependent parameters."""
    def fit(self,x,y):return self
    def predict_proba(self,x):
        values=np.asarray(x,dtype=float)
        if values.ndim!=2 or values.shape[1]!=1 or not np.isfinite(values).all():
            raise ValueError('Identity calibration requires finite one-column logits')
        # Inputs use the same clipped logit representation as fitted calibration.
        p=1/(1+np.exp(-np.clip(values[:,0],-700,700)))
        return np.column_stack([1-p,p])

class LeagueModel:
    def __init__(self,league,recipe='baseline'):
        if recipe not in RECIPES:raise ValueError('Unknown model recipe')
        self.league=league;self.recipe=recipe
    def estimators(self):
        r=RECIPES[getattr(self,'recipe','baseline')]
        return {'elo':EloClassifier(),'logistic':make_pipeline(StandardScaler(),LogisticRegression(C=r['logistic_c'],max_iter=500)),
                'boosting':HistGradientBoostingClassifier(max_iter=140,max_leaf_nodes=r['leaves'],l2_regularization=r['l2'],learning_rate=.04,min_samples_leaf=35,early_stopping=False,random_state=17)}
    def fit(self,games,as_of=None):
        recipe=RECIPES[self.recipe]
        rows,self.state=feature_history(games,self.league,as_of)
        rows=[(g,x) for g,x in rows if x[9]>=math.log1p(5) and x[10]>=math.log1p(5)]
        if len(rows)<240:raise ValueError(f'{self.league}: only {len(rows)} usable historical games')
        allx=np.stack([x for g,x in rows]);scores=np.array([[g.home_score-g.away_score,g.home_score+g.away_score] for g,x in rows]);allg=[g for g,x in rows]
        # Entire dates belong to a single partition; no split across one event/day.
        days=sorted(set(parse_time(g.start).date() for g in allg));d1=days[int(len(days)*.60)];d2=days[int(len(days)*.75)];d3=days[int(len(days)*.85)]
        dates=np.array([parse_time(g.start).date() for g in allg]);train=dates<d1;tune=(dates>=d1)&(dates<d2);cal=(dates>=d2)&(dates<d3);test=dates>=d3
        y=(scores[:,0]>0).astype(int);not_tie=scores[:,0]!=0
        def fit_class(mask):
            est=self.estimators()
            for model in est.values():model.fit(allx[mask&not_tie],y[mask&not_tie])
            return est
        initial=fit_class(train);tune_scores={name:brier_score_loss(y[tune&not_tie],model.predict_proba(allx[tune&not_tie])[:,1]) for name,model in initial.items()}
        # Selection uses tune only, never the final test.
        tune_y=y[tune&not_tie];tune_predictions={name:m.predict_proba(allx[tune&not_tie])[:,1] for name,m in initial.items()}
        candidates=[{'logistic':a/4,'boosting':b/4,'elo':1-(a+b)/4} for a in range(5) for b in range(5-a)]
        if self.recipe in ('elo_anchor','elo_identity'):candidates=[{'logistic':0.,'boosting':0.,'elo':1.}]
        self.weights=min(candidates,key=lambda w:brier_score_loss(tune_y,sum(w[name]*p for name,p in tune_predictions.items())))
        self.weight=self.weights['logistic']
        frozen=fit_class(train|tune)
        blend=lambda models,x:sum(self.weights[name]*model.predict_proba(x)[:,1] for name,model in models.items())
        rawcal=blend(frozen,allx[cal&not_tie])
        calibrator=(IdentityLogitCalibrator() if recipe.get('calibration_mode')=='identity'
                    else LogisticRegression(C=recipe['calibration_c'],max_iter=500))
        self.calibrator=calibrator.fit(logit(rawcal).reshape(-1,1),y[cal&not_tie])
        ptest=self.calibrator.predict_proba(logit(blend(frozen,allx[test&not_tie])).reshape(-1,1))[:,1]
        # Score model and residual pool are also fitted without the final test.
        frozen_score=make_pipeline(StandardScaler(),Ridge(alpha=recipe['ridge'])).fit(allx[train|tune],scores[train|tune])
        self.residuals=scores[cal]-frozen_score.predict(allx[cal]);scoretest=frozen_score.predict(allx[test])
        base=float(y[(train|tune)&not_tie].mean());evaluation=metrics(y[test&not_tie],ptest)
        baseline=metrics(y[test&not_tie],np.full(int(np.sum(test&not_tie)),base))
        # Paired weekly block bootstrap for Brier improvement, not row resampling.
        deltas=(ptest-y[test&not_tie])**2-(base-y[test&not_tie])**2
        testgames=np.array(allg,dtype=object)[test&not_tie];weeks=defaultdict(list)
        for g,delta in zip(testgames,deltas):
            week=parse_time(g.start).isocalendar();weeks[(week.year,week.week)].append(float(delta))
        blocks=[np.asarray(x) for x in weeks.values()];rng=np.random.default_rng(73)
        boots=[float(np.concatenate([blocks[i] for i in rng.integers(0,len(blocks),len(blocks))]).mean()) for _ in range(500)]
        low,high=np.quantile(boots,[.025,.975])
        fingerprint=hashlib.sha256(json.dumps([(g.id,g.start,g.home_score,g.away_score,g.context.get('box'),g.context.get('pitching_observations'),g.context.get('home_pitcher'),g.context.get('away_pitcher')) for g in allg],sort_keys=True).encode()).hexdigest()[:12]
        code_hash=hashlib.sha256(Path(__file__).read_bytes()+(Path(__file__).parent/'context.py').read_bytes()).hexdigest()
        self.version=f'{self.league}-sport-v4-{self.recipe}-{fingerprint}-{code_hash[:8]}'
        self.report={'league':self.league,'version':self.version,'code_sha256':code_hash,'training_data_fingerprint':fingerprint,'trained_at':utcnow().isoformat(),'training_games':len(rows),'first_game':allg[0].start,'last_game':allg[-1].start,'test_from':str(d3),'partitions':{'train':int(train.sum()),'selection':int(tune.sum()),'calibration':int(cal.sum()),'test':int(test.sum())},'features':list(feature_names(self.league)),'box_coverage':sum(bool(g.context.get('box')) for g in allg)/len(allg),'pitching_observation_coverage':sum(bool(g.context.get('pitching_observations')) for g in allg)/len(allg),'odds_used':False,'model_family':'sport efficiency + team form ensemble + coherent empirical score distribution','ensemble_weight_logistic':self.weight,'ensemble_weights':self.weights,'selection_brier':tune_scores,'test':evaluation,'baseline':baseline,'brier_delta_ci95':[float(low),float(high)],'beats_constant_baseline':bool(high<0),'score_mae':{'margin':float(mean_absolute_error(scores[test,0],scoretest[:,0])),'total':float(mean_absolute_error(scores[test,1],scoretest[:,1]))},'profit_backtest':None,'limitations':['Team-level model; injury, lineup and player-minute effects have not yet been trained.','Historical data may include later corrections. A complete archive of information available at each prediction time is not available.','Published forecasts use the same frozen parameters as the evaluation. Team form updates with new results.','Betting profitability has not been verified using historical executable odds.'],'test_predictions':[{'game_id':g.id,'date':g.start,'p':float(p),'result':int(g.home_score>g.away_score)} for g,p in zip(testgames,ptest)]}
        # A stronger odds-independent benchmark, computed on identical test events.
        testx=allx[test&not_tie];elo_p=1/(1+10**(-testx[:,0]-.125*(1-testx[:,11])))
        self.report['elo_baseline']=metrics(y[test&not_tie],elo_p)
        self.report.update(recipe=self.recipe,recipe_parameters=dict(recipe))
        self.report['calibration_method']=('identity_logit' if recipe.get('calibration_mode')=='identity' else 'logistic')
        self.report['beats_elo_brier']=evaluation['brier']<self.report['elo_baseline']['brier']
        # Deploy exactly the evaluated parameter fit; update observed team form separately.
        self.models=frozen;self.score_model=frozen_score
        self.draw_rate=float(np.mean(scores[train|tune,0]==0)) if self.league in ('NFL', 'MLS', 'EPL', 'UCL') else 0.
        # Synthetic, model-centered thresholds: probability diagnostics, NOT a betting ROI backtest.
        all_ptest=self.calibrator.predict_proba(logit(blend(frozen,allx[test])).reshape(-1,1))[:,1]
        market_y={k:[] for k in ('spreads','totals','home_total','away_total')};market_p={k:[] for k in market_y}
        for mu,actual,pwin in zip(scoretest,scores[test],all_ptest):
            hs,aws,w=score_distribution(mu[0],mu[1],self.residuals,pwin*(1-self.draw_rate),self.draw_rate,self.league)
            ml=float(np.floor(mu[0])+.5);tl=float(np.floor(mu[1])+.5)
            market_p['spreads'].append(float(w[hs-aws>ml].sum()));market_y['spreads'].append(int(actual[0]>ml))
            market_p['totals'].append(float(w[hs+aws>tl].sum()));market_y['totals'].append(int(actual[1]>tl))
            for kind,predicted,observed,samples in [('home_total',(mu[1]+mu[0])/2,(actual[1]+actual[0])/2,hs),('away_total',(mu[1]-mu[0])/2,(actual[1]-actual[0])/2,aws)]:
                threshold=float(np.floor(predicted)+.5)
                market_p[kind].append(float(w[samples>threshold].sum()));market_y[kind].append(int(observed>threshold))
        self.report['score_market_diagnostics']={k:metrics(market_y[k],market_p[k]) for k in market_y}
        self.report['score_market_diagnostics_note']='Half-point thresholds around model projections; these are not historical sportsbook lines or an ROI backtest.'
        self.report['deployment_policy']='same_frozen_parameters_as_evaluation'
        self.report['evaluation_status']='retrospective_development_evaluation; prospective validation pending'
        self.report['result_availability_policy']='max(game_start + 8h, next UTC midnight); resumed games excluded; source revision history unavailable'
        return self
    def forecast(self,g):
        if g.league!=self.league:raise ValueError('Wrong league')
        if g.state!='scheduled' or parse_time(g.start)<=utcnow():raise ValueError('Pregame model cannot price live/finished events')
        x=self.state.features(g);ps={name:float(m.predict_proba(x[None,:])[0,1]) for name,m in self.models.items()}
        raw=sum(self.weights[name]*p for name,p in ps.items());p=float(self.calibrator.predict_proba(np.array([[float(logit(raw))]]))[0,1]);p=float(np.clip(p,.02,.98))
        mu=self.score_model.predict(x[None,:])[0];margin,total=map(float,mu)
        from .injury_impact import calculate_injury_impact
        injury=g.context.get('injuries',{})
        injury_adj=calculate_injury_impact(g.league,injury) if injury else None
        warnings=[]
        if injury_adj and injury_adj.get('has_impact'):
            net_home_adv=float(injury_adj.get('net_home_advantage',0.0))
            home_pen=float(injury_adj.get('home_penalty_points',0.0))
            away_pen=float(injury_adj.get('away_penalty_points',0.0))
            margin+=net_home_adv
            total=max(1.0,total-(home_pen+away_pen)*0.5)
            logit_mult={'NFL':0.12,'NCAAF':0.10,'NBA':0.08,'WNBA':0.09,'NHL':0.70,'MLB':0.50}.get(g.league,0.10)
            raw_logit=float(logit(p))+(net_home_adv*logit_mult)
            p=float(1/(1+math.exp(-raw_logit)))
            p=float(np.clip(p,.02,.98))
            warnings.append(f"Quantitative injury/lineup adjustment applied: Net home advantage delta = {net_home_adv:+.2f} pts/goals.")
        elif any(injury.get(side) for side in ('home','away')):
            warnings.append('Players appear on the injury report. Evaluated with minor/zero point delta.')
        if g.league=='MLB' and not all(g.context.get(side+'_pitcher') for side in ('home','away')):warnings.append('One or both starting pitchers are unconfirmed. The model uses a league-average prior where needed.')
        if g.game_type=='preseason':warnings.append('Preseason: provisional extrapolation from regular/postseason team history; preseason calibration and rotations are unverified.')
        if injury.get('stale'):warnings.append('The injury report is more than one day old.')
        if min(x[9],x[10])<math.log1p(15):warnings.append('Limited team history; estimates are strongly shrunk toward the league average.')
        if self.report.get('beats_elo_brier') is False:warnings.append('The simple Elo baseline had a better Brier score on the chronological test. This model needs further validation.')
        if not self.report['beats_constant_baseline']:warnings.append('The match-winner model has not clearly outperformed the constant-probability baseline.')
        return Forecast(g.id,g.league,p*(1-self.draw_rate),(1-p)*(1-self.draw_rate),self.draw_rate,(total+margin)/2,(total-margin)/2,margin,total,ps,float(max(ps.values())-min(ps.values())),dict(zip(feature_names(self.league),map(float,x))),self.version,warnings,self.residuals)
