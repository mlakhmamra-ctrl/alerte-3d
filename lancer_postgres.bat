@echo off
rem Mémoire individuelle, partagée et collective des habitants dans PostgreSQL (voir docs/memoire_postgresql.md).
rem PostgreSQL tourne dans Docker (conteneur alerte-postgres, port 5440, mot de passe « alerte ») : rien à installer ni à taper.
rem   lancer_postgres.bat           ouvre psql sur la base déjà chargée
rem   lancer_postgres.bat reset     recrée le schéma et recharge les deux runs (repartir de zéro)
chcp 65001 >nul
cd /d "%~dp0"
set PGHOST=localhost
set PGPORT=5440
set PGUSER=postgres
set PGPASSWORD=alerte
set PGDATABASE=alerte
set PGCLIENTENCODING=UTF8
where psql >nul 2>nul || set "PATH=%PATH%;C:\Program Files\PostgreSQL\18\bin"

docker start alerte-postgres >nul 2>nul || docker run -d --name alerte-postgres --restart unless-stopped -e POSTGRES_PASSWORD=alerte -e POSTGRES_DB=alerte -p 5440:5432 -v alerte-pgdata:/var/lib/postgresql/data postgres:15-alpine >nul
:attente
docker exec alerte-postgres pg_isready -U postgres -d alerte >nul 2>nul || (timeout /t 1 >nul & goto :attente)
timeout /t 2 >nul

psql -tAc "SELECT 1 FROM pg_namespace WHERE nspname = 'memoire'" | findstr 1 >nul || set RESET=1
if /i "%1"=="reset" set RESET=1
if defined RESET (
    psql -q -f postgres/01_schema.sql || goto :fin
    psql -q -v fichier=data/runs/exemple_regles_pont_ferme.jsonl -f postgres/02_charger.sql || goto :fin
    if exist data\runs\c7862089.jsonl psql -q -v fichier=data/runs/c7862089.jsonl -f postgres/02_charger.sql
)

echo.
echo Base prete. Dans psql :
echo   \i postgres/03_demo.sql                         toute la demo (run d'exemple, 12 habitants)
echo   \set run c7862089 \set agent A16 \set msg m16   puis \i postgres/03_demo.sql : le run a 100 habitants
echo   \q                                              quitter
echo.
set PGOPTIONS=-c search_path=memoire,public
psql
:fin
pause
