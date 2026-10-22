@echo off
rem Demo en direct pour une presentation : simulation reelle avec les LLM sur la carte NVIDIA, acceleree (09:00 -> 10:00 en 12 min).
rem Ensuite : ouvrir godot\project.godot dans Godot et appuyer sur F5. R dans Godot relance un run.
cd /d "%~dp0"
docker info >nul 2>&1
if errorlevel 1 (
  echo Docker ne repond pas : lancez Docker Desktop puis recommencez.
  pause
  exit /b 1
)
docker compose -f docker-compose.yml -f docker-compose.gpu.yml -f docker-compose.demo.yml up --build
pause
