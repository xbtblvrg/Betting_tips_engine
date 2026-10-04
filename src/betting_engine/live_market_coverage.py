"""Exact-title evidence projection of the existing catalogue, never a funding gate."""
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import json, math
from .domain import parse_time, utcnow
from .market_catalog import classify

LEAGUES = ('NFL', 'NHL', 'MLB', 'NBA', 'WNBA')


STAGES = (('data','Data'),('parsing','Parsing'),('model','Model'),('pricing','Pricing'),
          ('guard','Guard'),('selection','Selection'),('sizing','Sizing'),
          ('issuance','Issuance / tracking'),('settlement','Settlement'),('learning','Learning'))


def _stage(count, expected, basis):
    return {'state': 'verified' if expected > 0 and count >= expected else 'partial' if count else 'not_observed',
            'observed': count, 'expected': expected, 'basis': basis}


def _finite(value):
    return type(value) in (int,float) and math.isfinite(value)


def _key(row):
    title = row.get('market_name') or row.get('market')
    if isinstance(row.get('stake_market'), dict):
        title = row['stake_market'].get('name') or title
    title = (row.get('stake_evidence') or {}).get('market_name') or title
    # Exact source title defines scope. Model scope alone must not merge titles.
    section = row.get('section', row.get('provider_section', 'standard'))
    scope = classify({'market': title, 'provider_section': section})['scope']
    return row.get('league'), section, title, scope


def _fresh(stamp, now):
    try:
        return -5 <= (now - parse_time(stamp)).total_seconds() <= 300
    except (TypeError, ValueError, AttributeError):
        return False


def _learning_consumers(tip, size, publication):
    """Current guarded consumers bound to the published runtime snapshots."""
    from .outcome_sizing import verify_summary, segment_key
    from .calibration_uncertainty import segment_key as calibration_key
    from .anchor_learning import effective_weight
    result = dict.fromkeys(('segment', 'calibration', 'weights', 'sizing'), False)
    if (tip.get('decision_guard') or {}).get('passed') is not True:
        return result
    adjustment = size.get('outcome_adjustment') or {}
    source = adjustment.get('source') or {}
    status = publication.get('status') or {}
    anchor = tip.get('market_anchor') or {}
    def digest(value):
        return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)
    try:
        result['segment'] = (verify_summary(source) and source.get('independent_games', 0) > 0
            and source.get('segment') == segment_key(tip)
            and source.get('snapshot_sha256') == (status.get('outcome_sizing') or {}).get('snapshot_sha256'))
        result['sizing'] = (result['segment'] and adjustment.get('summary_sha256') == source.get('summary_sha256')
            and adjustment.get('snapshot_sha256') == source.get('snapshot_sha256')
            and _finite(adjustment.get('factor')) and _finite(adjustment.get('adjusted_amount')))
        calibration = tip.get('calibration_uncertainty') or {}
        result['calibration'] = (calibration.get('state') == 'measured'
            and calibration.get('segment') == calibration_key(tip['league'], tip['market'], tip.get('line'), tip['reference_odds'])
            and digest(calibration.get('snapshot_sha256'))
            and calibration['snapshot_sha256'] == (status.get('calibration_uncertainty') or {}).get('snapshot_sha256'))
        weight, receipt = effective_weight(publication.get('settings') or {}, tip['league'], tip['market'],
            tip.get('line'), tip.get('model_version'), anchor.get('probability_semantics'))
        result['weights'] = (bool(receipt) and receipt == anchor.get('learned_weight')
            and digest(receipt.get('proof_sha256')) and digest(receipt.get('snapshot_sha256'))
            and receipt['snapshot_sha256'] == ((status.get('market_anchor') or {}).get('learned_weights') or {}).get('snapshot_sha256')
            and weight == anchor.get('model_weight'))
    except (KeyError, TypeError, ValueError, ArithmeticError):
        pass
    return result


def build_live_coverage(publication, catalog, decisions=(), feedback_tip_ids=(), now=None):
    """Project observed titles plus exact current evidence and immutable decisions.

    decisions: dict rows from system_decisions (payload may be JSON or dict).
    feedback_tip_ids: IDs explicitly present in a successful feedback input;
    a recent worker heartbeat alone is not proof of feedback for a decision.
    Funding support means a guarded current candidate passes policy and minimum
    sizing before exposure. Ready-now separately requires executable funded
    selection and an unmarked checklist. Neither grants permanent title approval.
    """
    now = now or utcnow()
    current = _fresh(publication.get('generated_at'), now)
    def active(tip):
        try:
            return parse_time(tip.get('start')) > now and parse_time(tip.get('valid_until')) > now
        except (TypeError, ValueError, AttributeError):
            return False
    groups = {}
    for source in [*catalog.get('types', []), *publication.get('market_types', [])]:
        key = _key(source)
        if key[0] not in LEAGUES or not key[2]:
            continue
        groups.setdefault(key, {})
    live = {_key(row): row for row in publication.get('market_types', [])}
    funded = Counter()
    qualifying = Counter()
    evidence = defaultdict(Counter)
    current_ids = set()
    for tip in publication.get('evaluated_candidates', []):
        if not current or not active(tip) or not _fresh(tip.get('quote_at'),now):
            continue
        identity=tip.get('id')
        if not identity or identity in current_ids:continue
        current_ids.add(identity)
        counts=evidence[_key(tip)]
        counts['candidates']+=1
        if (tip.get('input_readiness') or {}).get('passed') is True:counts['data']+=1
        if tip.get('model_version') and tip.get('model_scope')==_key(tip)[3]:counts['model']+=1
        anchor=tip.get('market_anchor') or {}
        if (all(_finite(tip.get(k)) for k in ('probability','fair_odds','minimum_odds','expected_roi','stress_roi'))
                and anchor.get('status')=='usable'):counts['pricing']+=1
        if (tip.get('decision_guard') or {}).get('passed') is True:counts['guard']+=1
        if tip.get('allocation_code') and tip.get('family_selection_policy'):counts['selection']+=1

    from .funding_policy import funding_basis, size_recommendation
    settings = publication.get('settings', {})
    sized_ids = set()
    for tip in publication.get('evaluated_candidates', []):
        if not (current and active(tip) and _fresh(tip.get('quote_at'), now) and
                (tip.get('decision_guard') or {}).get('passed') is True and
                (tip.get('input_readiness') or {}).get('passed') is True):
            continue
        identity = tip.get('id')
        if not identity or identity in sized_ids:
            continue
        sized_ids.add(identity)
        try:
            basis = funding_basis(tip, settings)
            size = size_recommendation(tip, settings, float(settings['max_bet']), 0)
            evidence[_key(tip)]['sizing']+=1
            consumers = _learning_consumers(tip, size, publication)
            for consumer, observed in consumers.items():
                evidence[_key(tip)]['learning_' + consumer] += int(observed)
            evidence[_key(tip)]['learning_all'] += int(all(consumers.values()))
            if basis['eligible'] and 1 <= size['amount'] <= min(5, settings['max_bet']):
                qualifying[_key(tip)] += 1
        except (KeyError, TypeError, ValueError, ArithmeticError):
            continue
    from .dashboard_payload import _execution_counts
    if current:
        ready = _execution_counts(publication.get('recommendations', []) + publication.get('alternatives', []),
                                  publication.get('evaluated_candidates', []), publication.get('tracked_bets', []),
                                  publication.get('placement_marks', {}), {'max_bet': 5, **settings}, {}, now,
                                  include_ready_ids=True)['ready_tip_ids']
        tip_index = {t['id']: t for t in publication.get('recommendations', []) + publication.get('alternatives', [])}
        tip_index.update({b['tip_id']: b['payload'] for b in publication.get('tracked_bets', [])})
        for identity in ready:
            if identity in tip_index:
                funded[_key(tip_index[identity])] += 1
    historical = defaultdict(list)
    seen = set()
    for decision in decisions:
        tip = decision.get('payload') or {}
        if isinstance(tip, str):
            tip = json.loads(tip)
        identity = decision.get('family') or decision.get('tip_id') or tip.get('id')
        if not identity or identity in seen or tip.get('stake', 0) <= 0:
            continue
        seen.add(identity)
        key = _key(tip)
        if key[0] not in LEAGUES or not key[2]:
            continue
        groups.setdefault(key, {})
        historical[key].append((decision, tip))
    feedback = set(feedback_tip_ids)
    today = now.astimezone(ZoneInfo('Europe/Budapest')).date()
    rows = []
    for key in sorted(groups):
        source = live.get(key, {})
        original = historical[key]
        issued_today = 0
        closed = []
        for decision, tip in original:
            try:
                if parse_time(decision.get('issued_at')).astimezone(ZoneInfo('Europe/Budapest')).date() == today:
                    issued_today += 1
            except (TypeError, ValueError, AttributeError):
                pass
            if decision.get('status') in ('won', 'lost', 'push', 'void', 'half_won', 'half_lost'):
                closed.append((decision, tip))
        fed = sum((d.get('tip_id') or t.get('id')) in feedback for d, t in closed)
        parsed = source.get('parsed_outcomes', 0) if current else 0
        priced = source.get('priced_outcomes', 0) if current else 0
        passed = source.get('guard_passed_outcomes', 0) if current else 0
        counts=evidence[key]
        # Guard-checked candidates reached finite pricing in this publication,
        # even when validation then rejected them. Pricing attempts/errors alone
        # are not evidence of a usable price or scope model.
        priced_for_guard=source.get('guard_checked_outcomes',0) if current else 0
        available=source.get('active_standard_outcomes',0) if key[1]=='standard' else source.get('observed_outcomes',0)
        activity_unverified=current and available>0 and key[1]!='standard'
        offered=current and available>0 and key[1]=='standard'
        expected=available if offered or activity_unverified else 0
        def settled_evidence(decision):
            value=decision.get('outcome_evidence')
            try:
                if isinstance(value,str):value=json.loads(value)
                return (isinstance(value,dict) and bool(value.get('source') or value.get('sources'))
                    and parse_time(value.get('settled_at'))<=now and _finite(decision.get('pnl')))
            except (TypeError,ValueError,AttributeError):return False
        closed_with_evidence=sum(settled_evidence(d) for d,_ in closed)
        tracked=sum(d.get('assumed_placement_recorded') is True or d.get('assumed_placement_recorded')==1 for d,_ in original)
        stages={
            'data':_stage(counts['data'],expected,'Fresh exact quotes and passed sports-input readiness, per observed candidate.'),
            'parsing':_stage(parsed,expected,'Exact parsed current source outcomes; inactive offers are excluded from the denominator.'),
            'model':_stage(max(counts['model'],priced_for_guard),expected,'Current exact-scope candidate model evidence, including candidates that reached guard evaluation before rejection.'),
            'pricing':_stage(max(counts['pricing'],priced_for_guard),expected,'Finite candidate price/EV reaching guard evaluation, including later rejection; pricing_error attempts alone do not count.'),
            'guard':_stage(passed,expected,'Current guard-passed outcomes; priced outcomes alone do not prove validation.'),
            'selection':_stage(counts['selection'],counts['candidates'],'Current evaluated candidates carry a selection policy and allocation decision; rejected alternatives need not be funded.'),
            'sizing':_stage(counts['sizing'],counts['guard'],'Current guarded candidates reproduce dynamic sizing under the active policy; zero allocation is not a funded instruction.'),
            'issuance':_stage(tracked,len(original),'Original funded decisions have recorded assumed-placement tickets; history is not a new issuance.'),
            'settlement':_stage(closed_with_evidence,len(closed),'Closed original decisions include outcome evidence and finite P&L. Open events are not settled evidence.'),
            'learning':_stage(fed + counts['learning_all'],len(closed) + counts['guard'],'Requires every closed original decision ingested and every unique current guarded candidate consuming source-bound segment performance, measured calibration, learned weights and dynamic sizing. Denominator combines closed decisions and guarded candidates; both groups must be nonempty.')}
        if not closed or not counts['guard']:
            stages['learning']['state'] = 'awaiting_measurement'
        if stages['learning']['state'] != 'verified':
            stages['learning']['state'] = 'partial' if any(counts['learning_' + name] for name in ('segment','calibration','weights','sizing')) else 'awaiting_measurement'
        if not offered:
            for name in ('data','parsing','model','pricing','guard','selection','sizing'):
                stages[name]['state']='activity_unverified' if activity_unverified else 'not_offered'
        verified=sum(v['state']=='verified' for v in stages.values())
        rows.append({'league': key[0], 'section': key[1], 'market': key[2], 'scope': key[3],
                     'observed_in_current_publication': bool(source) and current,
                     'offered_now':offered,'offer_activity_verified':offered and key[1]=='standard','inventory_state':'observed_activity_unverified' if activity_unverified else 'offered_now' if offered else 'historical_only' if original and not source else 'catalogue_only',
                     'stages':stages,'verified_stages':verified,'complete_10_of_10':offered and verified==10,
                     'learning_detail':{'feedback_ingested':fed,'feedback_expected':len(closed),'current_consumers_expected':counts['guard'],'current_outcome_sizing':counts['learning_sizing'],'current_learned_weights':counts['learning_weights'],'current_measured_calibration':counts['learning_calibration'],'current_segment_performance':counts['learning_segment'],'current_all_four_consumers':counts['learning_all'],'consumers':{name:{'observed_candidates':counts['learning_'+name],'state':'observed' if counts['learning_'+name] else 'awaiting_measurement'} for name in ('segment','calibration','weights','sizing')},'model_promotion_verified':False},
                     'observed_outcomes': source.get('observed_outcomes', 0),
                     'parsed_outcomes': parsed, 'priced_outcomes': priced,
                     'guard_passed_outcomes': passed, 'currently_fundable_outcomes': funded[key],
                     'connected': parsed > 0, 'evaluated': current and (source.get('evaluated_outcomes', 0) > 0 or source.get('guard_checked_outcomes', 0) > 0 or passed > 0),
                     'funding_eligible': qualifying[key] > 0,
                     'funding_eligible_now': funded[key] > 0,
                     'qualifying_before_exposure': qualifying[key],
                     'funding_supported': qualifying[key] > 0,
                     'issued_today': issued_today, 'settled_decisions': len(closed),
                     'feedback_decisions': fed, 'settled_and_feedback': bool(closed) and fed == len(closed),
                     'reasons': source.get('reasons', {}),
                     'next_step': source.get('next_step') or 'Obtain a fresh exact-contract evaluation for this observed title.',
                     'evidence_state': 'current_publication' if current and source else 'catalogue_or_history_only'})
    return {'version': 'live-market-coverage-v2', 'checked_at': now.isoformat(),
            'publication_at': publication.get('generated_at'), 'publication_version': publication.get('version'),
            'catalogue_at': catalog.get('checked_at'), 'publication_fresh': current,
            'scope': 'Observed feed titles; not proof of complete Stake website coverage. Funding support means a fresh guarded candidate passes current funding policy and minimum sizing before exposure/family allocation; eligible-now means an executable funded selection.',
            'feedback_definition': 'Every settled original decision of this exact title occurs in supplied successful feedback input IDs; this does not prove model promotion.',
            'stage_definitions':[{'id':key,'label':label} for key,label in STAGES],
            'stage_scope':'Current stages require outcome-level evidence; original issuance, settlement and feedback are explicitly historical. 10/10 records observed execution, not proven profitability or automatic model promotion.',
            'rows': rows,
            'leagues': {league: {'observed_types': sum(r['league'] == league for r in rows),
                                'activity_unverified_types':sum(r['league']==league and r['inventory_state']=='observed_activity_unverified' for r in rows),
                                'offered_types':sum(r['league']==league and r['offered_now'] for r in rows),
                                'complete_10_of_10_types':sum(r['league']==league and r['complete_10_of_10'] for r in rows),
                                'incomplete_offered_types':sum(r['league']==league and r['offered_now'] and not r['complete_10_of_10'] for r in rows),
                                'evaluated_types': sum(r['league'] == league and r['evaluated'] for r in rows),
                                'funding_eligible_types': sum(r['league'] == league and r['funding_eligible'] for r in rows),
                                'funding_eligible_now_types': sum(r['league'] == league and r['funding_eligible_now'] for r in rows),
                                'guard_passed_types': sum(r['league'] == league and r['guard_passed_outcomes'] > 0 for r in rows),
                                'issued_today': sum(r['issued_today'] for r in rows if r['league'] == league)} for league in LEAGUES}}


def export_markdown(report, path):
    """Deterministic user-facing export; caller chooses existing docs destination."""
    def cell(value):
        return str(value).replace('|', '\\|').replace('\n', ' ')
    def yes(value):
        return 'Yes' if value else 'No'
    lines = ['# Live Stake market coverage', '', 'Checked: ' + report['checked_at'], '', report['scope'], '',
             '| League | Observed titles | Activity unverified | Offered now | Complete 10/10 | Incomplete offered | Evaluated | Funding supported | Ready now | Issued today |',
             '|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for league, counts in report['leagues'].items():
        lines.append('| ' + ' | '.join(map(cell, [league, counts['observed_types'], counts.get('activity_unverified_types',0), counts.get('offered_types',0), counts.get('complete_10_of_10_types',0), counts.get('incomplete_offered_types',0), counts['evaluated_types'], counts['funding_eligible_types'], counts['funding_eligible_now_types'], counts['issued_today']])) + ' |')
    headers=['League','Exact Stake title','Provider section','Scope','Inventory','Progress',*[label for _,label in STAGES],'Issued today']
    lines += ['', report.get('stage_scope',''), '', '| '+' | '.join(headers)+' |', '| '+' | '.join('---' for _ in headers)+' |']
    for row in report['rows']:
        cells=[]
        for key,_ in STAGES:
            value=row.get('stages',{}).get(key,{})
            cells.append(f"{value.get('state','not_observed')} {value.get('observed',0)}/{value.get('expected',0)}")
        lines.append('| ' + ' | '.join(map(cell, [row['league'], row['market'], row.get('section','standard'), row['scope'],row.get('inventory_state','unknown'),str(row.get('verified_stages',0))+'/10',*cells,row['issued_today']])) + ' |')
    target = Path(path)
    from .io_utils import atomic_bytes
    atomic_bytes(target, ('\n'.join(lines) + '\n').encode())
    return str(target)


def read_decisions(connection):
    """Read only compact immutable fields; never materialize multi-MB guard trees."""
    sql = """SELECT family,tip_id,issued_at,status,pnl,outcome_evidence,
      EXISTS(SELECT 1 FROM bets WHERE bets.tip_id=system_decisions.tip_id
        AND json_extract(bets.payload,'$.tracking_source')='auto_assumed_placed') assumed_placement_recorded,
      json_object('id',tip_id,'league',json_extract(payload,'$.league'),
      'stake',json_extract(payload,'$.stake'),
      'stake_market',json_extract(payload,'$.stake_market'),
      'stake_evidence',json_object('market_name',json_extract(payload,'$.stake_evidence.market_name')),
      'market',json_extract(payload,'$.market')) payload FROM system_decisions"""
    return [dict(row) for row in connection.execute(sql)]


def feedback_ids(data_dir):
    """Hash-check current successful feedback's decision input chunks only."""
    import hashlib
    import re
    data = Path(data_dir)
    path = data / 'outcome_feedback/latest.json'
    if not path.exists():
        return [], 'No successful feedback receipt'
    try:
        state = json.loads(path.read_text())
        ref = state['latest_review']
        def read(reference, folder):
            sha = reference['sha256']
            relative = reference['path']
            if not re.fullmatch(r'[0-9a-f]{64}', sha) or not re.fullmatch(folder + r'/[0-9a-f]{64}\.json', relative):
                raise ValueError('Invalid feedback evidence path')
            target = data / relative
            if target.is_symlink() or not target.resolve().is_relative_to(data.resolve()) or target.stat().st_size > 16 * 1024 * 1024:
                raise ValueError('Invalid feedback evidence size/path')
            body = target.read_bytes()
            if hashlib.sha256(body).hexdigest() != sha:
                raise ValueError('Feedback evidence hash mismatch')
            return json.loads(body)
        review = read(ref, 'outcome_feedback/reviews')
        ids = []
        chunks = review.get('input_archive', {}).get('chunks', [])
        if len(chunks) > 10000:
            raise ValueError('Feedback chunk count limit')
        for chunk in chunks:
            if chunk['kind'] != 'decisions':
                continue
            records = read(chunk, 'outcome_feedback/inputs')
            if records.get('kind') != 'decisions' or len(records.get('rows', [])) != chunk['rows']:
                raise ValueError('Feedback decision chunk binding mismatch')
            ids.extend(row['tip_id'] for row in records['rows'] if row.get('tip_id'))
        if not chunks:
            ids = [row['tip_id'] for row in review.get('inputs', {}).get('decisions', []) if row.get('tip_id')]
        return ids, ref['sha256']
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return [], str(exc)


def build_from_store(store, publication, catalog, data_dir, now=None):
    with store.db() as connection:
        decisions = read_decisions(connection)
    ids, evidence = feedback_ids(data_dir)
    report = build_live_coverage(publication, catalog, decisions, ids, now)
    report['feedback_evidence'] = evidence
    return report
