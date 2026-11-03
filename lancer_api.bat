@echo off
rem Lance Kafka, la simulation et la passerelle SANS Ollama : les agents LLM utilisent les API de LLM_BACKENDS (fichier .env).
rem Ensuite : ouvrir godot\project.godot dans Godot et appuyer sur F5.
cd /d "%~dp0"
docker info >nul 2>&1
if errorlevel 1 (
  echo Docker ne repond pas : lancez Docker Desktop puis recommencez.
  pause
  exit /b 1
)
docker compose -f docker-compose.yml -f docker-compose.api.yml up --build
pause
