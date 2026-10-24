"""Quote lifetime is independent of polling cadence."""
from .domain import parse_time

MAX_QUOTE_AGE_SECONDS=300
ACTIVE_ENGINE_MAX_QUOTE_AGE_SECONDS=900


def fresh_quote(tip,now,max_age_seconds=MAX_QUOTE_AGE_SECONDS):
    try:
        age=(now-parse_time(tip['quote_at'])).total_seconds()
        if not (-5 <= age <= max_age_seconds):
            return False
        if now >= parse_time(tip['start']):
            return False
        if tip.get('withdrawn_at'):
            return False
        if max_age_seconds <= MAX_QUOTE_AGE_SECONDS:
            if now >= parse_time(tip['valid_until']):
                return False
        return True
    except (KeyError,TypeError,ValueError):return False
