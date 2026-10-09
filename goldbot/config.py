"""Lecture de config/config.toml (valeurs par défaut prudentes si une clé manque)."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class MT5Config:
    terminal_path: str = ""
    login: int = 0
    password: str = ""
    server: str = ""
    symbol: str = ""
    magic: int = 770077
    deviation_points: int = 30


@dataclass
class RiskConfig:
    risk_pct: float = 0.005
    max_daily_loss: float = 0.03
    max_drawdown: float = 0.12
    max_spread: float = 0.80
    max_positions: int = 1
    news_blackout_minutes: int = 45
    news_dates: list[str] = field(default_factory=list)
    no_entry_friday_after_utc: int = 18


@dataclass
class Config:
    mode: str = "paper"
    mt5: MT5Config = field(default_factory=MT5Config)
    strategy: dict = field(default_factory=lambda: {"name": "donchian_trend", "timeframe": "H4"})
    risk: RiskConfig = field(default_factory=RiskConfig)
    notify: dict = field(default_factory=dict)

    @property
    def timeframe(self) -> str:
        return self.strategy.get("timeframe", "H4")

    def strategy_params(self) -> dict:
        return {k: v for k, v in self.strategy.items() if k not in ("name", "timeframe")}


def load(path: str | Path | None = None) -> Config:
    path = Path(path) if path else ROOT / "config" / "config.toml"
    if not path.exists():
        path = ROOT / "config" / "config.example.toml"
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    cfg = Config(
        mode=data.get("mode", "paper"),
        mt5=MT5Config(**data.get("mt5", {})),
        strategy=data.get("strategy", {"name": "donchian_trend", "timeframe": "H4"}),
        risk=RiskConfig(**data.get("risk", {})),
        notify=data.get("notify", {}),
    )
    if cfg.mode not in ("paper", "live"):
        raise ValueError("mode doit valoir « paper » ou « live »")
    if not 0 < cfg.risk.risk_pct <= 0.03:
        raise ValueError("risk_pct doit être entre 0 et 3 % (0.03) : au-delà, la ruine devient probable")
    return cfg
