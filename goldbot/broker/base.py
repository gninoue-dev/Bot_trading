"""Interface commune des brokers : le moteur ne sait pas s'il parle à MT5 ou au simulateur."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from ..risk import SymbolSpec


@dataclass
class Position:
    ticket: int
    direction: int        # +1 achat, -1 vente
    volume: float
    price_open: float
    sl: float
    tp: float
    time_open: datetime
    profit: float = 0.0


@dataclass
class OrderResult:
    ok: bool
    ticket: int = 0
    price: float = 0.0
    message: str = ""


class Broker:
    symbol: str

    def connect(self) -> None: ...
    def shutdown(self) -> None: ...
    def spec(self) -> SymbolSpec: ...
    def equity(self) -> float: ...
    def balance(self) -> float: ...
    def bid_ask(self) -> tuple[float, float]: ...
    def bars(self, timeframe: str, count: int) -> pd.DataFrame: ...
    def positions(self) -> list[Position]: ...
    def open(self, direction: int, volume: float, sl: float, tp: float | None, comment: str) -> OrderResult: ...
    def modify_sl(self, ticket: int, sl: float, tp: float | None) -> OrderResult: ...
    def close(self, ticket: int, comment: str) -> OrderResult: ...
    def now(self) -> datetime: ...
