"""Prospective, game-clustered calibration uncertainty for pricing stress.

This module only reads immutable original decisions.  It never rewrites a
receipt or changes a result.  A segment with too few prospective settled
games keeps the legacy stress rule and is labelled explicitly.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math

from .contracts import allocation_family, split_market

VERSION = 'calibration-uncertainty-v1'
DEFAULT_MIN_GAMES = 30
Z_VALUE = 1.96


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except ValueError:
        return None


def odds_band(odds):
    try:
        odds = float(odds)
    except (TypeError, ValueError):
        return 'unknown'
    if not math.isfinite(odds) or odds <= 1:
        return 'unknown'
    if odds < 1.5:
        return '1.01-1.49'
    if odds < 2:
        return '1.50-1.99'
    if odds < 3:
        return '2.00-2.99'
    return '3.00+'


def segment_key(league, market, line, odds):
    scope, _ = split_market(market)
    family = allocation_family({'market': market, 'league': league, 'line': line})
    return '|'.join((str(league), scope, family, odds_band(odds)))


def _canonical_digest(payload):
    encoded = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _row_observation(row, trained_at_by_model, as_of):
    payload = row.get('payload') or {}
    if row.get('status') not in ('won', 'lost'):
        return None
    issued = _time(row.get('issued_at'))
    evidence = row.get('outcome_evidence') or {}
    settled = _time(evidence.get('settled_at'))
    if issued is None or settled is None or settled > as_of or issued > settled:
        return None
    trained = _time(trained_at_by_model.get(payload.get('model_version')))
    # A missing model-training timestamp cannot be treated as prospective.
    if trained is None or issued < trained:
        return None
    try:
        p = float(payload['probability'])
        push = float(payload.get('push_probability', 0.0))
        y = 1.0 if row['status'] == 'won' else 0.0
        if not (math.isfinite(p) and 0 < p < 1 and math.isfinite(push) and 0 <= push < 1 and p + push <= 1):
            return None
        conditional = p / (1.0 - push)
        key = segment_key(payload['league'], payload['market'], payload.get('line'), payload.get('reference_odds'))
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    return {'game_id': str(payload.get('game_id')), 'segment': key,
            'residual': conditional - y, 'issued_at': issued.isoformat(),
            'settled_at': settled.isoformat()}


def build_snapshot(rows, trained_at_by_model, as_of, *, minimum_games=DEFAULT_MIN_GAMES):
    """Build an immutable, prospective calibration snapshot.

    The standard error is calculated over game-level mean residuals, so
    several contracts from one event cannot masquerade as independent games.
    """
    as_of_dt = _time(as_of) if isinstance(as_of, str) else as_of
    if as_of_dt is None:
        as_of_dt = datetime.now(timezone.utc)
    groups = defaultdict(lambda: defaultdict(list))
    for row in rows or ():
        observation = _row_observation(row, trained_at_by_model or {}, as_of_dt)
        if observation:
            groups[observation['segment']][observation['game_id']].append(observation['residual'])
    segments = {}
    for key, games in sorted(groups.items()):
        game_means = [sum(values) / len(values) for values in games.values() if values]
        n_games = len(game_means)
        n_obs = sum(len(values) for values in games.values())
        bias = sum(game_means) / n_games if n_games else 0.0
        variance = (sum((value - bias) ** 2 for value in game_means) / (n_games - 1)
                    if n_games > 1 else 0.0)
        standard_error = math.sqrt(variance / n_games) if n_games else 0.0
        uncertainty = min(.35, max(0.0, bias) + Z_VALUE * standard_error)
        state = 'measured' if n_games >= minimum_games else 'insufficient_evidence'
        segments[key] = {'state': state, 'independent_games': n_games,
                          'observations': n_obs, 'bias': bias,
                          'standard_error': standard_error,
                          'uncertainty': uncertainty if state == 'measured' else 0.0,
                          'minimum_games': minimum_games,
                          'version': VERSION}
    body = {'version': VERSION, 'as_of': as_of_dt.isoformat(),
            'minimum_games': minimum_games, 'z_value': Z_VALUE,
            'segments': segments,
            'basis': 'Prospective original decisions only; issued_at >= model trained_at, settled_at <= as_of; game-clustered residuals.'}
    body['snapshot_sha256'] = _canonical_digest(body)
    return body


def summary_for(snapshot, league, market, line, odds):
    if not snapshot:
        return None
    key = segment_key(league, market, line, odds)
    summary = snapshot.get('segments', {}).get(key)
    if summary is None:
        summary = {'state': 'insufficient_evidence', 'independent_games': 0,
                   'observations': 0, 'bias': 0.0, 'standard_error': 0.0,
                   'uncertainty': 0.0, 'minimum_games': snapshot.get('minimum_games', DEFAULT_MIN_GAMES),
                   'version': snapshot.get('version', VERSION)}
    return {**summary, 'segment': key, 'snapshot_sha256': snapshot.get('snapshot_sha256')}
