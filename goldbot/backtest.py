"""Moteur de backtest bougie par bougie — volontairement PESSIMISTE.

Règles d'exécution (identiques à ce que fait le bot en réel) :
- décision à la clôture de la bougie i, exécution à l'OUVERTURE de i+1 ;
- achat au prix ask (= prix + ½ spread), vente au bid, + glissement sur les entrées et les stops ;
- si le stop ET l'objectif sont touchés dans la même bougie, on considère que le STOP a été touché d'abord ;
- gap d'ouverture au-delà du stop : sortie au prix d'ouverture (pire que le stop) ;
- les stops ajustés (breakeven, suiveur) ne s'appliquent qu'à partir de la bougie suivante ;
- swaps de nuit facturés à chaque passage du rollover (22 h UTC), triple le mercredi ;
- taille de position calculée sur le risque (% du capital), arrondie au pas de lot du broker ;
- limites de risque : perte journalière max, drawdown max (pause), comme en réel.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .strategies.base import Strategy


@dataclass
class Costs:
    spread: float = 0.45          # $ par once (écart bid/ask complet) — à vérifier sur ton compte HFM
    commission_lot: float = 0.0   # $ aller-retour par lot (0 sur compte Premium HFM, ~6–7 $ sur compte Zero)
    slippage: float = 0.10        # $ par once sur les entrées au marché et les stops
    # swaps exprimés en % annuel de la valeur de la position (s'adapte au prix de l'or).
    # Ordre de grandeur des brokers CFD : garder de l'or à l'achat coûte cher. À vérifier dans MT5 (spécification du symbole).
    swap_long_pct: float = -0.06
    swap_short_pct: float = -0.01
    contract: float = 100.0       # 1 lot XAUUSD = 100 onces
    lot_step: float = 0.01
    min_lot: float = 0.01
    max_lot: float = 50.0


@dataclass
class RiskRules:
    risk_pct: float = 0.01        # 1 % du capital risqué par trade
    max_daily_loss: float = 0.03  # -3 % sur la journée → plus d'entrée jusqu'au lendemain
    max_drawdown: float = 0.15    # -15 % depuis le plus haut → pause
    pause_days: int = 20          # durée de la pause après le drawdown max (le bot réel, lui, s'arrête net)
    max_risk_min_lot: float = 0.0   # petit compte : risque max au lot minimum (0 = 1,5 × risk_pct)


@dataclass
class Trade:
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    direction: int
    entry: float
    exit: float
    lots: float
    risk_usd: float
    pnl: float
    r: float
    reason: str
    bars: int
    swap: float


@dataclass
class Result:
    trades: pd.DataFrame
    equity: pd.Series
    initial: float
    strategy: str
    events: list

    def to_dict(self) -> dict:
        return {"strategy": self.strategy, "initial": self.initial, "final": float(self.equity.iloc[-1]),
                "trades": len(self.trades), "events": self.events}


def run(df: pd.DataFrame, strategy: Strategy, initial: float = 10_000.0, costs: Costs | None = None,
        risk: RiskRules | None = None, signals: pd.DataFrame | None = None) -> Result:
    costs = costs or Costs()
    risk = risk or RiskRules()
    sig = signals if signals is not None else strategy.signals(df)
    m = strategy.management

    o, h, l, c = (df[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    times = df.index
    hours = times.hour.to_numpy()
    days = times.normalize()
    s_sig = sig["signal"].to_numpy(int)
    s_sl = sig["sl"].to_numpy(float)
    s_tp = sig["tp"].to_numpy(float)
    s_atr = sig["atr"].to_numpy(float)
    ex_long = sig["exit_long"].to_numpy(bool)
    ex_short = sig["exit_short"].to_numpy(bool)
    n = len(df)

    half = costs.spread / 2
    # rollover (swap) à 21 h UTC : nombre de rollovers franchis entre deux bougies, mercredi = triple
    roll_idx = ((times - pd.Timedelta(hours=21)).floor("1D") - pd.Timestamp("1970-01-01", tz="UTC")).days.to_numpy()
    equity = initial
    peak = initial
    eq_curve = np.empty(n)
    trades: list[Trade] = []
    events: list[str] = []

    pos = 0          # +1 / -1 / 0
    entry = sl = tp = lots = risk_usd = sl_dist0 = 0.0
    entry_i = 0
    best = 0.0       # meilleur prix atteint depuis l'entrée
    swap_acc = 0.0
    day_start_eq = initial
    blocked_until_day = None
    paused_until = None
    cur_day = days[0]

    def close(i: int, price: float, reason: str) -> None:
        nonlocal pos, equity, swap_acc
        gross = (price - entry) * pos * lots * costs.contract
        pnl = gross - costs.commission_lot * lots + swap_acc
        equity += pnl
        trades.append(Trade(times[entry_i], times[i], pos, entry, price, lots, risk_usd, pnl,
                            pnl / risk_usd if risk_usd else 0.0, reason, i - entry_i, swap_acc))
        pos = 0
        swap_acc = 0.0

    for i in range(n):
        # ── changement de jour : remise à zéro de la limite journalière
        if days[i] != cur_day:
            cur_day = days[i]
            day_start_eq = equity + (_open_pnl(pos, entry, c[i - 1], lots, costs) if pos else 0.0)

        # ── swaps : un par rollover franchi depuis la bougie précédente (mercredi = triple)
        if pos and i > 0 and roll_idx[i] > roll_idx[i - 1]:
            rate = costs.swap_long_pct if pos > 0 else costs.swap_short_pct
            per = rate / 360 * c[i - 1] * costs.contract   # $ par lot et par nuit
            for k in range(roll_idx[i - 1] + 1, roll_idx[i] + 1):
                wd = (k + 3) % 7                      # jour 0 = jeudi 1/1/1970 ; lundi = 0
                if wd >= 5:
                    continue                          # pas de rollover facturé samedi / dimanche…
                mult = 3 if wd == 2 else 1            # … le week-end est facturé le mercredi (×3)
                swap_acc += per * lots * mult

        # ── 1. à l'ouverture : sorties demandées à la clôture précédente
        if pos and i > 0:
            want_exit = None
            if m.exit_on_opposite and s_sig[i - 1] == -pos:
                want_exit = "signal inverse"
            elif (pos > 0 and ex_long[i - 1]) or (pos < 0 and ex_short[i - 1]):
                want_exit = "sortie stratégie"
            elif m.max_bars and i - entry_i >= m.max_bars:
                want_exit = "durée max"
            elif m.exit_hour_utc >= 0 and (hours[i] >= m.exit_hour_utc or days[i] != days[entry_i]):
                want_exit = "fin de journée"
            if want_exit:
                close(i, o[i] - pos * (half + costs.slippage), want_exit)

        # ── 2. à l'ouverture : nouvelle entrée
        allowed = (blocked_until_day is None or days[i] > blocked_until_day) and (paused_until is None or times[i] >= paused_until)
        if not pos and i > 0 and s_sig[i - 1] != 0 and allowed and np.isfinite(s_sl[i - 1]) and s_sl[i - 1] > 0:
            d = s_sig[i - 1]
            px = o[i] + d * (half + costs.slippage)
            sl_dist = s_sl[i - 1]
            budget = equity * risk.risk_pct
            raw = budget / (sl_dist * costs.contract)
            size = np.floor(raw / costs.lot_step) * costs.lot_step
            cap = risk.max_risk_min_lot or 1.5 * risk.risk_pct
            if size < costs.min_lot and costs.min_lot * sl_dist * costs.contract <= equity * cap + 1e-9:
                size = costs.min_lot
            size = min(size, costs.max_lot)
            if size >= costs.min_lot:
                pos, entry, lots, entry_i = d, px, round(size, 2), i
                sl = px - d * sl_dist
                tp = px + d * s_tp[i - 1] if np.isfinite(s_tp[i - 1]) else np.nan
                risk_usd = sl_dist * lots * costs.contract
                sl_dist0 = sl_dist
                best = o[i]

        # ── 3. pendant la bougie : gap, stop, objectif (stop prioritaire).
        #    Les données sont des prix moyens : bid = prix - ½ spread, ask = prix + ½ spread.
        #    Un achat se clôture au bid, une vente à l'ask.
        if pos:
            if pos > 0:
                if o[i] - half <= sl and i > entry_i:
                    close(i, o[i] - half - costs.slippage, "stop (gap)")
                elif l[i] - half <= sl:
                    close(i, sl - costs.slippage, "stop")
                elif np.isfinite(tp) and h[i] - half >= tp:
                    close(i, tp, "objectif")
            else:
                if o[i] + half >= sl and i > entry_i:
                    close(i, o[i] + half + costs.slippage, "stop (gap)")
                elif h[i] + half >= sl:
                    close(i, sl + costs.slippage, "stop")
                elif np.isfinite(tp) and l[i] + half <= tp:
                    close(i, tp, "objectif")

        # ── 4. à la clôture : gestion du stop (applicable dès la bougie suivante)
        if pos:
            r_dist = sl_dist0
            best = max(best, h[i]) if pos > 0 else min(best, l[i])
            gain = (best - entry) * pos
            if m.breakeven_r and gain >= m.breakeven_r * r_dist:
                be = entry + pos * (costs.spread + costs.commission_lot / costs.contract)
                sl = max(sl, be) if pos > 0 else min(sl, be)
            if m.trail_atr and gain >= m.trail_after_r * r_dist and np.isfinite(s_atr[i]):
                trail = best - pos * m.trail_atr * s_atr[i]
                sl = max(sl, trail) if pos > 0 else min(sl, trail)

        # ── 5. capital et garde-fous
        open_pnl = _open_pnl(pos, entry, c[i], lots, costs) + swap_acc if pos else 0.0
        eq_now = equity + open_pnl
        peak = max(peak, eq_now)
        if eq_now < day_start_eq * (1 - risk.max_daily_loss) and (blocked_until_day is None or blocked_until_day < days[i]):
            if pos:
                close(i, c[i] - pos * (half + costs.slippage), "perte journalière max")
                eq_now = equity
            blocked_until_day = days[i]
            events.append(f"{times[i]:%Y-%m-%d} : limite de perte journalière atteinte")
        if eq_now < peak * (1 - risk.max_drawdown) and (paused_until is None or times[i] >= paused_until):
            if pos:
                close(i, c[i] - pos * (half + costs.slippage), "drawdown max")
                eq_now = equity
            paused_until = times[i] + pd.Timedelta(days=risk.pause_days)
            peak = eq_now  # nouveau départ après la pause
            events.append(f"{times[i]:%Y-%m-%d} : DRAWDOWN MAX — pause de {risk.pause_days} jours")
        eq_curve[i] = eq_now

    if pos:
        close(n - 1, c[-1] - pos * half, "fin des données")
        eq_curve[-1] = equity

    tdf = pd.DataFrame([asdict(t) for t in trades])
    return Result(tdf, pd.Series(eq_curve, index=times, name="equity"), initial, strategy.describe(), events)


def _open_pnl(pos: int, entry: float, price: float, lots: float, costs: Costs) -> float:
    exit_px = price - pos * costs.spread / 2
    return (exit_px - entry) * pos * lots * costs.contract
