"""Suivi de tendance avec entrée sur repli (H1).

Idée : l'or tend à faire de longues tendances. On ne court pas après le prix :
on attend qu'il revienne vers sa moyenne (repli) dans le sens de la tendance de
fond, puis on entre quand il repart.

- Tendance de fond : EMA lente H4 + prix du bon côté de l'EMA 200 H1.
- Force : ADX au-dessus d'un seuil (on évite les marchés plats).
- Repli : le prix vient toucher la zone de l'EMA rapide.
- Déclencheur : bougie qui reclôture du bon côté de l'EMA rapide, RSI d'accord.
- Stop sous le plus bas récent (borné entre 1 et 3 ATR) ; objectif en multiple de R.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..indicators import adx, atr, ema, higher_tf, rsi
from .base import Management, Strategy


@dataclass
class TrendPullback(Strategy):
    name: str = "trend_pullback"
    params: dict = field(default_factory=lambda: {
        "fast": 20, "slow": 200, "h4_ema": 50, "adx_min": 20, "rr": 2.0, "sl_atr_min": 1.0,
        "sl_atr_max": 3.0, "hours": (7, 20),
    })
    management: Management = field(default_factory=lambda: Management(breakeven_r=1.0, trail_atr=2.5, trail_after_r=1.5))

    def signals(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        out = self.frame(df.index)
        a = atr(df, 14)
        fast = ema(df["close"], p["fast"])
        slow = ema(df["close"], p["slow"])
        h4 = higher_tf(df, "4h", lambda b: ema(b["close"], p["h4_ema"]))
        h4_slope = h4.diff(4)
        strength = adx(df, 14)
        r = rsi(df["close"], 14)
        hour = df.index.hour
        in_session = (hour >= p["hours"][0]) & (hour < p["hours"][1])

        up = (df["close"] > slow) & (df["close"] > h4) & (h4_slope > 0) & (strength > p["adx_min"])
        dn = (df["close"] < slow) & (df["close"] < h4) & (h4_slope < 0) & (strength > p["adx_min"])
        touched_up = df["low"].rolling(3).min() <= fast + 0.2 * a
        touched_dn = df["high"].rolling(3).max() >= fast - 0.2 * a
        trig_up = (df["close"] > fast) & (df["close"] > df["open"]) & (r > 50) & (df["close"].shift(1) <= fast.shift(1) + 0.3 * a)
        trig_dn = (df["close"] < fast) & (df["close"] < df["open"]) & (r < 50) & (df["close"].shift(1) >= fast.shift(1) - 0.3 * a)

        long_ = up & touched_up & trig_up & in_session
        short = dn & touched_dn & trig_dn & in_session
        out.loc[long_, "signal"] = 1
        out.loc[short, "signal"] = -1

        sl_long = (df["close"] - df["low"].rolling(5).min()) + 0.3 * a
        sl_short = (df["high"].rolling(5).max() - df["close"]) + 0.3 * a
        sl = np.where(out["signal"] > 0, sl_long, sl_short)
        sl = np.clip(sl, p["sl_atr_min"] * a, p["sl_atr_max"] * a)
        out["sl"] = np.where(out["signal"] != 0, sl, np.nan)
        out["tp"] = out["sl"] * p["rr"]
        out["atr"] = a
        return out
