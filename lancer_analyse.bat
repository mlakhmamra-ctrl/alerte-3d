@echo off
rem Ouvre l'application d'analyse des runs dans le navigateur. Ensuite : « Charger des runs… » (data\runs\*.jsonl)
rem ou « Charger un dossier… » (ex. data\etude) pour comparer les paramètres. Rien n'est envoyé : tout reste dans le navigateur.
cd /d "%~dp0"
start "" "analyse\index.html"
 