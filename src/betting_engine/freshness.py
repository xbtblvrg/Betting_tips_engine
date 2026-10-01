"""Quote lifetime is independent of polling cadence."""
from .domain import parse_time

MAX_QUOTE_AGE_SECONDS=300


def fresh_quote(tip,now):
    try:
        return (-5 <= (now-parse_time(tip['quote_at'])).total_seconds() <= MAX_QUOTE_AGE_SECONDS
                and now < parse_time(tip['valid_until']) and now < parse_time(tip['start'])
                and not tip.get('withdrawn_at'))
    except (KeyError,TypeError,ValueError):return False
