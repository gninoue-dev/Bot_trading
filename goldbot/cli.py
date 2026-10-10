"""Ligne de commande : goldbot <commande>.

  goldbot backtest [--tf H4] [--risk 0.01] [--csv fichier]   → rapport HTML dans reports/
  goldbot walkforward [--tf H4]                              → validation hors échantillon + Monte Carlo
  goldbot fetch --source mt5 --tf H4                         → télécharge l'historique de TON broker
  goldbot run                                                → lance le bot (mode du config.toml)
  goldbot check                                              → diagnostic du compte (capital suffisant ? compte cent ?)
  goldbot status | reset                                     → état des garde-fous / redémarrage après arrêt
  Petit compte (compte cent) : goldbot --config config/petit_compte.example.toml backtest --tf H1 --capital 1000
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser

import pandas as pd

from .backtest import Costs, Result, RiskRules, run
from .config import ROOT, load
from .data import DATA_DIR, load_csv, research_data, resample, save_csv
from .metrics import summary
from .optimize import monte_carlo, walk_forward
from .report import write_report


def _data(args) -> pd.DataFrame:
    if args.csv:
        df = load_csv(args.csv)
        return df if args.tf in ("H1",) else resample(df, args.tf)
    return research_data(args.tf) if args.tf in ("H1", "D1") else resample(research_data("H1"), args.tf)


def _strategy(cfg, overrides: dict | None = None):
    from .live import build_strategy
    st = build_strategy(cfg)
    if overrides:
        st.params.update(overrides)
    return st


def cmd_backtest(args) -> None:
    cfg = load(args.config)
    df = _data(args)
    st = _strategy(cfg)
    costs = Costs(spread=args.spread)
    res = run(df, st, args.capital, costs, RiskRules(risk_pct=args.risk, max_daily_loss=cfg.risk.max_daily_loss,
                                                     max_drawdown=cfg.risk.max_drawdown,
                                                     max_risk_min_lot=cfg.risk.max_risk_pct_min_lot))
    s = summary(res)
    print(json.dumps(s, ensure_ascii=False, indent=2))
    out = write_report(res, ROOT / "reports" / f"backtest_{args.tf}.html", f"GoldBot — backtest {args.tf}")
    print(f"\n📊 Rapport : {out}")
    if args.open:
        webbrowser.open(out.as_uri())


def cmd_walkforward(args) -> None:
    from .strategies import STRATEGIES
    cfg = load(args.config)
    df = _data(args)
    cls = STRATEGIES[cfg.strategy.get("name", "donchian_trend")]
    space = {"entry": [20, 40, 55], "exit": [10, 20], "trend_ema": [50, 100, 200], "sl_atr": [2.0, 3.0]}
    train, test = (48, 12) if args.tf == "D1" else (12, 3)
    wf = walk_forward(df, cls, space, fixed={"regime_filter": cfg.strategy.get("regime_filter", True)},
                      train_months=train, test_months=test, initial=args.capital,
                      costs=Costs(spread=args.spread), risk=RiskRules(risk_pct=args.risk))
    res = Result(wf.oos_trades, wf.oos_equity, args.capital, "walk-forward (hors échantillon)", [])
    print(wf.windows.to_string(index=False))
    print(json.dumps(summary(res), ensure_ascii=False, indent=2))
    mc = {f"risque {r * 100:.1f} %": monte_carlo(wf.oos_trades, r, horizon=100) for r in (0.005, 0.01, 0.02)}
    print(json.dumps(mc, ensure_ascii=False, indent=2))
    extra = {f"Monte Carlo {k} : drawdown pire 5 %": f"{v.get('drawdown_pire_5%_%')} %" for k, v in mc.items()}
    out = write_report(res, ROOT / "reports" / f"walkforward_{args.tf}.html", f"GoldBot — walk-forward {args.tf}", extra)
    print(f"\n📊 Rapport : {out}")


def cmd_fetch(args) -> None:
    if args.source == "yahoo":
        df = research_data(args.tf, refresh=True)
        print(f"{len(df)} bougies {args.tf} (Yahoo, contrat GC=F) → {DATA_DIR}")
        return
    from .broker.mt5 import MT5Broker
    cfg = load(args.config)
    br = MT5Broker(cfg.mt5)
    br.connect()
    df = br.bars(args.tf, args.bars)
    path = save_csv(df, DATA_DIR / f"mt5_{br.symbol}_{args.tf}.csv")
    br.shutdown()
    print(f"{len(df)} bougies {args.tf} de {br.symbol} ({df.index[0]:%Y-%m-%d} → {df.index[-1]:%Y-%m-%d}) → {path}")
    print(f"Revalide la stratégie sur TES prix : goldbot walkforward --tf {args.tf} --csv {path}")


def cmd_run(args) -> None:
    from .broker.dryrun import DryRunBroker
    from .broker.mt5 import MT5Broker
    from .live import Engine, setup_logging
    cfg = load(args.config)
    setup_logging()
    broker = MT5Broker(cfg.mt5)
    if cfg.mode == "paper":
        broker = DryRunBroker(broker)
    elif not args.yes:
        print("⚠️  MODE RÉEL : le bot va passer de vrais ordres sur le compte MT5 connecté.")
        if input("Tape OUI pour confirmer : ").strip().upper() != "OUI":
            print("Annulé.")
            return
    Engine(cfg, broker).run_forever(args.poll)


def cmd_check(args) -> None:
    """Diagnostic du compte : le bot peut-il trader avec ce capital, ce compte, ce stop ?"""
    from .broker.mt5 import MT5Broker
    from .live import Engine
    cfg = load(args.config)
    broker = MT5Broker(cfg.mt5)
    broker.connect()
    ok, report = Engine(cfg, broker).diagnose()
    broker.shutdown()
    print(report)
    sys.exit(0 if ok else 1)


def cmd_status(args) -> None:
    from .risk import RiskState
    st = RiskState.load(ROOT / "state" / "risk.json")
    print(json.dumps(st.__dict__, ensure_ascii=False, indent=2))


def cmd_reset(args) -> None:
    from .risk import RiskGuard
    cfg = load(args.config)
    RiskGuard(cfg.risk, ROOT / "state" / "risk.json").reset()
    print("Garde-fous remis à zéro. Relance avec : goldbot run")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="goldbot", description="Bot de trading sur l'or (XAUUSD)")
    ap.add_argument("--config", default=None, help="chemin du config.toml")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--tf", default="H4", choices=["H1", "H4", "D1"])
        p.add_argument("--csv", default=None, help="historique CSV (export MT5…) au lieu de Yahoo")
        p.add_argument("--risk", type=float, default=0.01)
        p.add_argument("--capital", type=float, default=10_000.0)
        p.add_argument("--spread", type=float, default=0.45)

    p = sub.add_parser("backtest")
    common(p)
    p.add_argument("--open", action="store_true", help="ouvre le rapport dans le navigateur")
    p.set_defaults(fn=cmd_backtest)
    p = sub.add_parser("walkforward")
    common(p)
    p.set_defaults(fn=cmd_walkforward)
    p = sub.add_parser("fetch")
    p.add_argument("--source", default="yahoo", choices=["yahoo", "mt5"])
    p.add_argument("--tf", default="H4", choices=["H1", "H4", "D1"])
    p.add_argument("--bars", type=int, default=20_000)
    p.set_defaults(fn=cmd_fetch)
    p = sub.add_parser("run")
    p.add_argument("--poll", type=int, default=20)
    p.add_argument("--yes", action="store_true", help="pas de confirmation en mode réel (service Windows)")
    p.set_defaults(fn=cmd_run)
    sub.add_parser("check", help="diagnostic : ce compte peut-il trader ?").set_defaults(fn=cmd_check)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("reset").set_defaults(fn=cmd_reset)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
