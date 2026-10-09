"""Rapport HTML autonome d'un backtest (un seul fichier, s'ouvre dans n'importe quel navigateur)."""

from __future__ import annotations

import html
from pathlib import Path

import pandas as pd

from .backtest import Result
from .metrics import monthly_returns, summary


def _svg_equity(eq: pd.Series, w: int = 900, h: int = 260) -> str:
    s = eq.resample("1D").last().dropna()
    if len(s) < 2:
        return ""
    lo, hi = float(s.min()), float(s.max())
    span = (hi - lo) or 1.0
    xs = [i * (w - 20) / (len(s) - 1) + 10 for i in range(len(s))]
    ys = [h - 20 - (v - lo) / span * (h - 40) for v in s.values]
    path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y) in enumerate(zip(xs, ys)))
    dd = (s / s.cummax() - 1).values
    dd_path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{h - 20 - d * -1 * (h - 40) * 2:.1f}" for i, (x, d) in enumerate(zip(xs, dd)))
    return (f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="Courbe du capital">'
            f'<path d="{dd_path}" fill="none" stroke="#e5484d" stroke-width="1" opacity=".35"/>'
            f'<path d="{path}" fill="none" stroke="#d4a017" stroke-width="2.2"/>'
            f'<text x="10" y="14" font-size="12" fill="#888">{hi:,.0f} $</text>'
            f'<text x="10" y="{h - 4}" font-size="12" fill="#888">{lo:,.0f} $</text></svg>')


def write_report(res: Result, path: str | Path, title: str = "GoldBot — backtest", extra: dict | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    s = summary(res)
    labels = {
        "capital_final": "Capital final ($)", "rendement_total_%": "Rendement total (%)",
        "rendement_annuel_%": "Rendement annuel (%)", "drawdown_max_%": "Drawdown max (%)",
        "plus_long_creux_jours": "Plus long creux (jours)", "sharpe": "Sharpe", "sortino": "Sortino",
        "trades": "Trades", "trades_par_mois": "Trades / mois", "taux_reussite_%": "Taux de réussite (%)",
        "profit_factor": "Profit factor", "esperance_R": "Espérance (R / trade)", "gain_moyen_R": "Gain moyen (R)",
        "perte_moyenne_R": "Perte moyenne (R)", "pertes_consecutives_max": "Pertes consécutives max",
        "swaps_payes": "Swaps payés ($)", "duree_moyenne_bougies": "Durée moyenne (bougies)",
        "evenements_risque": "Déclenchements des garde-fous",
    }
    rows = "".join(f"<tr><td>{labels.get(k, k)}</td><td>{v}</td></tr>" for k, v in s.items())
    if extra:
        rows += "".join(f"<tr><td>{html.escape(str(k))}</td><td>{html.escape(str(v))}</td></tr>" for k, v in extra.items())
    mr = monthly_returns(res)
    by_year: dict[int, dict[int, float]] = {}
    for ts, v in mr.items():
        by_year.setdefault(ts.year, {})[ts.month] = v
    months = "".join(f"<th>{m}</th>" for m in ("J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"))
    mrows = ""
    for y in sorted(by_year):
        cells = ""
        for m in range(1, 13):
            v = by_year[y].get(m)
            color = "" if v is None else ("#1f7a3a" if v > 0 else "#9b2226")
            cells += f'<td style="color:{color}">{"" if v is None else f"{v:+.1f}"}</td>'
        mrows += f"<tr><th>{y}</th>{cells}</tr>"
    t = res.trades.tail(40)
    trades = "".join(
        f"<tr><td>{r.entry_time:%Y-%m-%d %H:%M}</td><td>{'Achat' if r.direction > 0 else 'Vente'}</td>"
        f"<td>{r.entry:.2f}</td><td>{r.exit:.2f}</td><td>{r.lots:.2f}</td>"
        f"<td style=\"color:{'#1f7a3a' if r.pnl > 0 else '#9b2226'}\">{r.pnl:+.2f}</td><td>{r.r:+.2f}</td><td>{html.escape(r.reason)}</td></tr>"
        for r in t.itertuples()) if len(t) else ""
    events = "".join(f"<li>{html.escape(e)}</li>" for e in res.events) or "<li>Aucun</li>"
    page = f"""<!doctype html><html lang="fr"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{{font-family:system-ui,sans-serif;margin:0;padding:24px;background:#faf9f6;color:#1d1d1f;max-width:1000px;margin:auto}}
h1{{font-size:22px}} h2{{font-size:16px;margin-top:28px}}
table{{border-collapse:collapse;width:100%;font-size:13px}} td,th{{padding:5px 8px;border-bottom:1px solid #e6e3dc;text-align:left}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:24px}} .card{{background:#fff;border:1px solid #e6e3dc;border-radius:10px;padding:14px}}
.warn{{background:#fff7e6;border:1px solid #f0c36d;border-radius:10px;padding:12px;font-size:13px}}
@media(max-width:700px){{.grid{{grid-template-columns:1fr}}}}
@media(prefers-color-scheme:dark){{body{{background:#141414;color:#eee}}.card{{background:#1d1d1d;border-color:#333}}td,th{{border-color:#333}}.warn{{background:#2a2111;border-color:#6b5520}}}}
</style></head><body>
<h1>🥇 {html.escape(title)}</h1>
<p>{html.escape(res.strategy)} · capital de départ {res.initial:,.0f} $ · {res.equity.index[0]:%d/%m/%Y} → {res.equity.index[-1]:%d/%m/%Y}</p>
<div class="card">{_svg_equity(res.equity)}<small>Or : capital · rouge pâle : drawdown</small></div>
<div class="grid"><div class="card"><h2>Résultats</h2><table>{rows}</table></div>
<div class="card"><h2>Garde-fous déclenchés</h2><ul>{events}</ul>
<div class="warn">Les performances passées ne garantissent pas les performances futures. Spread, glissement et swaps sont
inclus mais restent des estimations : vérifie-les sur ton compte. Commence toujours en démo.</div></div></div>
<h2>Rendements mensuels (%)</h2><div class="card"><table><tr><th></th>{months}</tr>{mrows}</table></div>
<h2>Derniers trades</h2><div class="card"><table><tr><th>Entrée</th><th>Sens</th><th>Prix</th><th>Sortie</th><th>Lots</th><th>Gain $</th><th>R</th><th>Raison</th></tr>{trades}</table></div>
</body></html>"""
    path.write_text(page, encoding="utf-8")
    return path
