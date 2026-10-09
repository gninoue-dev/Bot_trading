@echo off
rem Lance GoldBot (à mettre dans le Planificateur de tâches pour un démarrage automatique)
cd /d "%~dp0.."
.venv\Scripts\goldbot run --yes
