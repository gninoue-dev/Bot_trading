from .base import Management, Strategy
from .donchian_trend import DonchianTrend
from .session_breakout import SessionBreakout
from .trend_pullback import TrendPullback

STRATEGIES = {s.name: s for s in (TrendPullback, SessionBreakout, DonchianTrend)}

__all__ = ["Strategy", "Management", "TrendPullback", "SessionBreakout", "DonchianTrend", "STRATEGIES"]
