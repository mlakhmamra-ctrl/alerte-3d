@echo off
rem Spark sur les données d'ALERTE 3D.
rem   lancer_spark.bat batch  -> analyse de tous les runs (data\runs, data\etude) : tables Parquet et rapport dans data\spark\
rem   lancer_spark.bat flux   -> agrégats en direct sur le flux Kafka (lancez la simulation à côté : lancer.bat)
cd /d "%~dp0"
if /i "%1"=="flux" (
  docker compose --profile spark up spark-flux
) else (
  docker compose --profile spark-batch run --rm spark-batch
  echo.
  echo Rapport : data\spark\rapport_spark.md
)
