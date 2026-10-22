"""Sport-only records. Market information cannot enter the model feature schema."""
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Literal
import math

PRO_LEAGUES = ('NBA', 'MLB', 'NHL', 'NFL', 'WNBA')
COLLEGE_LEAGUES = ('NCAAF','NCAAM','NCAAW')
SOCCER_LEAGUES = ('EPL', 'UCL', 'MLS')
TENNIS_LEAGUES = ('ATP', 'WTA')
LEAGUES = PRO_LEAGUES + COLLEGE_LEAGUES + SOCCER_LEAGUES + TENNIS_LEAGUES

def utcnow():
    return datetime.now(timezone.utc)

def parse_time(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)

@dataclass
class Game:
    id: str
    league: str
    start: str
    home: str
    away: str
    home_id: str
    away_id: str
    home_abbr: str
    away_abbr: str
    state: str
    season: int
    home_score: float | None = None
    away_score: float | None = None
    neutral: bool = False
    game_type: str = 'regular'
    source: str = ''
    fetched_at: str = ''
    context: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.league not in LEAGUES: raise ValueError('Unsupported league')
        parse_time(self.start)
        if self.home_id == self.away_id: raise ValueError('Same opponent')
        if self.state == 'final' and (self.home_score is None or self.away_score is None):
            raise ValueError('Final game must have scores')
        if any(s is not None and (not math.isfinite(s) or s < 0 or s!=int(s)) for s in (self.home_score,self.away_score)):
            raise ValueError('Scores must be finite nonnegative integers')

    def dict(self): return asdict(self)
