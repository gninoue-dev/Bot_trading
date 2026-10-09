"""Tests de GoldBot. Lancer : .venv/bin/python -m pytest -q"""

from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from goldbot.backtest import Costs, RiskRules, run
from goldbot.config import Config, MT5Config, RiskConfig, load
from goldbot.risk import RiskGuard, SymbolSpec, position_size
from goldbot.strategies import DonchianTrend, SessionBreakout, TrendPullback
from goldbot.strategies.base import Management, Strategy


def synthetic(n: int = 3000, seed: int = 3, freq: str = "4h") -> pd.DataFrame:
    """Marché synthétique avec tendances et retournements (pas besoin d'internet)."""
    rng = np.random.default_rng(seed)
    drift = np.repeat(rng.choice([-1, 1], size=n // 300 + 1) * 0.8, 300)[:n]
    ret = drift + rng.normal(0, 6, n)
    close = 2000 + np.cumsum(ret)
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + rng.uniform(0, 5, n)
    low = np.minimum(open_, close) - rng.uniform(0, 5, n)
    idx = pd.date_range("2022-01-03", periods=n, freq=freq, tz="UTC")
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": 1000.0}, index=idx)


# ── pas de regard vers le futur ───────────────────────────

@pytest.mark.parametrize("strategy", [DonchianTrend(), TrendPullback(), SessionBreakout()])
def test_signals_never_use_future_bars(strategy):
    df = synthetic(2500, freq="1h")
    full = strategy.signals(df)
    cut = 1800
    partial = strategy.signals(df.iloc[:cut])
    a, b = full.iloc[:cut], partial
    assert (a["signal"].to_numpy() == b["signal"].to_numpy()).all(), "un signal passé change quand on ajoute le futur"
    np.testing.assert_allclose(a["sl"].to_numpy(), b["sl"].to_numpy(), equal_nan=True)


# ── backtest pessimiste ───────────────────────────────────

class OneShot(Strategy):
    """Un seul achat à la bougie 1, stop/objectif fixes : pour vérifier l'exécution."""

    def __init__(self, sl: float, tp: float):
        super().__init__("oneshot", {}, Management(exit_on_opposite=False))
        self._sl, self._tp = sl, tp

    def signals(self, df):
        out = self.frame(df.index)
        out.iloc[1, out.columns.get_loc("signal")] = 1
        out["sl"], out["tp"], out["atr"] = self._sl, self._tp, 5.0
        return out


def bars(rows, start="2024-01-02 00:00"):
    idx = pd.date_range(start, periods=len(rows), freq="1h", tz="UTC")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx).assign(volume=1.0)


NO_COST = Costs(spread=0.0, slippage=0.0, swap_long_pct=0.0, swap_short_pct=0.0)


def test_stop_has_priority_when_both_hit_same_bar():
    df = bars([[100, 101, 99, 100], [100, 101, 99, 100], [100, 120, 80, 100], [100, 101, 99, 100]])
    res = run(df, OneShot(sl=10, tp=10), 10_000, NO_COST)
    assert res.trades.iloc[0]["reason"] == "stop"
    assert res.trades.iloc[0]["pnl"] < 0


def test_gap_through_stop_fills_at_worse_open():
    df = bars([[100, 101, 99, 100], [100, 101, 99, 100], [100, 101, 99, 100], [70, 72, 69, 71]])
    res = run(df, OneShot(sl=10, tp=50), 10_000, NO_COST)
    t = res.trades.iloc[0]
    assert t["reason"] == "stop (gap)" and t["exit"] == pytest.approx(70)
    assert t["r"] < -2.5, "le gap fait perdre plus que le risque prévu"


def test_position_risks_one_percent():
    df = bars([[100, 101, 99, 100], [100, 101, 99, 100], [100, 101, 85, 100], [100, 101, 99, 100]])
    res = run(df, OneShot(sl=10, tp=50), 10_000, NO_COST, RiskRules(risk_pct=0.01))
    t = res.trades.iloc[0]
    assert t["lots"] == pytest.approx(0.1)          # 100 $ de risque / (10 $ × 100 onces)
    assert t["pnl"] == pytest.approx(-100, abs=1)


def test_costs_reduce_profit():
    df = synthetic(2000)
    clean = run(df, DonchianTrend(), costs=NO_COST)
    real = run(df, DonchianTrend(), costs=Costs())
    assert real.equity.iloc[-1] < clean.equity.iloc[-1]


def test_swaps_triple_on_wednesday_none_on_weekend():
    # position longue tenue du lundi au lundi suivant (H1) : 5 rollovers en semaine, dont mercredi ×3 = 7 nuits
    idx = pd.date_range("2024-01-08 00:00", "2024-01-15 23:00", freq="1h", tz="UTC")
    idx = idx[(idx.weekday < 5)]
    px = np.full(len(idx), 100.0)
    df = pd.DataFrame({"open": px, "high": px + 0.5, "low": px - 0.5, "close": px, "volume": 1.0}, index=idx)
    costs = Costs(spread=0, slippage=0, swap_long_pct=-0.036, swap_short_pct=0)
    res = run(df, OneShot(sl=50, tp=500), 10_000, costs)
    t = res.trades.iloc[0]
    per_night = 0.036 / 360 * 100 * 100 * t["lots"]
    assert t["swap"] == pytest.approx(-per_night * 8, rel=0.02)   # lun, mar, mer×3, jeu, ven(→ lundi), + lundi soir


# ── garde-fous ────────────────────────────────────────────

SPEC = SymbolSpec("XAUUSD", 0.01, 1.0, 0.01, 0.01, 50.0, 2, 0, 0.01)


def test_position_size_rounding_and_min_lot_rule():
    assert position_size(10_000, 0.01, 50, SPEC) == pytest.approx(0.02)
    assert position_size(1_000, 0.01, 50, SPEC) == 0.0, "le lot mini risquerait 5 % : on n'entre pas"
    assert position_size(4_000, 0.01, 50, SPEC) == pytest.approx(0.01), "lot mini accepté (risque ≤ 1,5 × prévu)"


def test_guard_halts_on_drawdown_and_blocks_entries(tmp_path):
    g = RiskGuard(RiskConfig(max_drawdown=0.10), tmp_path / "risk.json")
    now = datetime(2026, 3, 10, 10, tzinfo=timezone.utc)
    g.update(10_000, now)
    alerts = g.update(8_900, now)
    assert g.state.halted and alerts
    assert not g.can_enter(8_900, 0.3, now)[0]
    assert RiskGuard(RiskConfig(), tmp_path / "risk.json").state.halted, "l'arrêt survit au redémarrage"


def test_guard_daily_loss_spread_friday_and_nfp(tmp_path):
    g = RiskGuard(RiskConfig(max_daily_loss=0.03, max_spread=0.8), tmp_path / "r.json")
    tue = datetime(2026, 3, 10, 10, tzinfo=timezone.utc)
    g.update(10_000, tue)
    assert not g.can_enter(9_600, 0.3, tue)[0]
    assert not g.can_enter(10_000, 1.5, tue)[0]
    assert not g.can_enter(10_000, 0.3, datetime(2026, 3, 13, 19, tzinfo=timezone.utc))[0]   # vendredi soir
    nfp = datetime(2026, 3, 6, 12, 0, tzinfo=timezone.utc)                                   # 1er vendredi de mars
    assert g.in_news_blackout(nfp)
    assert g.can_enter(10_000, 0.3, tue)[0]


def test_config_refuses_crazy_risk(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('mode = "live"\n[risk]\nrisk_pct = 0.10\n')
    with pytest.raises(ValueError):
        load(p)


# ── moteur réel rejoué ≈ backtest ─────────────────────────

def test_engine_replay_matches_backtest():
    import logging
    from goldbot.broker.paper import PaperBroker
    from goldbot.live import Engine
    logging.getLogger("goldbot").setLevel(logging.WARNING)
    df = synthetic(2200)
    cfg = Config(strategy={"name": "donchian_trend", "timeframe": "H4", "regime_filter": False},
                 risk=RiskConfig(risk_pct=0.01, max_spread=5, news_blackout_minutes=0, no_entry_friday_after_utc=99))
    br = PaperBroker(df, 10_000, spread=0.45)
    br.cursor = 300
    eng = Engine(cfg, br, Path(tempfile.mkdtemp()))
    while True:
        eng.tick()
        if not br.step():
            break
    bt = run(df, DonchianTrend(params={**DonchianTrend().params, "regime_filter": False}),
             costs=Costs(swap_long_pct=0, swap_short_pct=0, slippage=0))
    n_bt = int((pd.to_datetime(bt.trades["entry_time"]) >= df.index[301]).sum())
    assert len(br.history) > 10
    assert abs(len(br.history) - n_bt) <= max(3, 0.15 * n_bt)


# ── connexion MetaTrader 5 (faux module) ──────────────────

class FakeMT5:
    TIMEFRAME_M15, TIMEFRAME_M30, TIMEFRAME_H1, TIMEFRAME_H4, TIMEFRAME_D1 = 15, 30, 16385, 16388, 16408
    POSITION_TYPE_BUY, POSITION_TYPE_SELL = 0, 1
    ORDER_TYPE_BUY, ORDER_TYPE_SELL = 0, 1
    TRADE_ACTION_DEAL, TRADE_ACTION_SLTP = 1, 6
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK, ORDER_FILLING_IOC, ORDER_FILLING_RETURN = 0, 1, 2
    TRADE_RETCODE_DONE, TRADE_RETCODE_REQUOTE, TRADE_RETCODE_PRICE_CHANGED = 10009, 10004, 10020

    def __init__(self):
        import time
        self.sent = []
        self.requotes = 1
        self.now = time.time() + 3 * 3600   # serveur HFM à GMT+3

    def initialize(self, **kw): return True
    def shutdown(self): pass
    def last_error(self): return (0, "ok")
    def terminal_info(self): return SimpleNamespace(trade_allowed=True)
    def symbol_info(self, name):
        if name != "XAUUSD.r":
            return None
        return SimpleNamespace(name=name, trade_tick_size=0.01, trade_tick_value=1.0, volume_min=0.01, volume_step=0.01,
                               volume_max=50, digits=2, trade_stops_level=50, point=0.01, filling_mode=2)
    def symbols_get(self, pattern): return [SimpleNamespace(name="XAUUSD.r")]
    def symbol_select(self, name, on): return True
    def symbol_info_tick(self, name): return SimpleNamespace(bid=4200.0, ask=4200.4, time=int(self.now))
    def account_info(self): return SimpleNamespace(equity=10_000.0, balance=10_000.0)
    def positions_get(self, symbol):
        return [SimpleNamespace(ticket=1, type=0, volume=0.1, price_open=4190, sl=4150, tp=0, time=0, profit=50, magic=770077),
                SimpleNamespace(ticket=2, type=1, volume=1.0, price_open=4210, sl=4250, tp=0, time=0, profit=-10, magic=1)]
    def copy_rates_from_pos(self, symbol, tf, start, count):
        t0 = 1_700_000_000
        return np.array([(t0 + i * 14400, 1.0, 2.0, 0.5, 1.5, 10, 0, 10) for i in range(5)],
                        dtype=[("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8"),
                               ("tick_volume", "i8"), ("spread", "i4"), ("real_volume", "i8")])
    def order_send(self, req):
        self.sent.append(dict(req))
        if self.requotes:
            self.requotes -= 1
            return SimpleNamespace(retcode=self.TRADE_RETCODE_REQUOTE, comment="requote", price=0)
        return SimpleNamespace(retcode=self.TRADE_RETCODE_DONE, comment="done", price=req.get("price", 0), order=99, deal=99)


def test_mt5_broker_detects_symbol_offset_and_sends_safe_orders():
    from goldbot.broker.mt5 import MT5Broker
    fake = FakeMT5()
    br = MT5Broker(MT5Config(), fake)
    br.connect()
    assert br.symbol == "XAUUSD.r"
    assert br.offset_h == 3
    assert [p.ticket for p in br.positions()] == [1], "seuls les trades du bot (magic) sont gérés"
    bars = br.bars("H4", 5)
    assert len(bars) == 4 and str(bars.index.tz) == "UTC", "bougie en cours exclue, heure ramenée en UTC"
    res = br.open(1, 0.05, sl=4199.9, tp=None, comment="test")
    assert res.ok and len(fake.sent) == 2, "un requote est retenté"
    req = fake.sent[-1]
    assert req["type_filling"] == fake.ORDER_FILLING_IOC
    assert req["magic"] == 770077
    assert req["sl"] <= 4200.4 - 0.55 + 1e-6, "stop éloigné pour respecter le stops level du broker"
