# ALERTE 3D — décision collective pendant une crue (thématique ESSAIM)


```
 ┌──────────── Docker (docker compose up) ────────────────────────────────────┐
 │  ollama  ◄──HTTP──  sim (Python)  ──événements──►  Kafka  ──►  gateway  ───┼──WebSocket──►  Godot 4 (sur votre PC)
 │  qwen2.5:7b / 0.5b   12 habitants                 alerte.events   (valide,  │   ws://127.0.0.1:8765
 │  nomic-embed-text    crue, rues, RAG              alerte.commands  indicateurs)◄─ commandes (alerte, rumeur, pause…)
 └─────────────────────────────────────────────────────────────────────────────┘
```

## Lancer

Il faut Docker Desktop et Godot 4.3 ou plus récent (version standard, pas .NET).

1. Dans ce dossier : `docker compose up --build`
   (ou double-clic sur `lancer.bat` sous Windows, `./lancer.sh` sous Mac/Linux).
   Le premier lancement télécharge les modèles (environ 6 Go) : c'est long, laissez la fenêtre ouverte.
   La simulation démarre quand vous voyez `modèle prêt : qwen2.5:1.5b` puis des lignes `sim-1`.
2. Ouvrez Godot → Importer → `godot/project.godot` → F5. La scène se connecte toute seule à `ws://127.0.0.1:8765`.
3. Arrêt : Ctrl+C, puis `docker compose down`.



