"""Read-only accounting evidence grouped by game, never a profit guarantee.

Related selections are one game cluster. Confidence intervals resample whole
event dates to retain within-day dependencies; they are descriptive and assume
the observed dates represent future conditions. No sizing or publishing gate is
changed by this report.
"""
from collections import defaultdict,Counter
import math
import numpy as np
from zoneinfo import ZoneInfo
from .domain import parse_time

MIN_GAMES=30
MIN_DAYS=14


def economic_evidence(decisions, *, accrued_system_cost=None, timezone='Europe/Budapest'):
    games={};open_games={};segments={};excluded=[];seen=set()
    for row in decisions:
        tip=row.get('payload',{});identity=row.get('family') or row.get('tip_id')
        try:
            if not identity or identity in seen:raise ValueError('missing_or_duplicate_decision_identity')
            seen.add(identity)
            game_id=tip['game_id'];stake=float(tip['stake'])
            if not game_id or not math.isfinite(stake) or stake<=0:raise ValueError('invalid_stake_or_game')
            date=parse_time(tip['start']).astimezone(ZoneInfo(timezone)).date().isoformat()
            if row['status']=='open':
                g=open_games.setdefault(game_id,{'game_id':game_id,'league':tip.get('league'),
                    'matchup':tip.get('away','')+' @ '+tip.get('home',''),'stake':0.,'decisions':0})
                g['stake']+=stake;g['decisions']+=1
                continue
            if row['status'] not in ('won','lost','push','void','manual'):
                raise ValueError('unknown_settlement_status')
            pnl=float(row['pnl'])
            if not math.isfinite(pnl) or pnl < -stake-1e-8:raise ValueError('invalid_settlement_pnl')
            g=games.setdefault(game_id,{'game_id':game_id,'date':date,'stake':0.,'pnl':0.,'decisions':0})
            g['stake']+=stake;g['pnl']+=pnl;g['decisions']+=1
            key=(tip.get('league'),tip.get('market'),tip.get('model_version'))
            s=segments.setdefault(key,{'league':key[0],'market':key[1],'model_version':key[2],
                'settled_decisions':0,'game_ids':set(),'stake':0.,'pnl':0.,'outcomes':Counter()})
            s['settled_decisions']+=1;s['game_ids'].add(game_id);s['stake']+=stake;s['pnl']+=pnl;s['outcomes'][row['status']]+=1
        except (ValueError,TypeError,KeyError,OverflowError) as exc:
            excluded.append({'decision':identity,'reason':str(exc)})
    dates=defaultdict(lambda:[0.,0.])
    for g in games.values():dates[g['date']][0]+=g['stake'];dates[g['date']][1]+=g['pnl']
    stake=sum(g['stake'] for g in games.values());pnl=sum(g['pnl'] for g in games.values())
    enough=len(games)>=MIN_GAMES and len(dates)>=MIN_DAYS
    interval=None
    if enough and not excluded:
        blocks=np.array([dates[d] for d in sorted(dates)],dtype=float)
        rng=np.random.default_rng(729)
        totals=blocks[rng.integers(0,len(blocks),size=(2000,len(blocks)))].sum(axis=1)
        interval=list(map(float,np.quantile(totals[:,1]/totals[:,0],[.025,.975])))
    cumulative=0.;peak=0.;drawdown=0.;curve=[]
    for day,(turnover,profit) in sorted(dates.items()):
        cumulative+=profit;peak=max(peak,cumulative);drawdown=max(drawdown,peak-cumulative)
        curve.append({'date':day,'stake':turnover,'pnl':profit,'cumulative_pnl':cumulative})
    cost=accrued_system_cost
    if cost is not None and (not isinstance(cost,(int,float)) or not math.isfinite(cost) or cost<0):
        raise ValueError('Invalid system cost')
    state='invalid_records' if excluded else 'insufficient_independent_results' if not enough else 'costs_unknown' if cost is None else 'observed_positive_net' if pnl-cost>0 else 'observed_nonpositive_net'
    return {'version':'economic-evidence-v1','state':state,'profit_established':False,
        'settled_decisions':sum(g['decisions'] for g in games.values()),'settled_games':len(games),
        'event_dates':len(dates),'settled_stake':stake,'gross_pnl':pnl,'gross_roi':pnl/stake if stake else None,
        'accrued_system_cost':cost,'net_pnl':pnl-cost if cost is not None else None,
        'gross_roi_interval95':interval,'minimum_games_for_interval':MIN_GAMES,'minimum_dates_for_interval':MIN_DAYS,
        'interval_basis':'Whole Budapest event dates resampled; related selections are not independent trials. Descriptive retrospective interval, not future profit validation.',
        'drawdown':drawdown,'drawdown_basis':'Cumulative settled decision P&L ordered by event date; not an account cash-balance drawdown.',
        'daily_results':curve,'segments':[{**s,'game_ids':sorted(s['game_ids']),'settled_games':len(s['game_ids']),
            'outcomes':dict(s['outcomes']),'roi':s['pnl']/s['stake']} for s in segments.values()],
        'open_stake':sum(g['stake'] for g in open_games.values()),'open_games':len(open_games),
        'exposure':sorted(open_games.values(),key=lambda g:(-g['stake'],g['game_id'])),
        'excluded_records':excluded,
        'execution_basis':'Original issued odds/stakes and sports-score settlement; account execution and fees are not verified.',
        'note':'Several wins on one game do not establish repeatable profitability. Missing costs remain unknown; this report never increases stakes.'}
