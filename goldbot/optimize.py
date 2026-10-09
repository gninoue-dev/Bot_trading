"""Optimisation honnête : walk-forward + choix ROBUSTE des paramètres + Monte Carlo.

Pourquoi : n'importe quelle stratégie peut paraître géniale si on choisit ses
réglages après coup sur les mêmes données (« sur-optimisation »). Le walk-forward
simule ce qui se passera vraiment :
  1. on choisit les paramètres sur une fenêtre passée (in-sample),
  2. on les applique tels quels sur la fenêtre suivante, jamais vue (out-of-sample),
  3. on avance et on recommence.
Seuls les résultats out-of-sample, mis bout à bout, comptent.

Choix robuste : au lieu du meilleur jeu de paramètres (souvent un coup de chance),
on prend celui dont les VOISINS (paramètres proches) sont aussi bons : une zone stable.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .backtest import Costs, Result, RiskRules, run
from .metrics import summary
from .strategies.base import Strategy


def grid(space: dict[str, list]) -> list[dict]:
    keys = list(space)
    return [dict(zip(keys, vals)) for vals in itertools.product(*(space[k] for k in keys))]


def score(res: Result, min_trades: int = 8) -> float:
    """Objectif : rendement ajusté du risque, pénalisé si trop peu de trades (résultat non significatif)."""
    s = summary(res)
    if s["trades"] < min_trades:
        return -9.0
    dd = abs(s["drawdown_max_%"]) or 1.0
    return s["rendement_annuel_%"] / max(dd, 3.0) + 0.25 * s["sharpe"]


def robust_pick(table: pd.DataFrame, space: dict[str, list]) -> dict:
    """Paramètres dont le voisinage (±1 cran sur chaque axe) a le meilleur score MÉDIAN."""
    keys = list(space)
    idx = {k: {v: i for i, v in enumerate(space[k])} for k in keys}
    best, best_val = None, -np.inf
    for _, row in table.iterrows():
        neigh = table
        for k in keys:
            pos = idx[k][row[k]]
            allowed = space[k][max(0, pos - 1): pos + 2]
            neigh = neigh[neigh[k].isin(allowed)]
        val = float(neigh["score"].median())
        if val > best_val:
            # on renvoie les valeurs d'origine (pandas convertit les entiers en décimaux)
            best_val, best = val, {k: next(v for v in space[k] if v == row[k]) for k in keys}
    return best


@dataclass
class WalkForward:
    oos_equity: pd.Series
    oos_trades: pd.DataFrame
    windows: pd.DataFrame


def walk_forward(df: pd.DataFrame, make: type[Strategy], space: dict[str, list], fixed: dict | None = None,
                 train_months: int = 12, test_months: int = 3, initial: float = 10_000.0,
                 costs: Costs | None = None, risk: RiskRules | None = None, warmup_bars: int = 300) -> WalkForward:
    fixed = fixed or {}
    start = df.index[0] + pd.DateOffset(months=train_months)
    windows = []
    equity_parts = []
    trades_parts = []
    capital = initial
    t0 = start
    while t0 < df.index[-1]:
        t1 = t0 + pd.DateOffset(months=test_months)
        train = df[(df.index >= t0 - pd.DateOffset(months=train_months)) & (df.index < t0)]
        rows = []
        for params in grid(space):
            res = run(train, make(params={**fixed, **params}), initial, costs, risk)
            rows.append({**params, "score": score(res)})
        table = pd.DataFrame(rows)
        chosen = robust_pick(table, space)
        # on teste sur [t0, t1) en gardant un historique de chauffe pour les indicateurs
        hist_start = df.index[max(0, df.index.searchsorted(t0) - warmup_bars)]
        chunk = df[(df.index >= hist_start) & (df.index < t1)]
        strat = make(params={**fixed, **chosen})
        sig = strat.signals(chunk)
        sig.loc[sig.index < t0, "signal"] = 0     # pas d'entrée pendant la chauffe
        res = run(chunk, strat, capital, costs, risk, signals=sig)
        eq = res.equity[res.equity.index >= t0]
        tr = res.trades
        if len(tr):
            tr = tr[pd.to_datetime(tr["entry_time"]) >= t0]
        if len(eq):
            capital = float(eq.iloc[-1])
            equity_parts.append(eq)
        trades_parts.append(tr)
        windows.append({"debut_test": t0.date(), "fin_test": min(t1, df.index[-1]).date(), **chosen,
                        "trades": len(tr), "capital_fin": round(capital, 2)})
        t0 = t1
    equity = pd.concat(equity_parts) if equity_parts else pd.Series(dtype=float)
    trades = pd.concat([t for t in trades_parts if len(t)], ignore_index=True) if any(len(t) for t in trades_parts) else pd.DataFrame()
    return WalkForward(equity, trades, pd.DataFrame(windows))


def monte_carlo(trades: pd.DataFrame, risk_pct: float, n: int = 5000, horizon: int | None = None,
                seed: int = 1) -> dict:
    """Rejoue les trades (en R) dans un ordre aléatoire pour mesurer le PIRE probable :
    drawdowns et probabilité de perdre beaucoup, avec un risque `risk_pct` par trade."""
    if trades is None or not len(trades):
        return {}
    rng = np.random.default_rng(seed)
    r = trades["r"].to_numpy(float)
    horizon = horizon or len(r)
    dds, finals = [], []
    for _ in range(n):
        seq = rng.choice(r, size=horizon, replace=True)
        eq = np.cumprod(1 + seq * risk_pct)
        peak = np.maximum.accumulate(eq)
        dds.append(float((eq / peak - 1).min()))
        finals.append(float(eq[-1] - 1))
    dds, finals = np.asarray(dds), np.asarray(finals)
    return {
        "trades_simules": horizon,
        "drawdown_median_%": round(100 * float(np.median(dds)), 1),
        "drawdown_pire_5%_%": round(100 * float(np.percentile(dds, 5)), 1),
        "drawdown_pire_1%_%": round(100 * float(np.percentile(dds, 1)), 1),
        "rendement_median_%": round(100 * float(np.median(finals)), 1),
        "proba_perte_%": round(100 * float((finals < 0).mean()), 1),
        "proba_perte_20%_%": round(100 * float((finals < -0.20).mean()), 1),
    }
