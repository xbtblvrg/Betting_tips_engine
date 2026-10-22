"""Pure offline engine assessment; no runtime discovery or service control.

Mirror the protected supervisor's completed feed-warning distinction while
retaining the journal's strict clocks. Caller evidence is not authenticated.
Source degradation remains an attention condition, but a fresh completed source
failure alone cannot be repaired by restarting a progressing process.
"""
from .development_acceptance import _time


VERSION = "offline-engine-health-v2"


def feed_degradation(health):
    """Recognize only the engine's exact structured completed-cycle warning."""
    if not isinstance(health, dict):
        return None
    state = health.get("engine")
    if not isinstance(state, dict) or state.get("running") is not False:
        return None
    feed = state.get("market_feed")
    if not isinstance(feed, dict):
        return None
    count, message = feed.get("error_count"), feed.get("message")
    if (feed.get("state") not in ("partial", "unavailable")
            or type(count) is not int or count <= 0
            or not isinstance(message, str) or not message
            or state.get("error") != message):
        return None
    return {key: feed[key] for key in ("state", "error_count", "message")}


def process_problem(health, now):
    """Source metadata never excuses stale, future or invalid progress."""
    try:
        state = (health or {}).get("engine", {})
        if not isinstance(state, dict):
            return "Invalid engine health"
        if state.get("error") and not state.get("running") and not feed_degradation(health):
            return "Engine refresh failed"
        running = state.get("running", False)
        if not isinstance(running, bool):
            return "Invalid engine running flag"
        stamp = state.get("started_at") if running else state.get("finished_at")
        age = (_time(now) - _time(stamp)).total_seconds()
        if age < 0:
            return "Future engine progress timestamp"
        if age > (900 if running else 300):
            return "Stale engine progress"
    except (TypeError, ValueError, AttributeError):
        return "Missing or invalid engine progress"
    return None


def assess(health, now):
    """Classification only; cadence, failure threshold and cooldown live in the journal.

    The feed field is retained diagnostic metadata even when progress is stale.
    Neither process health nor absence of a recognized warning proves coverage.
    """
    reason, feed = process_problem(health, now), feed_degradation(health)
    return {"version": VERSION, "process_reason": reason,
            "process_state": "degraded" if reason else "healthy",
            "failure_class": "process_failure" if reason else
                             "source_degradation" if feed else None,
            "market_feed": feed,
            "source_reason": "Market feed coverage degraded: " + feed["message"] if feed else None,
            "source_recovery_verified": False}
