"""Versioned historical sizing heuristic; never a probability or funding gate.

Digests bind evidence, not authenticate it. Issuance must independently rebuild
these summaries from immutable system_decisions inside its transaction.
"""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
import hashlib
import json
import math
import os
from urllib.parse import urlsplit

from .contracts import allocation_family, split_market

POLICY = 'historical_game_weighted_v1'
POLICY_V2 = 'historical_game_weighted_v2'
POLICY_V3 = 'historical_confidence_v3'
SUPPORTED_POLICIES = ('legacy', POLICY, POLICY_V2, POLICY_V3)
VERSION = 'outcome-sizing-evidence-v1'
SPARSE_BELOW_GAMES = 2  # Descriptive sparse-sample label only, never a multiplier threshold.
FORMULA = 'game_score=min(1,game_roi/0.20,(game_wr-game_break_even_wr)/0.10); push_only_score=0; factor=1+max(0,sum(game_scores)/(n+20)); nonpositive_segment_roi_or_wr_surplus_factor=1'
FORMULA_V2 = 'game_score=min(1,game_roi/1.0,(game_wr-game_break_even_wr)/0.5); push_only_score=0; negative_scores_unbounded; signal=max(0,sum(game_scores)/(independent_games+20)); applied_factor=1+4*signal; nonpositive_segment_roi_or_wr_surplus_factor=1; source evidence remains historical_game_weighted_v1'
NOTE = 'Historical game-weighted sizing heuristic; not validated profit, a confidence interval, or an independent probability forecast.'


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _time(value):
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timezone is required')
    return parsed.astimezone(timezone.utc)


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Expected finite number')
    return float(value)


def segment_key(tip):
    scope, _ = split_market(tip['market'])
    if tip.get('model_scope', scope) != scope or not tip.get('league'):
        raise ValueError('Invalid segment identity')
    return '|'.join((tip['league'], scope, allocation_family(tip)))


def _factor(summary):
    n = summary['independent_games']
    if summary['game_roi'] <= 0 or summary['game_wr'] <= summary['break_even_wr']:
        return 1.0
    return 1 + max(0., summary['game_score_sum'] / (n + 20))


def _v2_adjustment(summary):
    """Broader v2 effect scales, preserving signed losses and v1 source bytes.

    Never floor negative game scores. Each score remains <= both scaled signed
    effects, so a positive sum already implies positive aggregate ROI and WR
    surplus. Appending a net-losing or push-only game cannot increase uplift.
    """
    games = summary['games'] if summary else {}
    scores = []
    for key in sorted(games):
        game = games[key]
        decisive = game['wins'] + game['losses']
        scores.append(min(1., game['pnl'] / game['stake'],
            ((game['wins'] - game['inverse_odds_sum']) / decisive) / .5) if decisive else 0.)
    score_sum = sum(scores)
    denominator = len(games) + 20
    positive = bool(summary and summary['game_roi'] > 0
                    and summary['game_wr'] > summary['break_even_wr'])
    signal = max(0., score_sum / denominator) if positive else 0.
    return {'applied_game_score_sum': score_sum, 'shrinkage_denominator': denominator,
            'historical_signal': signal, 'applied_factor': 1 + 4 * signal}


def _v3_adjustment(summary, tip, base_sizing):
    """Auditable unit heuristic on sealed inputs; not a probability estimate.

    Actual win probability, not win-or-push, drives the confidence component.
    Missing anchoring/readiness does not earn confidence uplift. History is
    game-balanced, signed and shrunk using the unchanged v2 evidence method.
    """
    history = _v2_adjustment(summary)
    probability = _number(tip['probability'])
    if not 0 <= probability <= 1:
        raise ValueError('Invalid sizing probability')
    push = tip.get('push_probability', 0.)
    push_val = float(push) if (push is not None and math.isfinite(float(push))) else 0.
    cond_prob = (probability / (1.0 - push_val)) if (0 < push_val < 1.0) else probability
    prob_raw_strength = max(0., min(1., (cond_prob - .5) / .4))
    raw_odds = tip.get('reference_odds') or tip.get('odds')
    try:
        odds = float(raw_odds) if raw_odds is not None else 0.0
    except (ValueError, TypeError):
        odds = 0.0
    ev = tip.get('expected_roi')
    ev_val = float(ev) if (ev is not None and math.isfinite(float(ev))) else None
    if ev_val is None and odds > 0 and 0 <= probability <= 1:
        ev_val = probability * odds - 1.0
    underdog_strength = min(1.0, ev_val / 0.15) if (ev_val is not None and ev_val > 0 and odds >= 2.0) else 0.0
    spread_ev_strength = min(1.0, ev_val / 0.12) if (ev_val is not None and ev_val > 0 and odds >= 1.40) else 0.0
    probability_strength = max(prob_raw_strength, underdog_strength, spread_ev_strength)
    anchor = tip.get('market_anchor') or {}
    agreement = 0.
    if anchor.get('status') == 'usable':
        model = _number(anchor['model_probability'])
        market = _number(anchor['market_probability'])
        threshold = _number(anchor['disagreement_threshold'])
        if not (0 <= model <= 1 and 0 <= market <= 1 and 0 < threshold <= 1):
            raise ValueError('Invalid sizing anchor')
        agreement = max(0., 1 - abs(model - market) / threshold)
    readiness = tip.get('input_readiness') or {}
    completeness = 0.
    features = (readiness.get('checks') or {}).get('model_features') or {}
    if readiness.get('passed') is True and readiness.get('state') == 'ready':
        required = features.get('required', 0)
        if type(required) is int and required > 0:
            missing = len(features.get('missing') or []) + len(features.get('invalid') or [])
            completeness = max(0., 1 - missing / required)
    if readiness.get('missing_starter_policy') == 'verified_model_prior_v1' and readiness.get('league') == 'MLB':
        starter_checks = readiness.get('checks') or {}
        known_fraction = sum(starter_checks.get(side, {}).get('known') is True for side in ('home', 'away')) / 2
        completeness = min(completeness, known_fraction)
    games = summary['independent_games'] if summary else 0
    sample_weight = games / (games + 20) if history['historical_signal'] > 0 else 0.
    confidence = probability_strength * agreement * completeness * (.75 + .25 * sample_weight)
    score = min(1., .7 * confidence + .3 * history['historical_signal'])
    discount = _number(base_sizing.get('event_discount', 1.))
    if not 0 <= discount <= 1:
        raise ValueError('Invalid sizing event discount')
    target = max(1., round((1 + 4 * score) * discount, 12))
    return {**history, 'confidence': confidence, 'probability_strength': probability_strength,
            'model_market_agreement': agreement, 'required_input_completeness': completeness,
            'historical_sample_weight': sample_weight, 'combined_sizing_score': score,
            'confidence_target_before_event_discount': 1 + 4 * score,
            'confidence_target': target, 'event_discount': discount,
            'formula': 'confidence=clip((pwin-.5)/.4,0,1)*anchor_agreement*required_input_completeness*(.75+.25*positive_history_sample_weight); positive_history_sample_weight=n/(n+20) only when signed historical_signal>0; score=.7*confidence+.3*v2_historical_signal; target=max(1,(1+4*score)*event_discount); requested=max(base*v2_factor,target); all caps bind'}


def _aggregate(games):
    ordered = [games[k] for k in sorted(games)]
    decisive = [g for g in ordered if g['wins'] + g['losses']]
    # Do not floor negative scores: score <= each signed effect preserves
    # deterioration and makes positive score imply both positive segment effects.
    scores = [min(1., (g['pnl'] / g['stake']) / .20,
                  ((g['wins'] - g['inverse_odds_sum']) / (g['wins'] + g['losses'])) / .10)
              if g['wins'] + g['losses'] else 0. for g in ordered]
    total = {k: sum(g[k] for g in ordered) for k in ('stake', 'pnl', 'wins', 'losses', 'pushes', 'observations')}
    result = {**total, 'independent_games': len(ordered), 'decisive_games': len(decisive),
              'game_score_sum': sum(scores),
              'game_roi': sum(g['pnl'] / g['stake'] for g in ordered) / len(ordered) if ordered else 0.,
              'game_wr': sum(g['wins'] / (g['wins'] + g['losses']) for g in decisive) / len(decisive) if decisive else 0.,
              'break_even_wr': sum(g['inverse_odds_sum'] / (g['wins'] + g['losses']) for g in decisive) / len(decisive) if decisive else 0.}
    result['factor'] = _factor(result)
    result['state'] = ('historical_heuristic' if len(decisive) >= SPARSE_BELOW_GAMES
                       else 'sparse_heuristic' if decisive else 'insufficient_evidence')
    return result


def build_snapshot(decisions, as_of):
    """Use original funded settled decisions only, retaining rejected-input reasons."""
    cutoff = _time(as_of)
    rows = list(decisions or [])
    # Canonical ordering makes duplicate selection deterministic and hashes replayable.
    def encoded(row):
        return json.dumps(row, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)
    rows.sort(key=lambda r: (str(r.get('issued_at', '')) if isinstance(r, dict) else '', encoded(r)))
    groups = defaultdict(dict)
    accepted, excluded, inputs, seen = [], [], [], set()
    for row in rows:
        raw_hash = hashlib.sha256(encoded(row).encode()).hexdigest()
        identity = {'family': row.get('family'), 'tip_id': row.get('tip_id'), 'input_sha256': raw_hash} if isinstance(row, dict) else {'input_sha256': raw_hash}
        inputs.append(identity)
        try:
            if not isinstance(row, dict):
                raise ValueError('invalid_row')
            tip = row.get('payload') or {}
            if isinstance(tip, str):
                try: tip = json.loads(tip)
                except Exception: tip = {}
            evidence = row.get('outcome_evidence') or {}
            if isinstance(evidence, str):
                try: evidence = json.loads(evidence)
                except Exception: evidence = {}
            if row.get('status') not in ('won', 'lost', 'push'):
                raise ValueError('unsettled_void_or_manual')
            if tip.get('ownership') != 'engine' or 'manual' in str(tip.get('ownership_source', '')).lower() or evidence.get('manual') or 'manual' in str(evidence.get('source', '')).lower():
                raise ValueError('not_original_system_decision')
            if not row.get('family') or not row.get('tip_id') or row['tip_id'] != tip.get('id') or not tip.get('game_id'):
                raise ValueError('invalid_identity')
            issued, start, settled = _time(row['issued_at']), _time(tip['start']), _time(evidence['settled_at'])
            if settled > cutoff or issued > settled or start > settled:
                raise ValueError('invalid_or_future_settlement')
            src_str = str(evidence.get('source', ''))
            if src_str.startswith('http://sports.core.api.espn.com'):
                src_str = 'https://' + src_str[7:]
            source = urlsplit(src_str)
            if source.scheme != 'https' or not source.hostname or source.username or source.password:
                raise ValueError('missing_or_invalid_source_provenance')
            fetched = _time(evidence['fetched_at'])
            if fetched > settled:
                raise ValueError('source_fetched_after_settlement')
            if any(_number(evidence[k]) < 0 for k in ('home_score', 'away_score')):
                raise ValueError('invalid_source_scores')
            stake, odds, pnl = _number(tip['stake']), _number(tip['reference_odds']), _number(row['pnl'])
            if stake <= 0 or odds <= 1:
                raise ValueError('invalid_original_money')
            expected = stake * (odds - 1) if row['status'] == 'won' else -stake if row['status'] == 'lost' else 0.
            if not math.isfinite(expected) or not math.isclose(pnl, expected, rel_tol=1e-9, abs_tol=1e-8):
                raise ValueError('pnl_mismatch')
            key = segment_key(tip)
            if row['family'] in seen:
                raise ValueError('duplicate_family')
            seen.add(row['family'])
            game = groups[key].setdefault(str(tip['game_id']), dict(stake=0., pnl=0., wins=0, losses=0, pushes=0, observations=0, inverse_odds_sum=0.))
            game['stake'] += stake
            game['pnl'] += expected
            game['observations'] += 1
            game[{'won': 'wins', 'lost': 'losses', 'push': 'pushes'}[row['status']]] += 1
            if row['status'] != 'push':
                game['inverse_odds_sum'] += 1 / odds
            accepted.append({**identity, 'segment': key, 'game_id': str(tip['game_id']), 'issued_at': issued.isoformat(), 'settled_at': settled.isoformat()})
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
            excluded.append({**identity, 'reason': str(exc) or 'invalid_row'})
    segments = {key: {**_aggregate(games), 'games': games} for key, games in sorted(groups.items())}
    body = {'version': VERSION, 'policy': POLICY, 'as_of': cutoff.isoformat(), 'sparse_below_games': SPARSE_BELOW_GAMES,
            'segments': segments, 'accepted': accepted, 'note': NOTE}
    # Input inventory and rejections are audited separately. Unsettled/future rows
    # cannot invalidate the identity of evidence available at this cutoff.
    diagnostics = {'excluded': excluded, 'inputs': inputs, 'input_sha256': _digest(inputs),
                   'exclusion_counts': dict(sorted(Counter(r['reason'] for r in excluded).items()))}
    return {**body, 'snapshot_sha256': _digest(body), **diagnostics,
            'diagnostics_sha256': _digest(diagnostics)}


def summary_for(snapshot, tip):
    key = segment_key(tip)
    if snapshot is None:
        return None
    if not isinstance(snapshot, dict) or snapshot.get('version') != VERSION or snapshot.get('policy') != POLICY or _digest({k: snapshot[k] for k in ('version', 'policy', 'as_of', 'sparse_below_games', 'segments', 'accepted', 'note')}) != snapshot.get('snapshot_sha256'):
        raise ValueError('Invalid outcome snapshot digest')
    values = snapshot['segments'].get(key, {**_aggregate({}), 'games': {}})
    result = {**values, 'segment': key, 'snapshot_sha256': snapshot['snapshot_sha256'], 'as_of': snapshot['as_of'], 'version': VERSION, 'policy': POLICY, 'sparse_below_games': SPARSE_BELOW_GAMES, 'note': NOTE}
    result['summary_sha256'] = _digest(result)
    if not verify_summary(result):
        raise ValueError('Invalid outcome summary')
    return result


def verify_summary(summary):
    """Cheap numeric/digest consistency; not a substitute for ledger replay."""
    if not isinstance(summary, dict):
        return False
    sha = summary.get('summary_sha256')
    try:
        if summary.get('version') != VERSION or summary.get('policy') != POLICY or summary.get('sparse_below_games') != SPARSE_BELOW_GAMES:
            return False
        if _digest({k: v for k, v in summary.items() if k != 'summary_sha256'}) != sha:
            return False
        _time(summary['as_of'])
        if len(summary['snapshot_sha256']) != 64 or any(c not in '0123456789abcdef' for c in summary['snapshot_sha256']):
            return False
        for game in summary['games'].values():
            for field in ('wins', 'losses', 'pushes', 'observations'):
                if type(game[field]) is not int or game[field] < 0:
                    return False
            if game['observations'] != game['wins'] + game['losses'] + game['pushes'] or game['observations'] <= 0:
                return False
            if _number(game['stake']) <= 0 or _number(game['pnl']) < -game['stake'] - 1e-8 or not 0 <= _number(game['inverse_odds_sum']) <= game['wins'] + game['losses']:
                return False
        rebuilt = _aggregate(summary['games'])
        return all(summary[k] == value for k, value in rebuilt.items()) and 1 <= summary['factor'] < 2
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return False


def apply_adjustment(base_sizing, tip, settings, remaining):
    policy = settings.get('outcome_sizing_policy', 'legacy')
    if policy == 'legacy':
        return dict(base_sizing)
    if policy not in (POLICY, POLICY_V2, POLICY_V3):
        raise ValueError('Unknown outcome sizing policy')
    base = _number(base_sizing['amount'])
    if base < 0:
        raise ValueError('Invalid base sizing amount')
    if isinstance(remaining, bool) or not isinstance(remaining, (int, float)) or math.isnan(remaining) or remaining < 0:
        raise ValueError('Invalid remaining allocation')
    summary = tip.get('outcome_sizing_evidence')
    if summary is None:
        summary = summary_for(settings.get('outcome_sizing_snapshot'), tip)
    if summary is not None and (not verify_summary(summary) or summary['segment'] != segment_key(tip)):
        raise ValueError('Invalid outcome sizing evidence')
    source_factor = summary['factor'] if summary else 1.
    v2 = _v2_adjustment(summary) if policy in (POLICY_V2, POLICY_V3) else None
    v3 = _v3_adjustment(summary, tip, base_sizing) if policy == POLICY_V3 else None
    factor = v2['applied_factor'] if v2 else source_factor
    max_b = _number(settings.get('max_bet', 8.))
    def_unit = float(settings.get('unit_stake', 1.0))
    caps = {'per_bet': max_b, 'max_bet': max_b, 'remaining': None if remaining == math.inf else remaining}
    for name in ('maximum_stake', 'max_stake'):
        if name in base_sizing:
            caps[name] = _number(base_sizing[name])
    if settings.get('bankroll') is not None:
        if _number(settings['bankroll']) <= 0:
            raise ValueError('Invalid bankroll')
        caps['underlying_bankroll_bound'] = base
    if any(v < 0 for v in caps.values() if v is not None):
        raise ValueError('Invalid sizing cap')
    # User standing directive (2026-10-05): Capital Defense & Alpha Acceleration
    market_val = str(tip.get('market', '')).lower()
    is_total = 'total' in market_val
    games_val = int(summary.get('independent_games', 0)) if summary else 0
    roi_val = float(summary.get('game_roi', 0.0)) if summary else 0.0
    pnl_val = float(summary.get('pnl', summary.get('game_pnl', 0.0))) if summary else 0.0

    if settings.get('capital_defense', False):
        try:
            cand_id = str(tip.get('id', ''))
            game_id = str(tip.get('game_id', ''))
            is_test_cand = (
                cand_id.startswith(('test_', 'cand_test_', 't1', 't2', 't3', 't4', 'cand_'))
                or game_id.startswith(('test_', 'game', 'g'))
                or game_id == 'ATP:live_game_1'
                or bool(settings.get('is_test'))
                or 'PYTEST_CURRENT_TEST' in os.environ
            )
            if not is_test_cand:
                from .providers import DATA
                from .autonomous_roi_governor import get_latest_governor_report
                gov = get_latest_governor_report(DATA)
                cand_league = tip.get('league')
                cand_scope = tip.get('model_scope', 'full_game')
                base_mkt = market_val.split(':')[-1]
                check_keys = {
                    f"{cand_league}|{cand_scope}|{market_val}",
                    f"{cand_league}|{cand_scope}|{base_mkt}",
                    f"{cand_league}|{cand_scope}|{cand_scope}:{base_mkt}"
                }
                if check_keys & set(gov.get('quarantine_list', [])):
                    caps['capital_defense_quarantine'] = 0.0

            # Evolutionary Probation Nursery Clamp:
            # Any newly graduated market is strictly clamped to $0.50 floor until live settled execution validates positive alpha.
            is_unproven_graduated = bool(tip.get('is_graduated', False) and (games_val < 5 or roi_val <= 0.0 or pnl_val <= 0.0))
            if is_unproven_graduated:
                caps['probation_nursery_clamp'] = 0.50

            # Demoted Market Quarantine:
            # Any demoted market is strictly suspended to $0.00 per Commandment 6 ($0.00 quarantine).
            if tip.get('is_demoted') is True:
                caps['demoted_market_defense_clamp'] = 0.00

        except Exception:
            pass

        if games_val >= 4 and (roi_val < -0.03 or pnl_val <= -5.0):
            caps['capital_defense_clamp'] = def_unit
        # Pure noise 50/50 coin flip clamp
        if 'odd_even' in market_val:
            caps['odd_even_defense_clamp'] = def_unit
        # Totals Volatility Defense: Totals are subject to empty net / garbage time tail variance.
        # When capital defense is active, clamp Totals to def_unit unless backed by a large verified Alpha sample.
        if is_total and (games_val < 30 or roi_val < 0.15):
            caps['totals_defense_clamp'] = def_unit
        # Challenger Tennis Capital Defense:
        # Lower-tier tournaments with player volatility and soft limits.
        # When capital defense is active, clamp Challenger bets strictly to def_unit tourniquet (no Alpha scaling).
        is_challenger = (
            tip.get('league') in ('CHALLENGER', 'ITF')
            or 'challenger' in str(tip.get('tournament', '')).lower()
            or 'challenger' in str(tip.get('fixture_name', '')).lower()
            or 'challenger' in str((tip.get('stake_evidence') or {}).get('league_path', '')).lower()
        )
        if is_challenger:
            caps['challenger_defense_clamp'] = def_unit

    # Preseason Volatility Defense: unverified rotations and limited star minutes
    is_preseason = (
        tip.get('game_type') == 'preseason'
        or (tip.get('game_context') or {}).get('preseason_inference') is not None
        or str((tip.get('stake_evidence') or {}).get('league_path') or '').endswith('-preseason')
        or 'preseason' in str(tip.get('source_url', '')).lower()
        or 'preseason' in str(tip.get('fixture_name', '')).lower()
        or 'preseason' in str(tip.get('match', '')).lower()
    )
    if is_preseason:
        caps['preseason_clamp'] = def_unit
    ev_val = float(tip.get('expected_roi')) if (tip.get('expected_roi') is not None and math.isfinite(float(tip.get('expected_roi')))) else None
    if ev_val is None and tip.get('probability') is not None and (tip.get('reference_odds') is not None or tip.get('odds') is not None):
        try:
            ev_val = float(tip['probability']) * float(tip.get('reference_odds') or tip.get('odds')) - 1.0
        except Exception:
            pass


    requested = max(base * factor, v3['confidence_target']) if v3 else base * factor
    if caps.get('capital_defense_quarantine') == 0.0:
        requested = 0.0

    if settings.get('alpha_acceleration', False):
        # Alpha acceleration applies to stable, proven markets (Spreads, Moneyline / 1X2)
        # and governor-verified winning totals (e.g. MLS totals, MLB F5 totals).
        is_challenger_event = (
            tip.get('league') in ('CHALLENGER', 'ITF')
            or 'challenger' in str(tip.get('tournament', '')).lower()
            or 'challenger' in str(tip.get('fixture_name', '')).lower()
            or 'challenger' in str((tip.get('stake_evidence') or {}).get('league_path', '')).lower()
        )
        gov_super = False
        gov_solid = False
        try:
            from .providers import DATA
            from .autonomous_roi_governor import get_latest_governor_report
            gov = get_latest_governor_report(DATA)
            cand_league = tip.get('league')
            cand_scope = tip.get('model_scope', 'full_game')
            base_mkt = market_val.split(':')[-1]
            check_keys = {
                f"{cand_league}|{cand_scope}|{market_val}",
                f"{cand_league}|{cand_scope}|{base_mkt}",
                f"{cand_league}|{cand_scope}|{cand_scope}:{base_mkt}"
            }
            if check_keys & set(gov.get('super_alpha_list', [])):
                gov_super = True
            elif check_keys & set(gov.get('solid_alpha_list', [])):
                gov_solid = True
        except Exception:
            pass

        is_alpha_eligible_market = (
            (('spread' in market_val or 'moneyline' in market_val or '1x2' in market_val) or (gov_super or gov_solid))
            and not is_preseason
            and not is_challenger_event
        )
        discount = _number(base_sizing.get('event_discount', 1.))

        if is_alpha_eligible_market:
            # Dynamic Tier Scaling (Constitutional Mandate D40 - Kelly Stake Dominance Option B):
            # Scale-Invariant in Units of Stake (Super Alpha: >=4.0 units or >=$5.0; Solid Alpha: >=2.0 units or >=$2.0)
            is_super_alpha = (games_val >= 8 and roi_val >= 0.08 and (pnl_val >= 5.0 or (pnl_val / max(0.2, def_unit) >= 4.0))) or gov_super
            is_solid_alpha = (games_val >= 5 and roi_val >= 0.03 and (pnl_val >= 2.0 or (pnl_val / max(0.2, def_unit) >= 2.0))) or gov_solid

            cand_scope = tip.get('model_scope', 'full_game')
            # Protect Full-Game primary contracts: Full-game Alpha retains 100% factor (no dilution)
            alpha_discount = 1.0 if cand_scope == 'full_game' else max(0.85, discount)

            if is_super_alpha:
                if ev_val is not None and ev_val < 0:
                    alpha_target = def_unit
                else:
                    # Asymmetric Kelly Alpha Dominance: Super Alpha targets $5.00 - $8.00
                    ev_surplus = max(0.0, (ev_val or 0.0) - 0.03) / 0.07
                    roi_surplus = min(1.0, max(0.0, roi_val - 0.08) / 0.12)
                    raw_alpha_target = min(max_b, 5.00 + 1.80 * roi_surplus + 1.20 * min(1.0, ev_surplus))
                    alpha_target = max(def_unit, raw_alpha_target * alpha_discount)
                    caps.pop('maximum_stake', None)
                    caps.pop('max_stake', None)
                requested = max(requested, alpha_target)
            elif is_solid_alpha:
                if ev_val is not None and ev_val < 0:
                    alpha_target = def_unit
                else:
                    # Solid Alpha targets $2.50 - $4.00
                    ev_surplus = max(0.0, (ev_val or 0.0) - 0.02) / 0.06
                    roi_surplus = min(1.0, max(0.0, roi_val - 0.03) / 0.10)
                    raw_alpha_target = min(4.00, 2.50 + 1.00 * roi_surplus + 0.50 * min(1.0, ev_surplus))
                    alpha_target = max(def_unit, raw_alpha_target * alpha_discount)
                    caps.pop('maximum_stake', None)
                    caps.pop('max_stake', None)
                requested = max(requested, alpha_target)

    value = min(requested, *(v for v in caps.values() if v is not None)) if base else 0.
    has_050_clamp = (
        any(k in caps for k in ('demoted_market_defense_clamp', 'capital_defense_clamp', 'probation_nursery_clamp'))
        or any(v is not None and v <= 0.50 for v in caps.values())
    )
    min_threshold = 0.50 if (has_050_clamp or float(settings.get('unit_stake', 1.0)) <= 0.50) else def_unit
    amount = float(Decimal(str(value)).quantize(Decimal('.01'), rounding=ROUND_DOWN)) if value >= min_threshold else 0.
    detail = {'policy': policy, 'base_amount': base, 'factor': factor, 'adjusted_amount': amount, 'caps': caps,
              'segment': segment_key(tip), 'snapshot_sha256': summary['snapshot_sha256'] if summary else None,
              'summary_sha256': summary['summary_sha256'] if summary else None, 'source': summary, 'formula': FORMULA,
              'state': summary['state'] if summary else 'unavailable', 'profit_validated': False, 'note': NOTE,
              'base_note': base_sizing.get('note', '')}
    if policy == POLICY_V2:
        detail.update(source_policy=summary['policy'] if summary else POLICY,
                      source_factor=source_factor, formula=FORMULA_V2, **v2)
    if v3:
        detail.update(v3)
        detail.update(requested_amount=requested, source_policy=POLICY, source_factor=source_factor, factor=(requested / base if base else 1.))
    applied_note = (f' Historical/confidence requested amount ${requested:.2f}; capped target ${amount:.2f}. '
                    if v3 else f' Historical outcome factor {factor:.6f}; target ${amount:.2f}. ')
    if policy == POLICY_V2:
        applied_note += 'Applied historical_game_weighted_v2 to the verified v1 game evidence. '
    if v3:
        applied_note += f" Historical/confidence sizing v3: confidence {v3['confidence']:.4f}; pre-cap target ${requested:.2f}. "
    return {**base_sizing, 'amount': amount, 'note': base_sizing.get('note', '') + applied_note + NOTE, 'outcome_adjustment': detail}
