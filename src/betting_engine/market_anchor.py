"""Market-relative probability utilities.

The module is deliberately pure.  It can be used by the live decision path
or by a shadow trial without fetching data, mutating the ledger, or changing a
ticket after issuance.  A complete Stake contract is required before a
de-vigged probability is returned.
"""
from __future__ import annotations

from collections import defaultdict
import math


EPSILON = 1e-9


def conditional_push_free_line(kind: str, line) -> bool:
    """Whether a two-way quoted Asian contract is priced on non-push mass.

    Integer totals/handicaps and quarter lines have a zero/half settlement
    component.  The sportsbook's two prices identify the relative win/loss
    mass only; the model's push mass must be retained separately.  Half lines
    are included harmlessly (their model push mass is normally zero), while
    three-way markets never use this branch.
    """
    if kind.rsplit(':', 1)[-1] not in ('totals','spreads','home_total','away_total') or line is None:
        return False
    try:
        value=float(line)
        return math.isfinite(value) and abs(value*4-round(value*4))<1e-9
    except (TypeError,ValueError):
        return False


def _probability(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"Invalid {name}")
    if not 0.0 < value < 1.0:
        raise ValueError(f"Invalid {name}")
    return float(value)


def devig_probabilities(odds_by_outcome: dict[str, float]) -> dict[str, float]:
    """Return proportional no-vig probabilities for a complete market.

    The input is an exclusive market's decimal odds.  Missing, duplicate,
    non-finite and non-positive-vig contracts fail closed; an incomplete
    Stake response never becomes a fabricated market anchor.
    """
    if not isinstance(odds_by_outcome, dict) or len(odds_by_outcome) < 2:
        raise ValueError("A complete exclusive market with at least two outcomes is required")
    implied = {}
    for outcome, odds in odds_by_outcome.items():
        if not isinstance(outcome, str) or not outcome:
            raise ValueError("Invalid market outcome")
        if isinstance(odds, bool) or not isinstance(odds, (int, float)) or not math.isfinite(odds) or odds <= 1:
            raise ValueError("Invalid decimal odds")
        implied[outcome] = 1.0 / float(odds)
    total = sum(implied.values())
    if not math.isfinite(total) or total <= 0:
        raise ValueError("Invalid implied probability mass")
    return {outcome: value / total for outcome, value in implied.items()}


def conditional_devig_probabilities(odds_by_outcome: dict[str, float]) -> dict[str, float]:
    """Two-way devig explicitly labelled as conditional on no push.

    The arithmetic is proportional devig, but the separate name and receipt
    prevent callers from accidentally treating the result as unconditional
    probability when an integer/quarter line has model push mass.
    """
    if not isinstance(odds_by_outcome, dict) or len(odds_by_outcome)!=2:
        raise ValueError('A complete two-way Asian market is required')
    return devig_probabilities(odds_by_outcome)


def logit(value: float) -> float:
    value = _probability(value, "probability")
    return math.log(value / (1.0 - value))


def sigmoid(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Invalid logit")
    # Stable on both tails without importing a numerical package.
    if value >= 0:
        z = math.exp(-value)
        return 1.0 / (1.0 + z)
    z = math.exp(value)
    return z / (1.0 + z)


def _model_probability(value: float, allow_endpoints: bool) -> float:
    if allow_endpoints and not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= 1:
        return float(value)
    return _probability(value, "model probability")


def logit_blend(model_probability: float, market_probability: float, model_weight: float, *, allow_endpoint_model: bool = False) -> float:
    """Blend independent model and no-vig market probabilities in logit space."""
    model = _model_probability(model_probability, allow_endpoint_model)
    market = _probability(market_probability, "market probability")
    if isinstance(model_weight, bool) or not isinstance(model_weight, (int, float)) or not math.isfinite(model_weight):
        raise ValueError("Invalid model weight")
    if not 0.0 <= model_weight <= 1.0:
        raise ValueError("Model weight must be in [0, 1]")
    if allow_endpoint_model and model_weight == 1:
        return model
    # Epsilon is numerical regularization only at the logit transform;
    # reported raw model mass and disagreement retain their exact values.
    transformed_model = min(1.0 - EPSILON, max(EPSILON, model)) if model in (0., 1.) else model
    return sigmoid((1.0 - float(model_weight)) * logit(market) + float(model_weight) * logit(transformed_model))


def anchor_status(model_probability: float, market_probability: float, *, threshold: float = 0.30, allow_endpoint_model: bool = False) -> str:
    """Classify disagreement before a contract can receive funding."""
    model = _model_probability(model_probability, allow_endpoint_model)
    market = _probability(market_probability, "market probability")
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not math.isfinite(threshold) or threshold <= 0 or threshold >= 1:
        raise ValueError("Invalid disagreement threshold")
    return 'review' if abs(model - market) > threshold else 'usable'


def contract_anchors(markets, quotes):
    """Build exact-contract anchors from parsed Stake markets and quotes.

    Each `Market` carries the provider's condition identity.  Grouping by
    `(game, condition, kind, scope, line)` prevents a neighboring period or
    alternate line from being mixed into the devig calculation.
    """
    groups = defaultdict(list)
    for market in markets:
        quote = quotes.get(market.id)
        if quote is None:
            continue
        identity = market.metadata.get('stake_contract_identity', market.condition_id)
        # Stake expresses a two-way handicap as opposite signed lines
        # (home -7.5 / away +7.5).  The condition identity already contains
        # the exact offered line, so including the side's signed line here
        # would split one complete contract into two one-sided groups and
        # silently remove its market anchor.
        from .overtime_contracts import overtime_alias
        alias = overtime_alias(market.league, market.metadata.get('stake_market_name'),
                               market.metadata.get('stake_outcome_name')) if hasattr(market, 'league') else None
        binary_overtime = alias == (market.kind, market.side) and market.line is None
        key = (market.game_id, identity, 'overtime_alias' if binary_overtime else market.kind, market.metadata.get('model_scope'))
        groups[key].append((market, quote))
    result = {}
    expected = {
        'moneyline': {'home', 'away'}, 'spreads': {'home', 'away'},
        'totals': {'over', 'under'}, 'home_total': {'over', 'under'},
        'away_total': {'over', 'under'}, 'three_way': {'home', 'draw', 'away'},
        'odd_even': {'odd', 'even'}, 'both_score': {'yes', 'no'},
        'double_chance': {'home_draw', 'away_draw', 'home_away'},
        'exact_total': {'over', 'exact', 'under'},
        'overtime_alias': {'draw', 'home_away'},
    }
    for (_, _, kind, _), rows in groups.items():
        base = kind.rsplit(':', 1)[-1]
        sides = expected.get(base)
        if base == 'winner_total':
            first = rows[0][0]
            binary = (getattr(first, 'league', None) == 'WNBA'
                and first.metadata.get('model_scope') == 'full_game'
                and first.metadata.get('stake_market_name') == 'Winner & Total (Incl. Overtime)')
            sides = {winner+'_'+total for winner in (('home', 'away') if binary else ('home', 'away', 'draw'))
                     for total in ('over', 'under')}
        if base == 'exact_score':
            # These are complete exclusive multi-outcome contracts.  The
            # parser has already checked their exact Stake outcome syntax;
            # retain all outcomes for one proportional devig calculation.
            sides = {market.side for market, _ in rows}
        if not sides or len(rows) != len(sides) or {market.side for market, _ in rows} != sides:
            continue
        try:
            odds = {market.side: 1.0 / quote.effective_cost for market, quote in rows}
            market_name = str(rows[0][0].metadata.get('stake_market_name') or getattr(rows[0][0], 'description', '') or '').lower()
            # Draw No Bet is a two-way contract whose draw outcome is a push.
            # Stake exposes it with moneyline-shaped sides, so the kind alone
            # cannot identify its conditional (push-free) pricing rule.
            conditional = conditional_push_free_line(kind, rows[0][0].line) or 'draw no bet' in market_name
            fair = conditional_devig_probabilities(odds) if conditional else devig_probabilities(odds)
        except (ValueError, ZeroDivisionError):
            continue
        for market, quote in rows:
            result[market.id] = {
                'market_probability': fair[market.side],
                'market_probabilities': fair,
                'market_weight_source': 'Stake exact condition, conditional push-free devig' if conditional else 'Stake exact condition, proportional devig',
                'conditional_push_free': conditional,
                'market_anchor_key': [market.game_id, market.condition_id, market.kind, market.line],
                'quote_at': quote.fetched_at,
            }
    return result


def fit_model_weight(observations, *, minimum_games: int = 30, grid_step: float = 0.05):
    """Fit a model logit weight on an earlier chronological sample.

    The returned weight is a candidate only.  A caller must run a later
    prospective holdout and apply its own promotion gate.  This keeps the
    weekly learning loop reproducible and prevents post-outcome leakage.
    """
    if isinstance(minimum_games, bool) or not isinstance(minimum_games, int) or minimum_games < 1:
        raise ValueError("Invalid minimum game count")
    if isinstance(grid_step, bool) or not isinstance(grid_step, (int, float)) or not math.isfinite(grid_step) or not 0 < grid_step <= 1:
        raise ValueError("Invalid grid step")
    rows = sorted(observations, key=lambda row: str(row.get('issued_at', '')))
    games = {row.get('game_id') for row in rows if row.get('game_id')}
    if len(games) < minimum_games:
        return {'state': 'insufficient_evidence', 'independent_games': len(games), 'weight': 0.0}
    valid = []
    for row in rows:
        try:
            p = _probability(row['model_probability'], 'model probability')
            q = _probability(row['market_probability'], 'market probability')
            y = int(row['outcome'])
            if y not in (0, 1):
                raise ValueError
            valid.append((p, q, y))
        except (KeyError, TypeError, ValueError):
            continue
    if not valid:
        return {'state': 'insufficient_evidence', 'independent_games': len(games), 'weight': 0.0}
    candidates = [min(1.0, round(i * float(grid_step), 10)) for i in range(int(1 / float(grid_step)) + 1)]
    def loss(weight):
        total = 0.0
        for p, q, y in valid:
            blended = logit_blend(p, q, weight)
            total -= y * math.log(max(EPSILON, blended)) + (1 - y) * math.log(max(EPSILON, 1 - blended))
        return total / len(valid)
    best = min(candidates, key=lambda weight: (loss(weight), weight))
    return {'state': 'candidate', 'independent_games': len(games), 'observations': len(valid),
            'weight': best, 'log_loss': loss(best), 'grid_step': float(grid_step),
            'basis': 'chronological training sample; prospective holdout required before promotion'}
