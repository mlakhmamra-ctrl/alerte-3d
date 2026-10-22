#!/bin/sh
# Lance Kafka, Ollama, la simulation et la passerelle. Ensuite : ouvrir godot/project.godot dans Godot et appuyer sur F5.
# ./lancer.sh        -> avec les vrais modèles (premier lancement : téléchargement d'environ 6 Go)
# ./lancer.sh gpu    -> avec les vrais modèles, sur la carte graphique NVIDIA
# ./lancer.sh faux   -> sans modèle, avec le faux LLM de test (pour vérifier Kafka, la passerelle et Godot)
cd "$(dirname "$0")"
if ! docker info >/dev/null 2>&1; then echo "Docker ne répond pas : lancez Docker Desktop puis recommencez."; exit 1; fi
if [ "$1" = "faux" ]; then
  docker compose -f docker-compose.yml -f docker-compose.fake.yml up --build
elif [ "$1" = "gpu" ]; then
  docker compose -f docker-compose.yml -f docker-compose.gpu.yml up --build
else
  docker compose up --build
fi
