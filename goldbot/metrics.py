"""Mesures de performance d'un backtest (ce qui compte vraiment pour protéger ton capital)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .backtest import Result


def summary(res: Result) -> dict:
    eq = res.equity
    t = res.trades
    daily = eq.resample("1D").last().dropna()
    rets = daily.pct_change().dropna()
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    final = float(eq.iloc[-1])
    total = final / res.initial - 1
    cagr = (final / res.initial) ** (1 / years) - 1 if final > 0 else -1.0
    dd = eq / eq.cummax() - 1
    max_dd = float(dd.min())
    # plus longue période sous le plus haut
    under = dd < 0
    longest = 0
    if under.any():
        groups = (~under).cumsum()
        spans = eq.index.to_series()[under].groupby(groups[under]).agg(["first", "last"])
        longest = int(((spans["last"] - spans["first"]).dt.days).max()) if len(spans) else 0
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0.0
    down = rets[rets < 0]
    sortino = float(rets.mean() / down.std() * np.sqrt(252)) if len(down) > 1 and down.std() > 0 else 0.0
    out = {
        "capital_final": round(final, 2),
        "rendement_total_%": round(100 * total, 2),
        "rendement_annuel_%": round(100 * cagr, 2),
        "drawdown_max_%": round(100 * max_dd, 2),
        "plus_long_creux_jours": longest,
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "trades": int(len(t)),
    }
    if len(t):
        wins = t[t["pnl"] > 0]
        losses = t[t["pnl"] <= 0]
        gross_win, gross_loss = wins["pnl"].sum(), -losses["pnl"].sum()
        months = max(years * 12, 1e-9)
        out.update({
            "trades_par_mois": round(len(t) / months, 1),
            "taux_reussite_%": round(100 * len(wins) / len(t), 1),
            "profit_factor": round(gross_win / gross_loss, 2) if gross_loss > 0 else float("inf"),
            "esperance_R": round(float(t["r"].mean()), 3),
            "gain_moyen_R": round(float(wins["r"].mean()), 2) if len(wins) else 0.0,
            "perte_moyenne_R": round(float(losses["r"].mean()), 2) if len(losses) else 0.0,
            "pertes_consecutives_max": _max_streak(t["pnl"].to_numpy() <= 0),
            "swaps_payes": round(float(t["swap"].sum()), 2),
            "duree_moyenne_bougies": round(float(t["bars"].mean()), 1),
        })
    out["evenements_risque"] = len(res.events)
    return out


def monthly_returns(res: Result) -> pd.Series:
    m = res.equity.resample("ME").last()
    first = pd.Series([res.initial], index=[m.index[0] - pd.offsets.MonthEnd(1)])
    return (pd.concat([first, m]).pct_change().dropna() * 100).round(2)


def _max_streak(flags: np.ndarray) -> int:
    best = cur = 0
    for f in flags:
        cur = cur + 1 if f else 0
        best = max(best, cur)
    return int(best)
