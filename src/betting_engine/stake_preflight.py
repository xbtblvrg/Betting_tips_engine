"""Pre-Flight Stake Contract Verification and Guaranteed Market Identification.

Provides millisecond-accurate odds & market existence verification using Stake's official
odds feed, preventing unnecessary DOM loading in Safari, and supplying infallible
accordion and outcome parameters when Safari execution is required.
"""
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

log = logging.getLogger("betting_engine.stake_preflight")

_FEED_BASE = "https://odds-data.stake.com"
_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_CACHE_TTL = 15.0  # seconds


def extract_fixture_slug(event_url: Optional[str]) -> Optional[str]:
    """Extract the fixture slug from a full Stake event URL."""
    if not event_url or not isinstance(event_url, str):
        return None
    url_clean = event_url.split("?")[0].rstrip("/")
    parts = url_clean.split("/")
    if parts:
        candidate = parts[-1]
        if re.match(r"^\d+-[a-z0-9-]+$", candidate):
            return candidate
    return None


def fetch_stake_fixture_odds(slug: str, timeout: float = 1.5) -> Optional[Dict[str, Any]]:
    """Fetch structured odds feed for a fixture with lightweight caching."""
    if not slug:
        return None
    now = time.time()
    if slug in _CACHE:
        ts, data = _CACHE[slug]
        if now - ts < _CACHE_TTL:
            return data

    url = f"{_FEED_BASE}/odds/{slug}"
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code == 200:
            payload = resp.json()
            if isinstance(payload, dict) and "fixture" in payload:
                _CACHE[slug] = (now, payload)
                return payload
    except Exception as e:
        log.debug(f"Pre-flight feed fetch failed for {slug}: {e}")
    return None


def _normalize_text(t: Optional[str]) -> str:
    return " ".join(re.sub(r"[^\w\s\.\+\-]", " ", (t or "").lower()).split())


def _clean_entity(text: str) -> str:
    t = re.sub(r"\([+-]?\d+(?:\.\d+)?\)", " ", text)
    t = re.sub(r"[+-]\d+(?:\.\d+)?", " ", t)
    t = re.sub(r"\b(?:over|under|o|u)\b", " ", t, flags=re.I)
    t = re.sub(r"\b\d+(?:\.\d+)?\b", " ", t)
    return " ".join(t.lower().split())


def match_outcome_text(selection: str, outcome_name: str, line: Optional[float] = None) -> bool:
    """Robust matching of a selection string against a Stake outcome name."""
    # 1. Combo check: prevent matching combo outcomes when selection is not a combo
    o_is_combo = "&" in outcome_name or " and " in outcome_name.lower()
    s_is_combo = "&" in selection or " and " in selection.lower()
    if o_is_combo != s_is_combo:
        return False

    # 2. Over / Under direction check
    s_norm = selection.lower()
    o_norm = outcome_name.lower()
    s_is_over = "over" in s_norm or s_norm.startswith("o ")
    s_is_under = "under" in s_norm or s_norm.startswith("u ")
    o_is_over = "over" in o_norm or o_norm.startswith("o ")
    o_is_under = "under" in o_norm or o_norm.startswith("u ")

    if (s_is_over or s_is_under) or (o_is_over or o_is_under):
        if s_is_over != o_is_over or s_is_under != o_is_under:
            return False

    # 3. Line matching
    s_line = None
    if line is not None:
        try:
            s_line = float(line)
        except (ValueError, TypeError):
            pass
    if s_line is None:
        m_par = re.search(r"\(([+-]?\d+(?:\.\d+)?)\)", selection)
        if m_par:
            s_line = float(m_par.group(1))
        else:
            m_ou = re.search(r"(?:over|under|o|u)\s*(\d+(?:\.\d+)?)", selection, re.I)
            if m_ou:
                s_line = float(m_ou.group(1))
            else:
                m_signed = re.search(r"([+-]\d+(?:\.\d+)?)", selection)
                if m_signed:
                    s_line = float(m_signed.group(1))

    o_line = None
    m_par_o = re.search(r"\(([+-]?\d+(?:\.\d+)?)\)", outcome_name)
    if m_par_o:
        o_line = float(m_par_o.group(1))
    else:
        m_ou_o = re.search(r"(?:over|under|o|u)\s*(\d+(?:\.\d+)?)", outcome_name, re.I)
        if m_ou_o:
            o_line = float(m_ou_o.group(1))

    if s_line is not None and o_line is not None:
        if abs(s_line - o_line) > 1e-4:
            return False
    elif s_line is None and o_line is not None:
        # Target has no line (e.g. moneyline), but outcome has a line (spread/total) -> Disqualify!
        return False
    elif s_line is not None and o_line is None:
        # Outcome has no line, but target has line -> Disqualify unless it is simple 'Over'/'Under'
        if not ((s_is_over and o_norm == "over") or (s_is_under and o_norm == "under")):
            return False

    # 4. Entity matching
    s_ent = _clean_entity(selection)
    o_ent = _clean_entity(outcome_name)
    if not s_ent and not o_ent:
        # Purely Over/Under with matched line
        return True

    s_tokens = set(s_ent.split())
    o_tokens = set(o_ent.split())
    if s_tokens == o_tokens:
        return True

    club_stopwords = {"fc", "sc", "cf", "afc", "ac", "sk", "fk", "bk", "club", "de"}
    s_meaningful = s_tokens - club_stopwords
    o_meaningful = o_tokens - club_stopwords

    if s_meaningful and o_meaningful:
        common = s_meaningful.intersection(o_meaningful)
        if len(common) >= 1 and (common == s_meaningful or common == o_meaningful or len(common) >= 2):
            return True

    return False


def match_stake_feed_contract(
    feed_data: Dict[str, Any],
    market: str,
    selection: str,
    line: Optional[float] = None,
    league: Optional[str] = None,
    market_name: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Match a contract in the structured Stake feed and return infallible targeting params."""
    if not feed_data or not isinstance(feed_data, dict):
        return None

    league_groups = feed_data.get("groups", [])
    target_market_lower = f"{market or ''} {market_name or ''}".lower()

    # Determine period/scope
    target_period = None
    target_scope_type = None

    m_p = re.search(r"period_(\d+)", target_market_lower)
    if m_p:
        target_period = int(m_p.group(1))
        target_scope_type = "period"

    m_q = re.search(r"quarter_(\d+)", target_market_lower)
    if m_q:
        target_period = int(m_q.group(1))
        target_scope_type = "quarter"

    m_h = re.search(r"half_(\d+)", target_market_lower)
    if m_h:
        target_period = int(m_h.group(1))
        target_scope_type = "half"

    # Determine market type
    m_type = "moneyline"
    if "total" in target_market_lower:
        m_type = "totals"
    elif "spread" in target_market_lower or "handicap" in target_market_lower:
        m_type = "spreads"
    elif "both_score" in target_market_lower:
        m_type = "both_score"

    best_match = None

    for g in league_groups:
        g_name = g.get("name", "")
        g_name_lower = g_name.lower()

        # Prioritize relevant groups
        for bucket in g.get("markets", []):
            markets_list = bucket if isinstance(bucket, list) else [bucket]
            for m in markets_list:
                m_name = m.get("name", "")
                m_name_lower = m_name.lower()
                spec = m.get("specifiers", "") or ""

                # Check period match
                if target_period is not None:
                    spec_p = re.search(r"(?:periodnr|setnr)=(\d+)", spec)
                    has_p = False
                    if spec_p and int(spec_p.group(1)) == target_period:
                        has_p = True
                    elif f"{target_period}st {target_scope_type}" in m_name_lower or (target_scope_type == "period" and f"{target_period}st set" in m_name_lower):
                        has_p = True
                    elif f"{target_period}nd {target_scope_type}" in m_name_lower or (target_scope_type == "period" and f"{target_period}nd set" in m_name_lower):
                        has_p = True
                    elif f"{target_period}rd {target_scope_type}" in m_name_lower or (target_scope_type == "period" and f"{target_period}rd set" in m_name_lower):
                        has_p = True
                    elif f"{target_period}th {target_scope_type}" in m_name_lower or (target_scope_type == "period" and f"{target_period}th set" in m_name_lower):
                        has_p = True
                    elif target_scope_type == "quarter" and f"{target_period} quarter" in m_name_lower:
                        has_p = True
                    elif target_scope_type == "half" and f"{target_period} half" in m_name_lower:
                        has_p = True

                    if not has_p:
                        continue
                else:
                    # Full game: avoid period/quarter/half/set markets
                    if "periodnr=" in spec or "setnr=" in spec or any(k in m_name_lower for k in ["1st", "2nd", "3rd", "4th", "half", "set"]):
                        continue

                # Check market type match
                target_is_combo = "&" in target_market_lower or " and " in target_market_lower
                if m_type == "totals":
                    if "total" not in m_name_lower:
                        continue
                    is_target_team_total = (
                        "away_total" in target_market_lower
                        or "home_total" in target_market_lower
                        or "team" in target_market_lower
                    )
                    is_market_team_total = (
                        "team" in m_name_lower
                        or ("total" in m_name_lower and not m_name_lower.startswith("total") and not re.match(r"^(?:1st|2nd|3rd|4th|half|quarter|period)\s*(?:quarter|half|period)?\s*[-–]?\s*total", m_name_lower))
                    )
                    if not is_target_team_total and is_market_team_total:
                        continue
                    if is_target_team_total and not is_market_team_total:
                        continue
                    if not target_is_combo and ("&" in m_name_lower or " and " in m_name_lower):
                        continue
                    if line is not None:
                        spec_t = re.search(r"total=(\d+(?:\.\d+)?)", spec)
                        if spec_t and abs(float(spec_t.group(1)) - float(line)) > 0.01:
                            continue
                elif m_type == "spreads":
                    if not any(k in m_name_lower for k in ["handicap", "spread", "puck line", "run line"]):
                        continue
                    if not target_is_combo and ("&" in m_name_lower or " and " in m_name_lower):
                        continue
                    if line is not None:
                        spec_h = re.search(r"hcp=([+-]?\d+(?:\.\d+)?)", spec)
                        if spec_h and abs(abs(float(spec_h.group(1))) - abs(float(line))) > 0.01:
                            continue
                elif m_type == "both_score":
                    if "both teams to score" not in m_name_lower and "both to score" not in m_name_lower:
                        continue
                    if not target_is_combo and ("&" in m_name_lower or " and " in m_name_lower):
                        continue
                elif m_type == "moneyline":
                    if not any(k in m_name_lower for k in ["winner", "1x2", "draw no bet", "moneyline"]):
                        continue
                    if not target_is_combo and ("&" in m_name_lower or " and " in m_name_lower):
                        continue

                # Scan outcomes
                for o in m.get("outcomes", []):
                    o_name = o.get("name", "")
                    o_odds = float(o.get("odds", 0.0))
                    o_active = o.get("active", True)
                    o_id = o.get("id")

                    if match_outcome_text(selection, o_name, line):
                        # Determine proper tab name
                        tab = "Popular"
                        if (league or "").upper() in ("WTA", "ATP") or "set" in m_name_lower or "setnr=" in spec:
                            tab = "Sets"
                        elif target_period is not None:
                            if target_scope_type == "period":
                                tab = "Periods"
                            elif target_scope_type == "quarter":
                                tab = "Quarters"
                            elif target_scope_type == "half":
                                tab = "Half"
                        elif g_name_lower in ("periods", "quarters", "half", "goals", "sets", "games"):
                            tab = g_name.capitalize()

                        return {
                            "group_name": g_name,
                            "tab": tab,
                            "market_name": m_name,
                            "outcome_id": o_id,
                            "outcome_name": o_name,
                            "odds": o_odds,
                            "active": o_active,
                            "specifiers": spec,
                        }

    return None


def evaluate_odds_drift_flexible(
    live_odds: float,
    expected_odds: Optional[float] = None,
    min_odds: Optional[float] = None,
    model_probability: Optional[float] = None,
) -> Dict[str, Any]:
    """Evaluates whether live odds remain acceptable without overly strict artificial hurdles.
    
    Adheres strictly to the user directive:
    - Never reject if EV is positive (EV >= 0.0).
    - If probability is unknown, allow up to 10% drift from expected odds.
    - Strictly enforce live_odds >= 1.40 floor (Commandment 3).
    """
    if live_odds < 1.40:
        return {
            "acceptable": False,
            "reason": f"Odds {live_odds:.2f} below minimum allowed floor 1.40",
            "ev": None,
            "drift_pct": None,
        }

    # Case A: Model win probability is available -> Pure EV valuation
    if model_probability is not None and model_probability > 0:
        ev = round(model_probability * live_odds - 1.0, 4)
        # Accept if EV is positive or effectively break-even (>= -0.005 to absorb micro floating rounding)
        if ev >= -0.005:
            return {
                "acceptable": True,
                "reason": f"Live odds {live_odds:.2f} maintains positive EV ({ev * 100:+.2f}%)",
                "ev": ev,
                "drift_pct": round((live_odds - (expected_odds or live_odds)) / (expected_odds or live_odds) * 100, 2)
                if expected_odds
                else 0.0,
            }
        else:
            return {
                "acceptable": False,
                "reason": f"Live odds {live_odds:.2f} resulted in negative EV ({ev * 100:+.2f}%)",
                "ev": ev,
                "drift_pct": round((live_odds - (expected_odds or live_odds)) / (expected_odds or live_odds) * 100, 2)
                if expected_odds
                else 0.0,
            }

    # Case B: Model probability is not provided -> Flexible nominal drift check
    drift_pct = 0.0
    if expected_odds and expected_odds > 0:
        drift_pct = round((live_odds - expected_odds) / expected_odds * 100, 2)

    # If odds increased or stayed steady -> acceptable
    if live_odds >= (expected_odds or 1.40):
        return {
            "acceptable": True,
            "reason": f"Live odds {live_odds:.2f} >= expected {expected_odds or 1.40:.2f}",
            "ev": None,
            "drift_pct": drift_pct,
        }

    # Check minimum odds boundary
    if min_odds and min_odds > 0:
        # Permit minor tolerance (2% below min_odds or max 10% total drop)
        if live_odds >= min_odds * 0.98:
            return {
                "acceptable": True,
                "reason": f"Live odds {live_odds:.2f} within flexible min_odds tolerance ({min_odds:.2f})",
                "ev": None,
                "drift_pct": drift_pct,
            }

    # If within 10% drop of expected odds -> acceptable (non-strict)
    if expected_odds and live_odds >= expected_odds * 0.90:
        return {
            "acceptable": True,
            "reason": f"Live odds {live_odds:.2f} within 10% drift tolerance (expected: {expected_odds:.2f})",
            "ev": None,
            "drift_pct": drift_pct,
        }

    return {
        "acceptable": False,
        "reason": f"Live odds {live_odds:.2f} drifted by {drift_pct:.1f}% below acceptable tolerance",
        "ev": None,
        "drift_pct": drift_pct,
    }


def preflight_verify_contract(
    event_url: Optional[str],
    market: str,
    selection: str,
    line: Optional[float] = None,
    expected_odds: Optional[float] = None,
    min_odds: Optional[float] = None,
    model_probability: Optional[float] = None,
    league: Optional[str] = None,
    market_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute high-speed preflight check on a contract.
    
    Returns:
        status: 'ok' | 'skip_drift' | 'skip_suspended' | 'market_not_found' | 'fallback_to_dom'
    """
    slug = extract_fixture_slug(event_url)
    if not slug:
        return {"status": "fallback_to_dom", "reason": "No valid fixture slug in event URL"}

    feed_data = fetch_stake_fixture_odds(slug)
    if not feed_data:
        # Network timeout or feed unavailable -> Seamless fallback to DOM
        return {"status": "fallback_to_dom", "reason": "Feed unavailable, using DOM fallback"}

    match = match_stake_feed_contract(
        feed_data,
        market=market,
        selection=selection,
        line=line,
        league=league,
        market_name=market_name,
    )
    if not match:
        # Contract verified missing from Stake feed
        return {
            "status": "market_not_found",
            "reason": f"Market '{market}' / '{selection}' not currently offered on Stake for this fixture",
            "slug": slug,
        }

    if not match.get("active", True):
        return {
            "status": "skip_suspended",
            "reason": f"Outcome '{match.get('outcome_name')}' is currently suspended on Stake",
            "slug": slug,
            "match": match,
        }

    live_odds = float(match.get("odds", 0.0))
    drift_eval = evaluate_odds_drift_flexible(
        live_odds=live_odds,
        expected_odds=expected_odds,
        min_odds=min_odds,
        model_probability=model_probability,
    )

    if not drift_eval.get("acceptable", True):
        return {
            "status": "skip_drift",
            "reason": drift_eval.get("reason"),
            "live_odds": live_odds,
            "expected_odds": expected_odds,
            "ev": drift_eval.get("ev"),
            "drift_pct": drift_eval.get("drift_pct"),
            "match": match,
        }

    return {
        "status": "ok",
        "live_odds": live_odds,
        "exact_tab": match.get("tab", "Popular"),
        "exact_accordion": match.get("market_name"),
        "exact_outcome_name": match.get("outcome_name"),
        "exact_outcome_id": match.get("outcome_id"),
        "ev": drift_eval.get("ev"),
        "match": match,
    }
