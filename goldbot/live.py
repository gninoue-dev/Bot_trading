"""Le moteur du bot réel (et du mode papier).

À chaque nouvelle bougie close de l'unité de temps choisie (H4 par défaut) :
  1. garde-fous : suivi du capital, perte journalière, drawdown max (arrêt total) ;
  2. gestion des positions du bot : sortie stratégie, stop au prix d'entrée, stop suiveur ;
  3. éventuelle nouvelle entrée, seulement si tous les garde-fous sont verts.
Les règles sont EXACTEMENT celles du backtest (même code de stratégie, mêmes calculs de stop).
"""

from __future__ import annotations

import json
import logging
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import numpy as np

from .broker.base import Broker, OrderResult, Position
from .config import ROOT, Config
from .notify import Notifier
from .risk import RiskGuard, position_size
from .strategies import STRATEGIES
from .strategies.base import Strategy

log = logging.getLogger("goldbot")
HISTORY_BARS = 1500  # H4 : ~250 jours, assez pour la moyenne 200 jours du filtre de régime


def setup_logging(directory: Path = ROOT / "logs") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = RotatingFileHandler(directory / "goldbot.log", maxBytes=2_000_000, backupCount=10, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    log.handlers[:] = [fh, sh]
    log.setLevel(logging.INFO)


def build_strategy(cfg: Config) -> Strategy:
    cls = STRATEGIES[cfg.strategy.get("name", "donchian_trend")]
    base = cls()
    return cls(params={**base.params, **cfg.strategy_params()})


@dataclass
class TradeMemory:
    """Ce que le bot retient de chaque position (survit aux redémarrages)."""
    trades: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> "TradeMemory":
        return cls(json.loads(path.read_text())) if path.exists() else cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.trades, indent=2))


class Engine:
    def __init__(self, cfg: Config, broker: Broker, state_dir: Path = ROOT / "state", notifier: Notifier | None = None):
        self.cfg = cfg
        self.broker = broker
        self.strategy = build_strategy(cfg)
        self.state_dir = state_dir
        self.guard = RiskGuard(cfg.risk, state_dir / "risk.json")
        self.memory_path = state_dir / "trades.json"
        self.memory = TradeMemory.load(self.memory_path)
        self.notify = notifier or Notifier(cfg.notify)

    # ── une itération ─────────────────────────────────────
    def tick(self) -> str:
        """Renvoie un court résumé de ce qui s'est passé (pour les logs et les tests)."""
        now = self.broker.now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        equity = self.broker.equity()
        for alert in self.guard.update(equity, now):
            self._alert(alert)

        mine = self.broker.positions()
        if self.guard.state.halted:
            for p in mine:
                self._close(p, "arrêt d'urgence")
            return "arrêté"
        if self.guard.daily_loss_hit(equity) and mine:
            for p in mine:
                self._close(p, "perte journalière max")
            self._alert(f"⚠️ Perte journalière max atteinte ({equity:.2f} $) : positions fermées, reprise demain.")
            return "perte journalière"

        bars = self.broker.bars(self.cfg.timeframe, HISTORY_BARS)
        if len(bars) < 250:
            return "historique insuffisant"
        last = str(bars.index[-1])
        if last == self.guard.state.last_bar:
            return "attente"   # pas de nouvelle bougie close
        sig = self.strategy.signals(bars)
        row = sig.iloc[-1]
        atr_now = float(row["atr"]) if np.isfinite(row["atr"]) else float("nan")
        events = []

        # 1. sorties demandées par la stratégie
        for p in mine:
            if (p.direction > 0 and bool(row["exit_long"])) or (p.direction < 0 and bool(row["exit_short"])) \
                    or (self.strategy.management.exit_on_opposite and int(row["signal"]) == -p.direction):
                if self._close(p, "sortie stratégie").ok:
                    events.append(f"sortie #{p.ticket}")
        mine = self.broker.positions()

        # 2. gestion du stop (breakeven + suiveur), calculée sur les bougies closes depuis l'entrée
        for p in mine:
            new_sl = self._managed_stop(p, bars, atr_now)
            if new_sl is not None:
                res = self.broker.modify_sl(p.ticket, new_sl, p.tp or None)
                if res.ok:
                    events.append(f"stop #{p.ticket} → {new_sl:.2f}")
                    log.info("Stop ajusté #%s : %.2f → %.2f", p.ticket, p.sl, new_sl)

        # 3. entrée
        signal = int(row["signal"])
        if signal and len(mine) < self.cfg.risk.max_positions and not any(p.direction == signal for p in mine):
            bid, ask = self.broker.bid_ask()
            ok, why = self.guard.can_enter(equity, ask - bid, now)
            if not ok:
                log.info("Signal %s ignoré : %s", "ACHAT" if signal > 0 else "VENTE", why)
                events.append(f"signal ignoré ({why})")
            else:
                events.append(self._enter(signal, float(row["sl"]), float(row["tp"]), bid, ask, equity, last))

        self.guard.state.last_bar = last
        self.guard.state.save(self.guard.path)
        summary = "; ".join(e for e in events if e) or "rien à faire"
        log.info("Bougie %s close | capital %.2f | %s", last, equity, summary)
        return summary

    # ── actions ───────────────────────────────────────────
    def _enter(self, d: int, sl_dist: float, tp_dist: float, bid: float, ask: float, equity: float, bar: str) -> str:
        spec = self.broker.spec()
        lots = position_size(equity, self.cfg.risk.risk_pct, sl_dist, spec)
        if lots <= 0:
            msg = f"capital trop faible pour le lot minimum avec un stop de {sl_dist:.2f} $"
            log.warning("Entrée annulée : %s", msg)
            return "entrée annulée (" + msg + ")"
        price = ask if d > 0 else bid
        sl = round(price - d * sl_dist, spec.digits)
        tp = round(price + d * tp_dist, spec.digits) if np.isfinite(tp_dist) else None
        res = self.broker.open(d, lots, sl, tp, f"goldbot {self.strategy.name}")
        if not res.ok:
            self._alert(f"❌ Ordre refusé : {res.message}")
            return "ordre refusé"
        self.memory.trades[str(res.ticket)] = {"sl_dist": sl_dist, "entry": res.price or price, "bar": bar, "lots": lots}
        self.memory.save(self.memory_path)
        risk_usd = spec.loss_per_lot(sl_dist) * lots
        self._alert(f"{'🟢 ACHAT' if d > 0 else '🔴 VENTE'} or {lots:.2f} lot à {res.price or price:.2f} | stop {sl:.2f} "
                    f"| risque {risk_usd:.0f} $ ({100 * risk_usd / equity:.2f} %)")
        return f"entrée #{res.ticket}"

    def _close(self, p: Position, reason: str) -> OrderResult:
        res = self.broker.close(p.ticket, reason)
        if res.ok:
            self.memory.trades.pop(str(p.ticket), None)
            self.memory.save(self.memory_path)
            self._alert(f"⚪ Clôture #{p.ticket} ({reason}) : {p.profit:+.2f} $")
        else:
            self._alert(f"❌ Impossible de clôturer #{p.ticket} : {res.message}")
        return res

    def _managed_stop(self, p: Position, bars, atr_now: float) -> float | None:
        m = self.strategy.management
        info = self.memory.trades.get(str(p.ticket))
        if info is None:   # position inconnue (redémarrage sans mémoire) : on reconstruit le risque initial
            info = {"sl_dist": abs(p.price_open - p.sl) or atr_now, "entry": p.price_open}
            self.memory.trades[str(p.ticket)] = info
            self.memory.save(self.memory_path)
        r_dist = info["sl_dist"]
        t_open = p.time_open if p.time_open.tzinfo else p.time_open.replace(tzinfo=timezone.utc)
        started = bars.index[bars.index <= t_open]
        since = bars[bars.index >= started.max()] if len(started) else bars
        if not len(since):
            return None
        best = float(since["high"].max()) if p.direction > 0 else float(since["low"].min())
        gain = (best - p.price_open) * p.direction
        new = p.sl
        spec = self.broker.spec()
        if m.breakeven_r and gain >= m.breakeven_r * r_dist:
            bid, ask = self.broker.bid_ask()
            be = p.price_open + p.direction * (ask - bid)
            new = max(new, be) if p.direction > 0 else min(new, be)
        if m.trail_atr and gain >= m.trail_after_r * r_dist and np.isfinite(atr_now):
            trail = best - p.direction * m.trail_atr * atr_now
            new = max(new, trail) if p.direction > 0 else min(new, trail)
        new = round(new, spec.digits)
        improved = (new > p.sl + spec.point) if p.direction > 0 else (new < p.sl - spec.point)
        return new if improved else None

    def _alert(self, text: str) -> None:
        log.info(text)
        self.notify.send(text)

    # ── boucle ────────────────────────────────────────────
    def run_forever(self, poll_seconds: int = 20) -> None:
        self.broker.connect()
        spec = self.broker.spec()
        self._alert(f"🤖 GoldBot démarré ({self.cfg.mode}) sur {spec.name} | {self.strategy.describe()} | "
                    f"risque {100 * self.cfg.risk.risk_pct:.2f} %/trade | capital {self.broker.equity():.2f}")
        failures = 0
        while True:
            try:
                self.tick()
                failures = 0
                if self.guard.state.halted:
                    self._alert("🛑 GoldBot arrêté (drawdown max). Analyse la situation avant de relancer : goldbot reset")
                    return
            except KeyboardInterrupt:
                self._alert("👋 GoldBot arrêté manuellement (les positions ouvertes gardent leur stop chez le broker).")
                return
            except Exception as e:  # noqa: BLE001 - le bot ne doit jamais mourir sur une erreur réseau
                failures += 1
                log.error("Erreur : %s\n%s", e, traceback.format_exc())
                if failures in (3, 20):
                    self._alert(f"⚠️ GoldBot : {failures} erreurs d'affilée ({e}). Tentative de reconnexion…")
                try:
                    self.broker.shutdown()
                    time.sleep(5)
                    self.broker.connect()
                except Exception:  # noqa: BLE001
                    time.sleep(30)
            time.sleep(poll_seconds)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
