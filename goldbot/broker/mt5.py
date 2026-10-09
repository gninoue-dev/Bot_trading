"""Connexion à MetaTrader 5 (HFM ou autre broker). Fonctionne sur Windows, terminal MT5 installé.

Particularités gérées :
- nom du symbole variable selon le type de compte (XAUUSD, XAUUSD.r, XAUUSDm, GOLD…) → détection auto ;
- heure du serveur ≠ UTC (HFM : GMT+2 / GMT+3 en été) → décalage mesuré et corrigé ;
- mode de remplissage accepté par le broker (FOK / IOC / RETURN) → détecté ;
- distance minimale du stop (stops level) → stop éloigné si nécessaire ;
- seuls les ordres portant notre « magic number » sont gérés : tes trades manuels ne sont jamais touchés.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pandas as pd

from ..config import MT5Config
from ..risk import SymbolSpec
from .base import Broker, OrderResult, Position

CANDIDATES = ("XAUUSD", "XAUUSD.r", "XAUUSDr", "XAUUSD.", "XAUUSDm", "XAUUSD.m", "XAUUSD+", "GOLD", "XAUUSD.pro")


class MT5Broker(Broker):
    def __init__(self, cfg: MT5Config, mt5_module=None):
        if mt5_module is None:
            import MetaTrader5 as mt5_module  # noqa: N813 - le paquet n'existe que sous Windows
        self.mt5 = mt5_module
        self.cfg = cfg
        self.symbol = cfg.symbol
        self.offset_h = 0
        self._filling = None

    # ── connexion ─────────────────────────────────────────
    def connect(self) -> None:
        kw = {}
        if self.cfg.terminal_path:
            kw["path"] = self.cfg.terminal_path
        if self.cfg.login:
            kw.update(login=int(self.cfg.login), password=self.cfg.password, server=self.cfg.server)
        if not self.mt5.initialize(**kw):
            raise ConnectionError(f"MT5 ne démarre pas : {self.mt5.last_error()} — terminal ouvert et connecté ?")
        info = self.mt5.terminal_info()
        if info is not None and not info.trade_allowed:
            raise ConnectionError("Le trading algorithmique est désactivé dans MT5 (bouton « Algo Trading » / « Trading Algo »).")
        self.symbol = self.symbol or self._detect_symbol()
        if not self.mt5.symbol_select(self.symbol, True):
            raise ConnectionError(f"Symbole {self.symbol} introuvable chez ce broker.")
        self.offset_h = self._server_offset()

    def shutdown(self) -> None:
        self.mt5.shutdown()

    def _detect_symbol(self) -> str:
        for name in CANDIDATES:
            if self.mt5.symbol_info(name) is not None:
                return name
        found = self.mt5.symbols_get("*XAU*") or ()
        gold = [s.name for s in found if "USD" in s.name.upper()]
        if not gold:
            raise ConnectionError("Aucun symbole or (XAUUSD) trouvé : renseigne mt5.symbol dans config.toml")
        return min(gold, key=len)

    def _server_offset(self) -> int:
        """Décalage heure serveur − UTC, arrondi à l'heure (la dernière cotation porte l'heure du serveur)."""
        tick = self.mt5.symbol_info_tick(self.symbol)
        if tick is None or not tick.time:
            return 0
        diff = (tick.time - time.time()) / 3600
        return int(round(diff)) if abs(diff) < 14 else 0

    # ── informations ──────────────────────────────────────
    def spec(self) -> SymbolSpec:
        s = self.mt5.symbol_info(self.symbol)
        return SymbolSpec(s.name, s.trade_tick_size, s.trade_tick_value, s.volume_min, s.volume_step, s.volume_max,
                          s.digits, s.trade_stops_level, s.point)

    def equity(self) -> float:
        return float(self.mt5.account_info().equity)

    def balance(self) -> float:
        return float(self.mt5.account_info().balance)

    def bid_ask(self) -> tuple[float, float]:
        t = self.mt5.symbol_info_tick(self.symbol)
        return float(t.bid), float(t.ask)

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    def bars(self, timeframe: str, count: int) -> pd.DataFrame:
        tf = {"M15": self.mt5.TIMEFRAME_M15, "M30": self.mt5.TIMEFRAME_M30, "H1": self.mt5.TIMEFRAME_H1,
              "H4": self.mt5.TIMEFRAME_H4, "D1": self.mt5.TIMEFRAME_D1}[timeframe]
        rates = self.mt5.copy_rates_from_pos(self.symbol, tf, 0, count)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"Pas de bougies {timeframe} : {self.mt5.last_error()}")
        df = pd.DataFrame(rates)
        # heure serveur → UTC
        df.index = pd.to_datetime(df["time"], unit="s", utc=True) - pd.Timedelta(hours=self.offset_h)
        df = df.rename(columns={"tick_volume": "volume"})[["open", "high", "low", "close", "volume"]].astype(float)
        return df.iloc[:-1]  # la dernière bougie est en cours : on ne décide que sur des bougies closes

    def positions(self) -> list[Position]:
        out = []
        for p in self.mt5.positions_get(symbol=self.symbol) or ():
            if p.magic != self.cfg.magic:
                continue  # ce n'est pas un trade du bot
            d = 1 if p.type == self.mt5.POSITION_TYPE_BUY else -1
            out.append(Position(p.ticket, d, p.volume, p.price_open, p.sl, p.tp,
                                datetime.fromtimestamp(p.time, timezone.utc), p.profit))
        return out

    # ── ordres ────────────────────────────────────────────
    def _filling_mode(self):
        if self._filling is None:
            fm = self.mt5.symbol_info(self.symbol).filling_mode
            # bits : 1 = FOK, 2 = IOC
            self._filling = (self.mt5.ORDER_FILLING_FOK if fm & 1 else
                             self.mt5.ORDER_FILLING_IOC if fm & 2 else self.mt5.ORDER_FILLING_RETURN)
        return self._filling

    def _send(self, request: dict) -> OrderResult:
        for attempt in range(3):
            res = self.mt5.order_send(request)
            if res is None:
                return OrderResult(False, message=f"order_send a échoué : {self.mt5.last_error()}")
            if res.retcode == self.mt5.TRADE_RETCODE_DONE:
                return OrderResult(True, getattr(res, "order", 0) or getattr(res, "deal", 0), res.price, "ok")
            if res.retcode in (self.mt5.TRADE_RETCODE_REQUOTE, self.mt5.TRADE_RETCODE_PRICE_CHANGED) and attempt < 2:
                bid, ask = self.bid_ask()
                if "price" in request:
                    request["price"] = ask if request["type"] == self.mt5.ORDER_TYPE_BUY else bid
                time.sleep(0.5)
                continue
            return OrderResult(False, message=f"refusé (code {res.retcode}) : {res.comment}")
        return OrderResult(False, message="refusé après 3 tentatives")

    def _respect_stops_level(self, direction: int, price: float, sl: float) -> float:
        spec = self.spec()
        min_dist = (spec.stops_level + 5) * spec.point
        if direction > 0 and price - sl < min_dist:
            sl = price - min_dist
        if direction < 0 and sl - price < min_dist:
            sl = price + min_dist
        return round(sl, spec.digits)

    def open(self, direction: int, volume: float, sl: float, tp: float | None, comment: str) -> OrderResult:
        bid, ask = self.bid_ask()
        price = ask if direction > 0 else bid
        sl = self._respect_stops_level(direction, price, sl)
        req = {
            "action": self.mt5.TRADE_ACTION_DEAL, "symbol": self.symbol, "volume": float(volume),
            "type": self.mt5.ORDER_TYPE_BUY if direction > 0 else self.mt5.ORDER_TYPE_SELL,
            "price": price, "sl": sl, "tp": float(tp) if tp else 0.0,
            "deviation": self.cfg.deviation_points, "magic": self.cfg.magic, "comment": comment[:28],
            "type_time": self.mt5.ORDER_TIME_GTC, "type_filling": self._filling_mode(),
        }
        return self._send(req)

    def modify_sl(self, ticket: int, sl: float, tp: float | None) -> OrderResult:
        pos = next((p for p in self.positions() if p.ticket == ticket), None)
        if pos is None:
            return OrderResult(False, message="position introuvable")
        bid, ask = self.bid_ask()
        sl = self._respect_stops_level(pos.direction, bid if pos.direction > 0 else ask, sl)
        req = {"action": self.mt5.TRADE_ACTION_SLTP, "symbol": self.symbol, "position": ticket,
               "sl": sl, "tp": float(tp) if tp else 0.0, "magic": self.cfg.magic}
        return self._send(req)

    def close(self, ticket: int, comment: str) -> OrderResult:
        pos = next((p for p in self.positions() if p.ticket == ticket), None)
        if pos is None:
            return OrderResult(False, message="position introuvable")
        bid, ask = self.bid_ask()
        req = {
            "action": self.mt5.TRADE_ACTION_DEAL, "symbol": self.symbol, "volume": pos.volume, "position": ticket,
            "type": self.mt5.ORDER_TYPE_SELL if pos.direction > 0 else self.mt5.ORDER_TYPE_BUY,
            "price": bid if pos.direction > 0 else ask, "deviation": self.cfg.deviation_points,
            "magic": self.cfg.magic, "comment": comment[:28],
            "type_time": self.mt5.ORDER_TIME_GTC, "type_filling": self._filling_mode(),
        }
        return self._send(req)
