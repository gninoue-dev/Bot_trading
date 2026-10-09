"""Mode « paper » sur prix réels : données et cotations MT5, mais ordres SIMULÉS (aucun envoi au broker)."""

from __future__ import annotations

from datetime import datetime, timezone

from .base import Broker, OrderResult, Position


class DryRunBroker(Broker):
    def __init__(self, real: Broker, initial: float | None = None, contract: float = 100.0):
        self.real = real
        self.symbol = getattr(real, "symbol", "XAUUSD")
        self.initial = initial
        self.cash = 0.0
        self.contract = contract
        self._pos: list[Position] = []
        self._next = 1

    def connect(self) -> None:
        self.real.connect()
        self.symbol = self.real.symbol
        if self.initial is None:
            self.initial = self.real.balance()
        if not self.cash:
            self.cash = float(self.initial)

    def shutdown(self) -> None:
        self.real.shutdown()

    def spec(self):
        return self.real.spec()

    def now(self) -> datetime:
        return self.real.now()

    def bid_ask(self):
        return self.real.bid_ask()

    def bars(self, timeframe, count):
        return self.real.bars(timeframe, count)

    def _mark(self) -> None:
        bid, ask = self.bid_ask()
        for p in list(self._pos):
            px = bid if p.direction > 0 else ask
            p.profit = (px - p.price_open) * p.direction * p.volume * self.contract
            hit_sl = (p.direction > 0 and bid <= p.sl) or (p.direction < 0 and ask >= p.sl)
            hit_tp = p.tp and ((p.direction > 0 and bid >= p.tp) or (p.direction < 0 and ask <= p.tp))
            if hit_sl or hit_tp:
                self.close(p.ticket, "stop" if hit_sl else "objectif")

    def positions(self):
        self._mark()
        return list(self._pos)

    def balance(self) -> float:
        return self.cash

    def equity(self) -> float:
        return self.cash + sum(p.profit for p in self.positions())

    def open(self, direction, volume, sl, tp, comment) -> OrderResult:
        bid, ask = self.bid_ask()
        price = ask if direction > 0 else bid
        p = Position(self._next, direction, volume, price, sl, tp or 0.0, datetime.now(timezone.utc))
        self._next += 1
        self._pos.append(p)
        return OrderResult(True, p.ticket, price, "simulé")

    def modify_sl(self, ticket, sl, tp) -> OrderResult:
        for p in self._pos:
            if p.ticket == ticket:
                p.sl = sl
                return OrderResult(True, ticket)
        return OrderResult(False, message="position introuvable")

    def close(self, ticket, comment) -> OrderResult:
        bid, ask = self.bid_ask()
        for p in list(self._pos):
            if p.ticket == ticket:
                px = bid if p.direction > 0 else ask
                self.cash += (px - p.price_open) * p.direction * p.volume * self.contract
                self._pos.remove(p)
                return OrderResult(True, ticket, px, "simulé")
        return OrderResult(False, message="position introuvable")
