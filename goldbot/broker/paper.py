"""Broker simulé : rejoue des bougies (ou suit des prix réels) sans argent en jeu.

Sert à deux choses :
- tester le moteur réel de bout en bout sans MetaTrader (tests automatiques, Linux) ;
- le mode « paper » : faire tourner le bot plusieurs semaines sur de vrais prix avant le réel.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from ..risk import SymbolSpec
from .base import Broker, OrderResult, Position


class PaperBroker(Broker):
    def __init__(self, data: pd.DataFrame, initial: float = 10_000.0, spread: float = 0.45,
                 symbol: str = "XAUUSD", contract: float = 100.0, leverage: float = 500.0, currency: str = "USD"):
        self.leverage = leverage
        self.ccy = currency
        self.data = data
        self.symbol = symbol
        self.cash = initial
        self.spread = spread
        self.contract = contract
        self.cursor = 0           # index de la bougie EN COURS (les précédentes sont closes)
        self._pos: list[Position] = []
        self._next = 1
        self.history: list[dict] = []

    # le temps avance quand le test (ou le mode paper) appelle step()
    def step(self) -> bool:
        self._check_stops()
        if self.cursor + 1 >= len(self.data):
            return False
        self.cursor += 1
        return True

    def _bar(self):
        return self.data.iloc[self.cursor]

    def connect(self) -> None: ...
    def shutdown(self) -> None: ...

    def spec(self) -> SymbolSpec:
        return SymbolSpec(self.symbol, 0.01, 1.0, 0.01, 0.01, 50.0, 2, 0, 0.01)

    def now(self) -> datetime:
        return self.data.index[self.cursor].to_pydatetime()

    def bid_ask(self) -> tuple[float, float]:
        px = float(self._bar()["open"])
        return px - self.spread / 2, px + self.spread / 2

    def bars(self, timeframe: str, count: int) -> pd.DataFrame:
        closed = self.data.iloc[max(0, self.cursor - count): self.cursor]
        return closed.copy()

    def positions(self) -> list[Position]:
        bid, ask = self.bid_ask()
        for p in self._pos:
            p.profit = ((bid if p.direction > 0 else ask) - p.price_open) * p.direction * p.volume * self.contract
        return list(self._pos)

    def balance(self) -> float:
        return self.cash

    def currency(self) -> str:
        return self.ccy

    def margin_required(self, direction: int, volume: float, price: float) -> float:
        return volume * self.contract * price / self.leverage

    def free_margin(self) -> float:
        used = sum(self.margin_required(p.direction, p.volume, p.price_open) for p in self._pos)
        return self.equity() - used

    def equity(self) -> float:
        return self.cash + sum(p.profit for p in self.positions())

    def open(self, direction: int, volume: float, sl: float, tp: float | None, comment: str) -> OrderResult:
        bid, ask = self.bid_ask()
        price = ask if direction > 0 else bid
        pos = Position(self._next, direction, volume, price, sl, tp or 0.0, self.now())
        self._next += 1
        self._pos.append(pos)
        return OrderResult(True, pos.ticket, price, "ok")

    def modify_sl(self, ticket: int, sl: float, tp: float | None) -> OrderResult:
        for p in self._pos:
            if p.ticket == ticket:
                p.sl = sl
                return OrderResult(True, ticket, 0.0, "ok")
        return OrderResult(False, message="position introuvable")

    def close(self, ticket: int, comment: str, price: float | None = None) -> OrderResult:
        for p in list(self._pos):
            if p.ticket == ticket:
                bid, ask = self.bid_ask()
                px = price if price is not None else (bid if p.direction > 0 else ask)
                pnl = (px - p.price_open) * p.direction * p.volume * self.contract
                self.cash += pnl
                self._pos.remove(p)
                self.history.append({"ticket": ticket, "direction": p.direction, "open": p.price_open, "close": px,
                                     "volume": p.volume, "pnl": pnl, "time": self.now(), "reason": comment})
                return OrderResult(True, ticket, px, "ok")
        return OrderResult(False, message="position introuvable")

    def _check_stops(self) -> None:
        """Pendant la bougie en cours : stop / objectif touchés ? (stop prioritaire, comme le backtest)."""
        bar = self._bar()
        half = self.spread / 2
        for p in list(self._pos):
            if p.direction > 0:
                if bar["low"] - half <= p.sl:
                    self.close(p.ticket, "stop", min(p.sl, bar["open"] - half))
                elif p.tp and bar["high"] - half >= p.tp:
                    self.close(p.ticket, "objectif", p.tp)
            else:
                if bar["high"] + half >= p.sl:
                    self.close(p.ticket, "stop", max(p.sl, bar["open"] + half))
                elif p.tp and bar["low"] + half <= p.tp:
                    self.close(p.ticket, "objectif", p.tp)
