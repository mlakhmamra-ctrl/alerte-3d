@echo off
rem Lance Kafka, Ollama, la simulation et la passerelle. Ensuite : ouvrir godot\project.godot dans Godot et appuyer sur F5.
rem lancer.bat        : avec les vrais modeles (premier lancement : telechargement d environ 6 Go)
rem lancer.bat faux   : sans modele, avec le faux LLM de test
rem lancer.bat gpu    : avec les vrais modeles, calcules sur la carte graphique NVIDIA
cd /d "%~dp0"
docker info >nul 2>&1
if errorlevel 1 (
  echo Docker ne repond pas : lancez Docker Desktop puis recommencez.
  pause
  exit /b 1
)
if "%1"=="faux" (
  docker compose -f docker-compose.yml -f docker-compose.fake.yml up --build
) else if "%1"=="gpu" (
  docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build
) else (
  docker compose up --build
)
pause
