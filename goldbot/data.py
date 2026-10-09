"""Données de prix de l'or : un seul format partout.

DataFrame indexé par l'heure UTC (tz-aware), colonnes open, high, low, close, volume.

Sources :
- Yahoo Finance (GC=F, contrat à terme sur l'or) : pour la recherche, sans compte.
  ≈ 2 ans en H1, 20 ans en D1. Le future suit le spot à quelques dollars près.
- MetaTrader 5 (ton compte HFM) : prix exacts du broker, pour le réel et pour
  revalider les stratégies sur un historique plus long (`goldbot fetch --source mt5`).
- CSV : n'importe quel export (MT5, TradingView, Dukascopy…).
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
COLUMNS = ["open", "high", "low", "close", "volume"]

TIMEFRAMES = {"M5": "5min", "M15": "15min", "M30": "30min", "H1": "1h", "H4": "4h", "D1": "1D"}


def fetch_yahoo(symbol: str = "GC=F", interval: str = "1h", range_: str = "730d") -> pd.DataFrame:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval={interval}&range={range_}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 goldbot"})
    with urllib.request.urlopen(req, timeout=30) as r:
        payload = json.load(r)
    res = payload["chart"]["result"]
    if not res:
        raise RuntimeError(f"Yahoo : pas de données pour {symbol} ({payload['chart'].get('error')})")
    res = res[0]
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({k: q.get(k) for k in COLUMNS}, index=pd.to_datetime(res["timestamp"], unit="s", utc=True))
    if interval.endswith(("m", "h")):
        # la dernière bougie « en cours » est horodatée à la seconde près : on ne garde que les bougies closes
        step = pd.Timedelta(interval.replace("m", "min"))
        df = df[(df.index.floor(step) == df.index)]
        df = df[df.index + step <= pd.Timestamp.now(tz="UTC")]
    return clean(df)


def clean(df: pd.DataFrame) -> pd.DataFrame:
    """Retire les bougies vides / incohérentes, trie, dédoublonne."""
    df = df[COLUMNS].astype(float)
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df[(df["high"] >= df["low"]) & (df["close"] > 0)]
    df["high"] = df[["open", "high", "close"]].max(axis=1)
    df["low"] = df[["open", "low", "close"]].min(axis=1)
    df["volume"] = df["volume"].fillna(0)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df.index.name = "time"
    return df


def resample(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    rule = TIMEFRAMES[timeframe]
    out = df.resample(rule, label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    )
    return out.dropna(subset=["open"])


def save_csv(df: pd.DataFrame, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path)
    return path


def load_csv(path: str | Path) -> pd.DataFrame:
    """Lit notre format, ou un export MT5 (<DATE> <TIME> <OPEN>…) / TradingView (time,open,…)."""
    path = Path(path)
    raw = pd.read_csv(path, sep=None, engine="python")
    cols = {c.lower().strip("<>").strip(): c for c in raw.columns}
    if "date" in cols and "time" in cols:
        idx = pd.to_datetime(raw[cols["date"]].astype(str) + " " + raw[cols["time"]].astype(str), utc=True)
    else:
        tcol = cols.get("time") or cols.get("datetime") or cols.get("date") or raw.columns[0]
        t = raw[tcol]
        idx = pd.to_datetime(t, unit="s", utc=True) if pd.api.types.is_numeric_dtype(t) else pd.to_datetime(t, utc=True)
    vol = cols.get("volume") or cols.get("tickvol") or cols.get("vol")
    df = pd.DataFrame({
        "open": raw[cols["open"]].values, "high": raw[cols["high"]].values,
        "low": raw[cols["low"]].values, "close": raw[cols["close"]].values,
        "volume": raw[vol].values if vol else 0.0,
    }, index=idx)
    return clean(df)


def research_data(timeframe: str = "H1", refresh: bool = False) -> pd.DataFrame:
    """Données de recherche mises en cache dans data/ (téléchargées une fois)."""
    if timeframe == "D1":
        path = DATA_DIR / "gold_D1.csv"
        if refresh or not path.exists():
            save_csv(fetch_yahoo("GC=F", "1d", "20y"), path)  # « max » renvoie des bougies mensuelles
        return load_csv(path)
    path = DATA_DIR / "gold_H1.csv"
    if refresh or not path.exists():
        fresh = fetch_yahoo("GC=F", "1h", "730d")
        if path.exists():  # on accumule : Yahoo ne donne que 730 jours glissants
            fresh = clean(pd.concat([load_csv(path), fresh]))
        save_csv(fresh, path)
    h1 = load_csv(path)
    return h1 if timeframe == "H1" else resample(h1, timeframe)


def fetch_mt5(symbol: str, timeframe: str = "H1", bars: int = 50_000) -> pd.DataFrame:
    """Historique depuis le terminal MetaTrader 5 (Windows, terminal ouvert et connecté)."""
    import MetaTrader5 as mt5  # noqa: N813

    tf = {"M5": mt5.TIMEFRAME_M5, "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30,
          "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4, "D1": mt5.TIMEFRAME_D1}[timeframe]
    rates = mt5.copy_rates_from_pos(symbol, tf, 0, bars)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"MT5 : pas d'historique pour {symbol} ({mt5.last_error()})")
    df = pd.DataFrame(rates)
    df.index = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.rename(columns={"tick_volume": "volume"})
    return clean(df)
