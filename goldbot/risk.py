"""Garde-fous du bot réel. Ils passent AVANT la stratégie : protéger le capital d'abord.

- taille de position calculée sur le risque, avec les vraies caractéristiques du symbole ;
- limite de perte journalière (plus d'entrée jusqu'au lendemain) ;
- drawdown maximal = ARRÊT TOTAL (fermeture + bot stoppé, redémarrage manuel) ;
- filtre de spread, fenêtres d'annonces économiques, pas d'entrée le vendredi soir ;
- état persisté sur disque (plus haut historique, capital de début de journée, arrêt d'urgence).
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import RiskConfig


@dataclass
class SymbolSpec:
    name: str
    tick_size: float        # plus petit mouvement de prix
    tick_value: float       # valeur en $ d'un tick pour 1 lot
    volume_min: float
    volume_step: float
    volume_max: float
    digits: int = 2
    stops_level: int = 0    # distance mini du stop (en points) imposée par le broker
    point: float = 0.01

    def loss_per_lot(self, price_distance: float) -> float:
        return price_distance / self.tick_size * self.tick_value


def position_size(equity: float, risk_pct: float, sl_distance: float, spec: SymbolSpec,
                  max_risk_min_lot: float | None = None) -> float:
    """Lots tels que la perte au stop ≈ risk_pct du capital.

    Petit compte : si même le lot minimum dépasse risk_pct, on l'accepte seulement si sa perte
    au stop reste ≤ max_risk_min_lot du capital (plafond absolu, 1,5 × risk_pct par défaut).
    """
    if max_risk_min_lot is None:
        max_risk_min_lot = 1.5 * risk_pct
    if sl_distance <= 0 or equity <= 0:
        return 0.0
    budget = equity * risk_pct
    per_lot = spec.loss_per_lot(sl_distance)
    raw = budget / per_lot
    lots = math.floor(raw / spec.volume_step + 1e-9) * spec.volume_step
    if lots < spec.volume_min:
        # mode petit compte : le lot minimum est accepté tant qu'il reste sous le plafond absolu
        lots = spec.volume_min if spec.volume_min * per_lot <= equity * max_risk_min_lot + 1e-9 else 0.0
    return round(min(lots, spec.volume_max), 2)


def min_lot_risk_pct(equity: float, sl_distance: float, spec: SymbolSpec) -> float:
    """Risque (en % du capital) d'un trade au lot minimum avec ce stop."""
    return spec.volume_min * spec.loss_per_lot(sl_distance) / equity if equity > 0 else float("inf")


def money(amount: float, currency: str = "USD") -> str:
    """Montant lisible ; sur un compte cent (USC) on montre aussi l'équivalent en dollars."""
    if currency.upper() in ("USC", "USX", "CENT", "US CENT"):
        return f"{amount:,.0f} USC ({amount / 100:,.2f} $)".replace(",", " ")
    return f"{amount:,.2f} {currency}".replace(",", " ")


@dataclass
class RiskState:
    peak_equity: float = 0.0
    day: str = ""
    day_start_equity: float = 0.0
    halted: bool = False            # arrêt d'urgence (drawdown max) : redémarrage MANUEL
    halted_reason: str = ""
    last_bar: str = ""              # dernière bougie traitée (évite de trader deux fois la même)

    @classmethod
    def load(cls, path: Path) -> "RiskState":
        if path.exists():
            return cls(**json.loads(path.read_text()))
        return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2))


class RiskGuard:
    def __init__(self, cfg: RiskConfig, state_path: Path):
        self.cfg = cfg
        self.path = state_path
        self.state = RiskState.load(state_path)

    def update(self, equity: float, now: datetime) -> list[str]:
        """Met à jour le suivi du capital. Renvoie les alertes à envoyer."""
        alerts: list[str] = []
        s = self.state
        today = now.strftime("%Y-%m-%d")
        if s.day != today:
            s.day, s.day_start_equity = today, equity
        s.peak_equity = max(s.peak_equity, equity)
        if not s.halted and s.peak_equity > 0 and equity < s.peak_equity * (1 - self.cfg.max_drawdown):
            s.halted = True
            s.halted_reason = (f"Drawdown max atteint : capital {equity:.2f} < {100 * (1 - self.cfg.max_drawdown):.0f} % "
                               f"du plus haut ({s.peak_equity:.2f}) le {now:%Y-%m-%d %H:%M} UTC")
            alerts.append("🛑 " + s.halted_reason + ". Le bot ferme tout et s'arrête. Redémarrage manuel : goldbot reset.")
        self.state.save(self.path)
        return alerts

    def daily_loss_hit(self, equity: float) -> bool:
        s = self.state
        return s.day_start_equity > 0 and equity < s.day_start_equity * (1 - self.cfg.max_daily_loss)

    def can_enter(self, equity: float, spread: float, now: datetime) -> tuple[bool, str]:
        if self.state.halted:
            return False, "bot arrêté (drawdown max) — " + self.state.halted_reason
        if self.daily_loss_hit(equity):
            return False, f"perte journalière max ({100 * self.cfg.max_daily_loss:.0f} %) atteinte"
        if spread > self.cfg.max_spread:
            return False, f"spread trop large ({spread:.2f} $ > {self.cfg.max_spread:.2f} $)"
        if now.weekday() == 4 and now.hour >= self.cfg.no_entry_friday_after_utc:
            return False, "vendredi soir : pas de nouvelle position avant le week-end"
        if self.in_news_blackout(now):
            return False, "fenêtre d'annonce économique (NFP / date listée)"
        return True, ""

    def in_news_blackout(self, now: datetime) -> bool:
        win = timedelta(minutes=self.cfg.news_blackout_minutes)
        events = [_first_friday_nfp(now)]
        for d in self.cfg.news_dates:
            try:
                events.append(datetime.strptime(d, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc))
            except ValueError:
                continue
        # une position H4 ouverte peu avant l'annonce la subirait : on regarde aussi les 4 h à venir
        return any(e - win - timedelta(hours=4) <= now <= e + win for e in events)

    def reset(self) -> None:
        self.state.halted = False
        self.state.halted_reason = ""
        self.state.peak_equity = 0.0
        self.state.save(self.path)


def _first_friday_nfp(now: datetime) -> datetime:
    """Emploi américain (NFP) : 1er vendredi du mois, 12 h 30 UTC (13 h 30 en hiver US)."""
    first = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    offset = (4 - first.weekday()) % 7
    friday = first + timedelta(days=offset)
    # heure d'été américaine approximative : mi-mars → début novembre
    summer = 3 <= friday.month <= 10 or (friday.month == 11 and friday.day < 7)
    return friday.replace(hour=12 if summer else 13, minute=30, tzinfo=timezone.utc)
