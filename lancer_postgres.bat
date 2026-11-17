@echo off
rem Mémoire individuelle, partagée et collective des habitants dans PostgreSQL (voir docs/memoire_postgresql.md).
rem C'est la base de la simulation (service postgres du docker compose, avec pgvector) : port 5440, mot de passe « alerte ».
rem   lancer_postgres.bat           démarre la base, charge les runs de démonstration s'il le faut, ouvre psql
rem   lancer_postgres.bat reset     recrée le schéma et recharge les runs de démonstration (efface aussi les runs en direct)
chcp 65001 >nul
cd /d "%~dp0"
set PGHOST=localhost
set PGPORT=5440
set PGUSER=postgres
set PGPASSWORD=alerte
set PGDATABASE=alerte
set PGCLIENTENCODING=UTF8
where psql >nul 2>nul || set "PATH=%PATH%;C:\Program Files\PostgreSQL\18\bin"

docker stop alerte-postgres >nul 2>nul
docker compose up -d postgres || goto :fin
:attente
docker compose exec -T postgres pg_isready -U postgres -d alerte >nul 2>nul || (timeout /t 1 >nul & goto :attente)
timeout /t 2 >nul

set SCHEMA=
if /i "%1"=="reset" set SCHEMA=1
psql -tAc "SELECT 1 FROM pg_namespace WHERE nspname = 'memoire'" | findstr 1 >nul || set SCHEMA=1
if defined SCHEMA psql -q -f postgres/01_schema.sql || goto :fin
psql -tAc "SELECT 1 FROM memoire.run WHERE run_id = '7086a147'" | findstr 1 >nul || (
    psql -q -v fichier=data/runs/exemple_regles_pont_ferme.jsonl -f postgres/02_charger.sql
    if exist data\runs\c7862089.jsonl psql -q -v fichier=data/runs/c7862089.jsonl -f postgres/02_charger.sql
)

echo.
echo Base prete. Dans psql :
echo   \i postgres/03_demo.sql                         toute la demo (run d'exemple, 12 habitants)
echo   \set run c7862089 \set agent A16 \set msg m16   puis \i postgres/03_demo.sql : le run a 100 habitants
echo   SELECT * FROM run ORDER BY charge_le DESC;      les runs, dont ceux ecrits en direct par la simulation
echo   \q                                              quitter
echo.
set PGOPTIONS=-c search_path=memoire,public
psql
:fin
pause
