"""Telegram Notification Service for newly published bet recommendations.

Reads TELEGRAM_TOKEN and TELEGRAM_CHAT_ID from environment / .env.
Ensures durable deduplication so no recommendation is ever notified twice.
Formats rich Telegram messages with Stake odds, models EV, stake, and direct link.
Fully synchronized with the 10 Supreme Mandatory Commandments Constitution.
"""
import os
import json
import math
import logging
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from zoneinfo import ZoneInfo

from .presentation import market_name, selection_name, infer_stake_tab

log = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
ENV_PATH = ROOT_DIR / ".env"
PERSISTENCE_PATH = ROOT_DIR / "data" / "notified_telegram_tips.json"


def _read_env_val(key: str) -> Optional[str]:
    """Reads a variable from os.environ or .env file."""
    val = os.environ.get(key)
    if val:
        return val.strip()
    if ENV_PATH.exists():
        try:
            for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:
            pass
    return None


def get_telegram_config() -> Dict[str, Optional[str]]:
    return {
        "token": _read_env_val("TELEGRAM_TOKEN"),
        "chat_id": _read_env_val("TELEGRAM_CHAT_ID")
    }


def _load_notified_ids() -> set:
    if PERSISTENCE_PATH.exists():
        try:
            data = json.loads(PERSISTENCE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return set(data)
        except Exception as e:
            log.warning("Could not read notified tips persistence: %s", e)
    return set()


def _save_notified_ids(ids: set) -> None:
    try:
        PERSISTENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Keep the latest 5000 ids to prevent unbounded file growth
        id_list = list(ids)[-5000:]
        PERSISTENCE_PATH.write_text(json.dumps(id_list, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("Could not persist notified tips: %s", e)


def send_telegram_message(text: str, parse_mode: str = "HTML") -> bool:
    """Sends a raw message to the configured Telegram chat."""
    cfg = get_telegram_config()
    token, chat_id = cfg.get("token"), cfg.get("chat_id")
    if not token or not chat_id:
        log.warning("Telegram token or chat_id missing. Skipping notification.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            res_json = json.loads(resp.read().decode("utf-8"))
            return bool(res_json.get("ok"))
    except Exception as e:
        log.error("Failed to send Telegram message: %s", e)
        return False


def format_recommendation_message(tip: Dict[str, Any]) -> str:
    """Formats a structured HTML notification message for a bet recommendation."""
    league = tip.get("league", "SPORTS")
    away = tip.get("away", "Away")
    home = tip.get("home", "Home")

    # Use Stake terminology for market display and selection name
    raw_market = tip.get("market")
    if raw_market:
        market = tip.get("market_display") or market_name(league, raw_market, tip)
        try:
            selection = selection_name(tip) or tip.get("selection", "Selection")
        except Exception:
            selection = tip.get("selection", "Selection")
    else:
        market = tip.get("market_display") or "Winner"
        selection = tip.get("selection", "Selection")

    stake = float(tip.get("stake", 0.0))
    odds = float(tip.get("reference_odds") or tip.get("odds") or 1.90)

    # Determine effective minimum odds based on funding basis and tier
    fb = tip.get("funding_basis") or {}
    fb_tier = fb.get("tier")
    fb_min_odds = fb.get("minimum_odds")
    tip_min_odds = tip.get("minimum_odds")

    if fb_tier in ("model_pick", "probability_pick") and fb_min_odds and math.isfinite(float(fb_min_odds)):
        effective_min = float(fb_min_odds)
    elif tip_min_odds and math.isfinite(float(tip_min_odds)):
        effective_min = float(tip_min_odds)
    elif fb_min_odds and math.isfinite(float(fb_min_odds)):
        effective_min = float(fb_min_odds)
    else:
        effective_min = float(fb.get("min_odds") or 1.40)

    prob = float(tip.get("probability") or 0.5) * 100
    ev = float(tip.get("expected_roi") or 0.0) * 100
    stress_ev = float(tip.get("stress_roi") or 0.0) * 100

    ev_str = f"+{ev:.1f}%" if ev >= 0 else f"{ev:.1f}%"
    stress_str = f"+{stress_ev:.1f}%" if stress_ev >= 0 else f"{stress_ev:.1f}%"

    # Format Budapest start time with dynamic timezone acronym (%Z)
    start_str = tip.get("start", "")
    budapest_time_str = start_str
    if start_str:
        try:
            dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            budapest_dt = dt.astimezone(ZoneInfo("Europe/Budapest"))
            budapest_time_str = budapest_dt.strftime("%Y-%m-%d %H:%M %Z")
        except Exception:
            budapest_time_str = start_str

    # Strategy badge aligned with the 10 Supreme Commandments ($0.50–$8.00 Kelly)
    is_defense = (stake <= 0.50) or ("capital defense" in str(tip.get("stake_note", "")).lower())
    if stake >= 5.0:
        tier_badge = "🔥 <b>SUPER ALPHA</b> ($5.00–$8.00 · High Conviction Alpha)"
    elif stake >= 2.50:
        tier_badge = "🚀 <b>ALPHA ACCELERATION</b> ($2.50–$4.99 · Positive CLV Scale)"
    elif is_defense or stake <= 0.50:
        tier_badge = "🛡️ <b>CAPITAL DEFENSE</b> ($0.50 Clamp · Turnaround Protocol)"
    else:
        tier_badge = "⚡ <b>STANDARD QUANT VALUE</b> ($1.00–$2.49 · Kelly Value)"

    # Stake navigation and tab
    nav = tip.get("navigation", {}) or {}
    stake_url = nav.get("event_url") or tip.get("reference_url") or "https://stake.com/sports"
    stake_tab = nav.get("tab") or infer_stake_tab(league, raw_market, market)

    poly_note = ""
    tip_scope = tip.get("model_scope") or ("full_game" if ":" not in str(tip.get("market", "")) else str(tip.get("market", "")).split(":")[0])
    poly_sensor = tip.get("game_context", {}).get("polymarket")
    if tip_scope == "full_game" and poly_sensor and isinstance(poly_sensor, dict) and poly_sensor.get("matched"):
        poly_note = f"\n🔮 <b>Polymarket CLOB:</b> Shadow Consensus Monitored (0 vig blend)"

    msg = (
        f"🎯 <b>NEW BET RECOMMENDATION</b>\n"
        f"{tier_badge}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏆 <b>League:</b> {league}\n"
        f"⚔️ <b>Matchup:</b> {away} @ {home}\n"
        f"⏰ <b>Start Time:</b> {budapest_time_str}\n"
        f"📊 <b>Market:</b> {market}\n"
        f"📌 <b>Selection:</b> <b>{selection}</b>\n"
        f"💰 <b>Suggested Stake:</b> <b>${stake:.2f} USD</b>\n"
        f"📈 <b>Stake Odds:</b> <b>{odds:.2f}</b> (Min. required: {effective_min:.2f})\n"
        f"🎲 <b>Model Win Prob:</b> {prob:.1f}% | <b>Expected EV:</b> {ev_str}\n"
        f"🛡️ <b>Stress EV:</b> {stress_str}\n"
        f"📍 <b>Stake Tab:</b> <b>{stake_tab}</b>"
        f"{poly_note}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"👉 <a href=\"{stake_url}\">Open Match on Stake ↗</a>"
    )
    return msg


def notify_new_recommendations(recommendations: List[Dict[str, Any]]) -> int:
    """Inspects recommendations and sends notifications for newly issued ones.
    
    Returns the number of newly notified recommendations.
    """
    if not recommendations:
        return 0

    notified_ids = _load_notified_ids()
    newly_sent = 0

    for tip in recommendations:
        tip_id = str(tip.get("id"))
        stake = float(tip.get("stake", 0.0))

        # Only notify funded, non-alternative executable instructions (stake >= $0.50 defense floor)
        if not tip_id or stake < 0.50 or tip.get("alternative"):
            continue

        if tip_id in notified_ids:
            continue

        msg = format_recommendation_message(tip)
        success = send_telegram_message(msg)
        if success:
            notified_ids.add(tip_id)
            newly_sent += 1
            log.info("Sent Telegram notification for tip %s (%s)", tip_id, tip.get("selection"))

    if newly_sent > 0:
        _save_notified_ids(notified_ids)

    return newly_sent


def send_test_notification() -> bool:
    """Sends a sample test recommendation message to confirm Telegram functionality."""
    sample_tip = {
        "id": "test_sample_notification_001",
        "league": "NFL",
        "away": "Kansas City Chiefs",
        "home": "Buffalo Bills",
        "start": datetime.now().isoformat(),
        "market": "spreads",
        "market_display": "Handicap (Incl. Overtime)",
        "selection": "Kansas City Chiefs +2.5",
        "stake": 3.50,
        "reference_odds": 1.95,
        "minimum_odds": 1.82,
        "probability": 0.564,
        "expected_roi": 0.0998,
        "stress_roi": 0.045,
        "navigation": {"event_url": "https://stake.com/sports", "tab": "Handicap"},
        "game_context": {"polymarket": {"matched": True}}
    }
    msg = format_recommendation_message(sample_tip)
    return send_telegram_message(f"🔔 <b>[TEST NOTIFICATION - SYSTEM ACTIVE]</b>\n\n{msg}")


ISSUES_PERSISTENCE_PATH = ROOT_DIR / "data" / "notified_telegram_issues.json"


def _load_notified_issues() -> set:
    if ISSUES_PERSISTENCE_PATH.exists():
        try:
            data = json.loads(ISSUES_PERSISTENCE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return set(data)
        except Exception as e:
            log.warning("Could not read notified issues persistence: %s", e)
    return set()


def _save_notified_issues(ids: set) -> None:
    try:
        ISSUES_PERSISTENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
        ISSUES_PERSISTENCE_PATH.write_text(json.dumps(list(ids), indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("Could not persist notified issues: %s", e)


def notify_system_issues(issues: List[Dict[str, Any]]) -> int:
    """Alerts the user via Telegram when actionable quarantined issues or feed failures occur."""
    failures = [i for i in issues if i.get('reason') in (
        'source_error', 'orderbook_error', 'pagination_limit', 'stake_schedule_error',
        'stake_fixture_error', 'stake_invalid_odds', 'stake_sports_registry_error',
        'stake_tournament_error', 'stake_fixture_schema', 'stake_fixture_identity_conflict',
        'stake_fixture_endpoint_error', 'stake_seed_identity_unresolved', 'stake_endpoint_market_difference'
    )]
    if not failures:
        return 0

    notified = _load_notified_issues()
    new_failures = []
    for f in failures:
        key = f"{f.get('league')}:{f.get('event') or f.get('fixture')}:{f.get('reason')}"
        if key not in notified:
            new_failures.append((key, f))

    if not new_failures:
        return 0

    lines = []
    for key, f in new_failures[:6]:
        league = f.get('league', 'API')
        event = f.get('event') or f.get('fixture') or 'Unknown event'
        reason = f.get('reason', 'feed_error')
        detail = f.get('detail', '')
        lines.append(f"• <b>[{league}]</b> {event}\n  <i>Reason: {reason} — {detail}</i>")

    msg = (
        "⚠️ <b>[QUANT ENGINE - SYSTEM REVIEW REQUIRED]</b>\n\n"
        "EvidenceGuard safety gate has <b>quarantined</b> the following event(s) due to data discrepancy (no funded stake was placed):\n\n"
        + "\n\n".join(lines) +
        f"\n\n🛡️ <i>All other markets and sports are running uninterrupted. See the dashboard for details.</i>"
    )

    success = send_telegram_message(msg)
    if success:
        for key, _ in new_failures:
            notified.add(key)
        _save_notified_issues(notified)
        log.info("Dispatched Telegram alert for %d system issues", len(new_failures))
        return len(new_failures)
    return 0
