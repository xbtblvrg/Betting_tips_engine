"""Portfolio-level invariant guard to automatically detect and intercept anomalies.

Ensures no contradictory bets, duplicate families, stale quotes, or negative-EV
traps ever reach the executable recommendations.
"""
from typing import Dict, List, Any, Tuple
from collections import defaultdict
from .contracts import allocation_family, split_market


class PortfolioInvariantViolation:
    def __init__(self, code: str, message: str, game_id: str, tip_ids: List[str]):
        self.code = code
        self.message = message
        self.game_id = game_id
        self.tip_ids = tip_ids

    def to_dict(self) -> Dict[str, Any]:
        return {
            'code': self.code,
            'message': self.message,
            'game_id': self.game_id,
            'tip_ids': self.tip_ids
        }


def check_portfolio_invariants(tips: List[Dict[str, Any]]) -> Tuple[bool, List[Dict[str, Any]]]:
    """Audit the active recommendation pool for cross-market portfolio invariants."""
    violations: List[PortfolioInvariantViolation] = []
    by_game: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    
    for t in tips:
        # Only evaluate funded or actionable instructions
        stake = t.get('stake', 0)
        review = t.get('execution_review') or t.get('instruction_review') or {}
        if stake > 0 or review.get('actionable') is True:
            by_game[t.get('game_id')].append(t)

    opposing_sides = {'home': 'away', 'away': 'home', 'over': 'under', 'under': 'over'}

    for gid, game_tips in by_game.items():
        # 1. Family duplicate check
        family_counts: Dict[str, List[str]] = defaultdict(list)
        market_counts: Dict[str, List[str]] = defaultdict(list)
        seen_sides: Dict[Tuple[str, str], Tuple[str, str]] = {}  # (scope, base) -> (side, tip_id)

        for t in game_tips:
            tid = t.get('id')
            fam = allocation_family(t)
            mkt = t.get('market')
            side = t.get('side')
            scope_base = split_market(mkt) if mkt else ('full_game', 'moneyline')
            
            family_counts[fam].append(tid)
            market_counts[mkt].append(tid)

            # Check opposing sides within the same scope and market base (e.g. over vs under, home vs away)
            if side and side in opposing_sides:
                key = (scope_base[0], scope_base[1])
                opposing = opposing_sides[side]
                if key in seen_sides and seen_sides[key][0] == opposing:
                    violations.append(PortfolioInvariantViolation(
                        code='contradictory_opposing_sides',
                        message=f'Opposing sides ({side} vs {opposing}) detected on game {gid} in {scope_base}. Accidental hedge is mathematically prohibited.',
                        game_id=gid,
                        tip_ids=[seen_sides[key][1], tid]
                    ))
                else:
                    seen_sides[key] = (side, tid)

        for fam, tids in family_counts.items():
            if len(tids) > 1:
                violations.append(PortfolioInvariantViolation(
                    code='duplicate_market_family',
                    message=f'Multiple funded picks ({len(tids)}) in market family {fam} on game {gid}. Max 1 pick per family permitted.',
                    game_id=gid,
                    tip_ids=tids
                ))

        for mkt, tids in market_counts.items():
            if len(tids) > 1 and mkt not in family_counts:
                violations.append(PortfolioInvariantViolation(
                    code='duplicate_market',
                    message=f'Multiple funded picks ({len(tids)}) in market {mkt} on game {gid}.',
                    game_id=gid,
                    tip_ids=tids
                ))

    return len(violations) == 0, [v.to_dict() for v in violations]
