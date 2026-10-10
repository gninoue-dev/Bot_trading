<p align="center">
  <img src="https://capsule-render.vercel.app/api?type=waving&height=170&color=0:1d1d1f,60:7a5c12,100:d4a017&text=GoldBot&fontColor=ffffff&fontSize=52&fontAlignY=38&desc=Bot%20de%20trading%20sur%20l'or%20(XAUUSD)%20%C2%B7%20MetaTrader%205&descAlignY=60&descSize=17" alt="GoldBot"/>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/actif-XAUUSD-d4a017"/>
  <img src="https://img.shields.io/badge/plateforme-MetaTrader%205-1f6feb"/>
  <img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white"/>
  <img src="https://img.shields.io/badge/tests-19%20%E2%9C%93-2EA043"/>
  <img src="https://img.shields.io/badge/priorit%C3%A9-prot%C3%A9ger%20le%20capital-9b2226"/>
</p>

**GoldBot** trade uniquement l'or, sur MetaTrader 5 (développé pour HFM, compatible avec tout broker MT5). Il suit une seule stratégie, choisie parce qu'elle a **survécu aux tests honnêtes**. Les garde-fous de risque passent **avant** la stratégie.

> ⚠️ **Avertissement.** Le trading comporte un risque réel de perte en capital. Aucun bot ne garantit des gains, et les performances passées ne préjugent pas des performances futures. Utilise d'abord le mode papier, puis un compte démo, et ne risque que de l'argent que tu peux te permettre de perdre.

---

## 📈 Résultats hors échantillon (walk-forward H4, coûts inclus)

<p align="center"><img src="docs/walkforward_h4.png" alt="Courbe hors échantillon" width="100%"/></p>

| Rendement annuel (risque 1 %/trade) | Drawdown max | Profit factor | Espérance | Trades/mois |
|---|---|---|---|---|
| **+7,5 %** | **-6,2 %** | **1,76** | **+0,42 R** | 2,3 |

Le bot participe à la hausse de l'or, puis traverse sa grosse correction en ne cédant que 6 %. **Détail complet, y compris les stratégies rejetées et les limites :** [`docs/STRATEGIE.md`](docs/STRATEGIE.md).

---

## 🧠 La stratégie en clair
**Cassure de canal H4 + filtre de tendance de fond** (famille « Turtles ») :
- 🟢 **Achat** quand l'or clôture au-dessus de son plus haut des 20 dernières bougies H4, s'il est au-dessus de ses moyennes 200 (H4) et 200 jours.
- 🔴 **Vente**, à l'inverse, seulement en tendance baissière de fond.
- 🛑 **Stop initial à 2 ATR**, toujours posé chez le broker.
- 🎯 **Sortie** sur la cassure du plus bas (ou du plus haut) des 10 dernières bougies, ou par un stop suiveur à 3 ATR.
- 📊 **Profil** : peu de trades (2 ou 3 par mois), plus de la moitié perdants mais petits, quelques gros gagnants.

## 🛡️ Les garde-fous
| | |
|---|---|
| Taille de position | calculée pour risquer X % du capital (0,5 % par défaut) avec les vraies spécifications du symbole ; refuse d'entrer si même le lot minimum risquerait trop |
| Perte journalière | -3 % → plus aucune entrée avant le lendemain |
| Drawdown max | -12 % → **le bot ferme tout et s'arrête** (redémarrage manuel) |
| Spread | pas d'entrée si le spread dépasse 0,80 $ |
| Annonces | pas d'entrée autour du NFP et des dates que tu listes (FOMC, CPI…) |
| Week-end | pas de nouvelle position le vendredi soir |
| Tes trades manuels | jamais touchés (identifiant « magic » propre au bot) |
| Panne | stops toujours chez le broker ; reconnexion automatique ; état sauvegardé sur disque |

## 💵 Dès 10 $ : le profil petit compte
Sur un **compte cent HFM**, avec `config/petit_compte.example.toml` (stratégie H1, risque plafonné à 5 % au lot minimum) :
- simulation hors échantillon : **10 $ → 17,64 $ en 17 mois**, pire recul -23 % ;
- `goldbot check` te dit tout de suite si ton compte peut trader ;
- le bot vérifie la marge avant chaque ordre et refuse tout trade qui dépasse ton plafond.

Détails : [guide](docs/GUIDE_DEMARRAGE.md#quel-capital--oui-dès-10-) et [recherche](docs/STRATEGIE.md#7-version-h1-pour-les-petits-comptes).

## 🚀 Démarrer
👉 **[Guide pas à pas](docs/GUIDE_DEMARRAGE.md)** : installation, test sur tes prix HFM, papier, démo, puis réel en petit.

```powershell
scripts\install_windows.ps1                               # installation
.venv\Scripts\goldbot fetch --source mt5 --tf H4          # historique de TON broker
.venv\Scripts\goldbot walkforward --tf H4 --csv data\mt5_XAUUSD_H4.csv
.venv\Scripts\goldbot run                                 # papier → démo → réel (config\config.toml)
```

## 🧰 Toutes les commandes
| Commande | Rôle |
|---|---|
| `goldbot backtest [--tf H4] [--risk 0.01] [--csv …] [--open]` | backtest avec rapport HTML |
| `goldbot walkforward [--tf H4] [--csv …]` | validation hors échantillon + Monte Carlo |
| `goldbot fetch --source mt5 --tf H4` | télécharge l'historique depuis MT5 |
| `goldbot run` | lance le bot (mode `paper` ou `live` selon la config) |
| `goldbot check` | diagnostic du compte : capital suffisant ? compte cent nécessaire ? |
| `goldbot status` / `goldbot reset` | état des garde-fous / redémarrage après un arrêt |

## 🏗️ Architecture
```
goldbot/
├── strategies/        donchian_trend (retenue), trend_pullback et session_breakout (rejetées, gardées pour la recherche)
├── backtest.py        moteur pessimiste : bid/ask, glissement, gaps, swaps (mercredi ×3), stop prioritaire
├── optimize.py        walk-forward + choix robuste des paramètres + Monte Carlo
├── risk.py            taille de position, perte journalière, drawdown max, annonces
├── live.py            moteur réel (mêmes règles que le backtest)
├── broker/mt5.py      MetaTrader 5 : détection du symbole HFM, fuseau serveur, remplissage, stops level, requotes
├── broker/dryrun.py   mode papier sur prix réels
├── broker/paper.py    simulateur (tests, rejeu)
└── report.py, cli.py  rapports HTML et ligne de commande
```

## 🧪 Tests
```bash
.venv/bin/python -m pytest -q
```
19 tests, qui vérifient notamment que :
- les stratégies **ne regardent jamais le futur** ;
- le stop est prioritaire et les gaps sont exécutés au mauvais prix ;
- la taille de position et les swaps (mercredi ×3, rien le week-end) sont justes ;
- les garde-fous fonctionnent et que l'arrêt d'urgence survit à un redémarrage ;
- le moteur réel rejoué donne les mêmes trades que le backtest ;
- les petits comptes (cent, dès 10 $) tradent au lot minimum, sous le plafond de risque, et uniquement si la marge le permet ;
- la connexion MT5 se comporte correctement face à un **faux MetaTrader** (symbole `XAUUSD.r`, fuseau GMT+3, requote, stops level).

---
*Projet personnel d'apprentissage du trading algorithmique — Jean-Marc Gninoue.*
