"""Suivi de tendance par cassure de canal (H4 ou D1) — la famille des « Turtles ».

Idée : acheter un nouveau plus haut de N bougies, vendre un nouveau plus bas, et
laisser courir les gains avec un stop suiveur. Peu de trades, gains rares mais
gros : une approche qui a historiquement bien marché sur l'or, actif à tendances.

- Entrée : clôture au-dessus du plus haut des `entry` bougies précédentes (ou sous le plus bas).
- Filtre : prix du bon côté de l'EMA longue (on ne joue que dans le sens de la tendance de fond).
- Filtre de régime (activé par défaut) : on n'achète que si le prix est au-dessus de sa moyenne
  200 jours et on ne vend que s'il est en dessous. Validé en walk-forward : profit factor 1,50 → 1,76.
- Stop initial : `sl_atr` ATR ; sortie : cassure inverse du canal `exit` OU stop suiveur ATR.

Réglages par défaut = ceux que le walk-forward H4 a choisis presque à chaque fenêtre (20 / 10 / 200 / 2,0).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..indicators import atr, donchian, ema, higher_tf
from .base import Management, Strategy


@dataclass
class DonchianTrend(Strategy):
    name: str = "donchian_trend"
    params: dict = field(default_factory=lambda: {"entry": 20, "exit": 10, "trend_ema": 200, "sl_atr": 2.0,
                                                  "regime_filter": True})
    management: Management = field(default_factory=lambda: Management(trail_atr=3.0, trail_after_r=1.0))

    def signals(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        out = self.frame(df.index)
        a = atr(df, 20)
        hi, lo = donchian(df, p["entry"])
        xhi, xlo = donchian(df, p["exit"])
        trend = ema(df["close"], p["trend_ema"])
        long_ = (df["close"] > hi) & (df["close"] > trend)
        short = (df["close"] < lo) & (df["close"] < trend)
        if p.get("regime_filter"):
            # régime de fond journalier (EMA 200 jours) : on ne joue que dans son sens
            d1 = higher_tf(df, "1D", lambda b: ema(b["close"], 200)) if _intraday(df) else ema(df["close"], 200)
            d1_up = df["close"] > d1
            long_ &= d1_up | d1.isna()
            short &= ~d1_up & d1.notna()
        out.loc[long_, "signal"] = 1
        out.loc[short, "signal"] = -1
        out["sl"] = np.where(out["signal"] != 0, p["sl_atr"] * a, np.nan)
        out["exit_long"] = df["close"] < xlo
        out["exit_short"] = df["close"] > xhi
        out["atr"] = a
        return out


def _intraday(df: pd.DataFrame) -> bool:
    return len(df) > 2 and (df.index[1] - df.index[0]) < pd.Timedelta("1D")
