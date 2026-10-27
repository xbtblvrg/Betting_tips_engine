"""Scheduled, read-only wager exports into a separate frozen price-study store."""
from collections import Counter
import hashlib
import json
from pathlib import Path

from .captured_price_evaluation import CORE, FrozenCaptureReports, _relevant
from .domain import parse_time, utcnow
from .io_utils import atomic_json


def closed_cohorts(decisions, now=None):
    now = now or utcnow()
    groups = {}
    for decision in decisions:
        tip = decision['payload']
        start = parse_time(tip['start'])
        # Let the final pre-start refresh finish archiving before freezing.
        if (now-start).total_seconds() < 90:
            continue
        key = (tip['game_id'], start.isoformat())
        groups.setdefault(key, []).append({k: decision[k] for k in CORE})
    result = []
    for (game, start), rows in sorted(groups.items()):
        rows.sort(key=lambda d: d['family'])
        digest = hashlib.sha256(json.dumps(rows,sort_keys=True,allow_nan=False).encode()).hexdigest()
        result.append({'key': game+':'+start+':'+digest, 'game_id': game, 'start': start,
                       'decision_count': len(rows), 'decisions': rows})
    return result


def evaluate_game(store, data_dir, game_id, start, now=None):
    data_dir = Path(data_dir)
    all_decisions = store.system_decisions()
    cohorts = closed_cohorts(all_decisions, now)
    cohort = next((c for c in cohorts if c['game_id']==game_id and c['start']==start),None)
    if cohort is None:
        raise ValueError('No complete issued cohort after the start-time grace period')
    decisions = [d for d in all_decisions if d.get('payload',{}).get('game_id')==game_id and d.get('payload',{}).get('start')==start]
    decisions.sort(key=lambda d: d['family'])
    fixture_ids = set()
    for decision in decisions:
        parts = str(decision['payload'].get('reference_market_id','')).split(':')
        if len(parts)==4 and parts[0]=='stake':fixture_ids.add(parts[1])
    with store.db() as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='closing_price_captures'").fetchone()
        # Include conflicting league labels for the same provider fixture; the
        # evaluator must see them as blockers instead of silently dropping them.
        captures = [dict(r) for r in db.execute('SELECT * FROM closing_price_captures')
                    if any(_relevant(dict(r),d) for d in decisions)] if exists else []
    path = data_dir/'closing_studies.sqlite3'
    evidence = FrozenCaptureReports(path) if path.exists() else FrozenCaptureReports.create(path)
    study = evidence.freeze(decisions,captures,archive_root=data_dir,as_of=start)
    summary_path = data_dir/'closing_evaluation.json'
    previous = json.loads(summary_path.read_text()) if summary_path.exists() else {}
    reports = {r['family']:r for r in previous.get('reports',[])}
    for report in study['reports']:
        decision = report['decision_export'];tip=decision['payload'];price=report.get('closing_price')
        outcome_snapshot = {k: decision[k] for k in ('status', 'pnl', 'outcome_evidence') if k in decision}
        reports[decision['family']]={'family':decision['family'],'game_id':tip['game_id'],
            'league':tip['league'],'start':tip['start'],'matchup':tip['away']+' @ '+tip['home'],
            'market_key':tip['market'],'market_display':tip.get('stake_evidence',{}).get('market_name') or tip.get('market_display',tip['market']),'selection':tip.get('selection',tip.get('side','')),
            'issued_odds':tip['reference_odds'],'issued_stake':tip.get('stake'),'closing_odds':price['decimal_odds'] if price else None,
            'price_clv':report['price_clv'],'status':report['status'],'reason':report.get('reason'),
            'study_id':study['study_id'],'comparison_sha256':report.get('comparison_sha256'),
            'outcome_snapshot':outcome_snapshot,
            'lifecycle_basis':'Retained settlement evidence, not availability at the quote cutoff.'}
    rows = sorted(reports.values(),key=lambda r:(r['start'],r['family']),reverse=True)
    statuses = Counter(r['status'] for r in rows)
    summary={'checked_at':(now or utcnow()).isoformat(),'state':'active','enabled':True,
        'evaluated_decisions':len(rows),'evaluated_games':len({r['game_id'] for r in rows}),
        'status_counts':dict(statuses),'reports':rows,'latest_study_id':study['study_id'],
        'comparison_basis':'Last eligible exact-contract price observed in the final five minutes before scheduled start; not the guaranteed final bookmaker tick.',
        'profit_established':False,'note':'Price CLV is a price diagnostic, not realized ROI or verified account profit. Missing closing prices remain missing.'}
    atomic_json(summary_path,summary)
    return {'study_id':study['study_id'],'game_id':game_id,**study['coverage'],
            'conclusion':'Exact issued contracts evaluated; missing prices retained. No wagers or model parameters changed.'}
