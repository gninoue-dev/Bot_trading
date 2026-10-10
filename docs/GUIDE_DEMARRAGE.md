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

### Quel capital ? (oui, dès 10 $)
Sur l'or, le plus petit ordre possible est **0,01 lot**. Ce qu'il coûte si le stop est touché dépend du type de compte :

| | Compte standard / Premium | **Compte cent HFM** |
|---|---|---|
| Perte au stop, 0,01 lot en H4 (~76 $ de stop) | ~76 $ | ~0,76 $ |
| Perte au stop, 0,01 lot en H1 (~38 $ de stop) | ~38 $ | ~0,38 $ |
| Capital mini, profil normal (H4, 1 %) | ~7 500 $ | ~76 $ |
| **Capital mini, profil petit compte (H1, ≤ 5 %)** | ~760 $ | **~8 à 10 $** |

**Avec 10 $ : compte CENT obligatoire + profil petit compte.**
1. Ouvre un **compte cent** chez HFM. Le dépôt minimum est de 0 à 5 $. Vérifie qu'il est disponible en MT5 dans ton pays, avec l'or.
2. Copie `config\petit_compte.example.toml` en `config\config.toml`.
3. Lance `goldbot check`. Le bot te dit s'il peut trader avec ce compte, et sinon pourquoi.

En profil petit compte, chaque trade risque **3 à 5 % du capital** au lot minimum, au lieu de 1 %. Les mouvements sont donc plus violents :
- simulation hors échantillon sur 17 mois : **10 $ → 17,64 $, avec un pire recul de -23 %** ;
- à ce niveau de risque, un recul de -30 à -50 % reste possible lors d'une mauvaise série.

Le risque redescend tout seul vers 1 % quand le compte grandit, car le bot calcule la taille de chaque ordre.

Sur un compte **standard** avec 10 $, le bot refuse de trader : un seul stop ferait perdre plus que tout le compte.

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
