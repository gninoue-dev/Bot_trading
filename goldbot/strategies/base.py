"""Interface commune des stratégies.

Une stratégie reçoit les bougies et renvoie, pour chaque bougie close, une intention :
- `signal` : +1 achat, -1 vente, 0 rien (exécuté à l'ouverture de la bougie suivante)
- `sl` : distance du stop en dollars (obligatoire : AUCUN trade sans stop)
- `tp` : distance de l'objectif en dollars (NaN = pas d'objectif fixe)
Et des règles de gestion de position communes (breakeven, trailing, sortie au temps).
Le MÊME code sert au backtest, au papier et au réel.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd


@dataclass
class Management:
    breakeven_r: float = 0.0       # >0 : stop remonté au prix d'entrée (+frais) quand le gain atteint X R
    trail_atr: float = 0.0         # >0 : stop suiveur à X ATR du plus haut/bas atteint
    trail_after_r: float = 0.0     # le stop suiveur ne s'active qu'au-delà de X R
    max_bars: int = 0              # >0 : sortie après X bougies
    exit_hour_utc: int = -1        # >=0 : clôture forcée à cette heure (stratégies intraday)
    exit_on_opposite: bool = True  # un signal inverse clôture la position


@dataclass
class Strategy:
    name: str = "base"
    params: dict = field(default_factory=dict)
    management: Management = field(default_factory=Management)

    def signals(self, df: pd.DataFrame) -> pd.DataFrame:  # pragma: no cover - interface
        raise NotImplementedError

    @staticmethod
    def frame(index: pd.Index) -> pd.DataFrame:
        return pd.DataFrame({"signal": np.zeros(len(index), dtype=int), "sl": np.nan, "tp": np.nan,
                             "atr": np.nan, "exit_long": False, "exit_short": False}, index=index)

    def describe(self) -> str:
        p = ", ".join(f"{k}={v}" for k, v in self.params.items())
        return f"{self.name}({p})"
