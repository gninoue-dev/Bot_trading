# Guide de démarrage — de zéro au bot qui trade (Windows + MT5 HFM)

> Règle d'or : **démo d'abord**. On ne passe au réel qu'après plusieurs semaines où la démo se comporte comme le backtest.

## Étape 1 — Installer (10 minutes)
1. Installe **MetaTrader 5 de HFM** (site HFM › Plateformes › MT5) et connecte-toi à un **compte DÉMO**.
2. Dans MT5 :
   - clique sur **« Algo Trading »** (le bouton doit être vert) ;
   - va dans **Outils › Options › Expert Advisors** et coche « Autoriser le trading algorithmique ».
3. Télécharge ce dépôt (bouton vert « Code » › Download ZIP) et dézippe-le, par exemple dans `C:\GoldBot`.
4. Clic droit sur `scripts\install_windows.ps1` › **Exécuter avec PowerShell**. Le script installe Python et les dépendances, et crée `config\config.toml`.

## Étape 2 — Vérifier la stratégie sur TES prix (15 minutes)
Avec MT5 ouvert, dans un terminal (dossier du bot) :
```
.venv\Scripts\goldbot fetch --source mt5 --tf H4
.venv\Scripts\goldbot walkforward --tf H4 --csv data\mt5_XAUUSD_H4.csv
```
Le nom du fichier CSV contient le nom du symbole chez ton broker. La première commande l'affiche.

Ouvre le rapport `reports\walkforward_H4.html`. Si les résultats hors échantillon restent positifs sur plusieurs années, on continue. **Sinon, on ne trade pas** et on m'envoie le rapport.

## Étape 3 — Mode papier (1 à 2 semaines)
Dans `config\config.toml`, laisse `mode = "paper"`, puis :
```
.venv\Scripts\goldbot run
```
Le bot lit les vrais prix mais n'envoie aucun ordre. Les décisions sont dans `logs\goldbot.log`, et sur Telegram si tu l'as configuré.

## Étape 4 — Compte démo (au moins 1 mois)
Mets `mode = "live"` **avec le compte DÉMO connecté** dans MT5, puis lance `goldbot run` et tape `OUI`. Les ordres apparaissent dans MT5 avec le commentaire `goldbot`.

## Étape 5 — Réel, petit
Ne passe au réel qu'après ces quatre étapes. Commence petit :
- `risk_pct = 0.005`, soit 0,5 % par trade ;
- un capital que tu peux te permettre de voir baisser de 15 à 20 % sans paniquer.

Après 3 mois conformes au backtest, tu peux monter à `0.01`. **Jamais plus de 2 %.**

### Quel capital ?
Sur l'or en H4, le stop fait souvent 60 à 110 $. Même au lot minimum (0,01 = 1 once), un trade risque donc 60 à 110 $.

| Compte | Capital minimum pour 0,5 % | 1 % | 2 % |
|---|---|---|---|
| Standard / Premium (1 lot = 100 onces) | ~15 000 $ | ~7 500 $ | ~3 800 $ |
| Cent (solde en cents) | ~150 $ | ~76 $ | ~38 $ |

Si ton capital est plus petit que la ligne « standard », utilise un **compte cent** (à vérifier chez HFM pour l'or sur MT5). Sinon, le bot refuse d'entrer : il ne prendra jamais un risque trop gros pour toi.

## Faire tourner le bot 24 h/24
Le bot doit tourner quand le marché est ouvert, du lundi au vendredi. Deux options :
- **Ton PC allumé** : simple, mais une coupure de courant ou d'internet l'arrête. Les positions gardent quand même leur stop chez le broker.
- **Un VPS Windows** (HFM en propose, parfois gratuit selon le dépôt) : recommandé.

Lancement automatique : `scripts\run_bot.bat` dans le Planificateur de tâches Windows, au démarrage.

## Si le bot s'arrête tout seul
Il s'arrête net quand le **drawdown maximal** est atteint (-12 % par défaut) : il ferme tout et prévient. C'est voulu.
1. Analyse ce qui s'est passé : `goldbot status` et les logs.
2. Relance seulement après réflexion, avec `goldbot reset` puis `goldbot run`.

## Notifications Telegram (recommandé)
1. Sur Telegram, parle à **@BotFather** › `/newbot` › copie le *token*.
2. Parle à **@userinfobot** pour obtenir ton *chat_id*.
3. Colle les deux dans `[notify]` de `config\config.toml`.

Tu recevras chaque entrée et sortie, et toutes les alertes de risque.
