"""Indicateurs techniques (pandas/numpy, sans dépendance externe).

Tous sont calculés uniquement avec le passé : la valeur à la bougie i n'utilise
que les bougies ≤ i. Les stratégies décident à la CLÔTURE de i et entrent à
l'OUVERTURE de i+1 : aucun regard vers le futur.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def true_range(df: pd.DataFrame) -> pd.Series:
    prev = df["close"].shift(1)
    return pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """ATR de Wilder."""
    return true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(50)


def adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    up = df["high"].diff()
    dn = -df["low"].diff()
    plus_dm = np.where((up > dn) & (up > 0), up, 0.0)
    minus_dm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = true_range(df).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    pdi = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / tr
    mdi = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / n, adjust=False, min_periods=n).mean() / tr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def donchian(df: pd.DataFrame, n: int) -> tuple[pd.Series, pd.Series]:
    """Plus haut / plus bas des n bougies PRÉCÉDENTES (exclut la bougie courante)."""
    return df["high"].rolling(n).max().shift(1), df["low"].rolling(n).min().shift(1)


def higher_tf(df: pd.DataFrame, rule: str, fn) -> pd.Series:
    """Calcule un indicateur sur une unité de temps supérieure (ex. H4) et le ramène sur les bougies H1
    sans regarder le futur : une bougie H4 n'est utilisée qu'une fois CLOSE."""
    big = df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna(subset=["open"])
    val = fn(big)
    # disponible à la fin de la bougie H4 → décalée d'une période puis propagée
    val.index = val.index + pd.Timedelta(rule)
    return val.reindex(df.index.union(val.index)).ffill().reindex(df.index)
