"""Cassure du range asiatique à l'ouverture de Londres (H1, intraday).

Idée : pendant la session asiatique l'or évolue souvent dans un petit range ;
l'arrivée des volumes européens puis américains provoque une cassure qui a
tendance à se prolonger dans la journée.

- Range : plus haut / plus bas entre range_start et range_end (UTC).
- Entrée : clôture au-delà du range (+ marge en ATR) entre range_end et last_entry.
- Filtre : range ni trop étroit ni trop large par rapport à l'ATR journalier.
- Stop : milieu du range (ou bord opposé), borné en ATR ; objectif en multiple du risque.
- Une seule position par jour ; clôture forcée en fin de journée.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..indicators import atr, higher_tf
from .base import Management, Strategy


@dataclass
class SessionBreakout(Strategy):
    name: str = "session_breakout"
    params: dict = field(default_factory=lambda: {
        "range_start": 0, "range_end": 7, "last_entry": 13, "buffer_atr": 0.1, "rr": 1.5,
        "min_range_d": 0.15, "max_range_d": 0.9, "stop": "mid",
    })
    management: Management = field(default_factory=lambda: Management(breakeven_r=1.0, exit_hour_utc=20))

    def signals(self, df: pd.DataFrame) -> pd.DataFrame:
        p = self.params
        out = self.frame(df.index)
        a = atr(df, 14)
        d_atr = higher_tf(df, "1D", lambda b: atr(b, 14))
        hour = df.index.hour
        day = df.index.normalize()

        in_range = (hour >= p["range_start"]) & (hour < p["range_end"])
        rh = df["high"].where(in_range).groupby(day).cummax()
        rl = df["low"].where(in_range).groupby(day).cummin()
        # range figé à la fin de la fenêtre, propagé sur le reste de la journée
        rh = rh.groupby(day).ffill()
        rl = rl.groupby(day).ffill()
        width = rh - rl
        ok_width = (width >= p["min_range_d"] * d_atr) & (width <= p["max_range_d"] * d_atr)
        window = (hour >= p["range_end"]) & (hour < p["last_entry"])

        up = window & ok_width & (df["close"] > rh + p["buffer_atr"] * a)
        dn = window & ok_width & (df["close"] < rl - p["buffer_atr"] * a)
        # une seule entrée par jour : la première cassure
        first = (up | dn) & ((up | dn).astype(int).groupby(day).cumsum() == 1)
        out.loc[up & first, "signal"] = 1
        out.loc[dn & first, "signal"] = -1

        mid = (rh + rl) / 2
        stop_ref_long = mid if p["stop"] == "mid" else rl
        stop_ref_short = mid if p["stop"] == "mid" else rh
        sl = np.where(out["signal"] > 0, df["close"] - stop_ref_long, stop_ref_short - df["close"])
        sl = np.clip(sl, 0.8 * a, 3.0 * a)
        out["sl"] = np.where(out["signal"] != 0, sl, np.nan)
        out["tp"] = out["sl"] * p["rr"]
        out["atr"] = a
        return out
