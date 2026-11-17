"""Les modules de sim/ s'importent comme dans le conteneur (WORKDIR /app, modules à plat).
La passerelle a aussi un main.py : elle est chargée par son chemin (test_unitaires.py), pas par sys.path."""
import os
import sys

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIM = os.path.join(RACINE, "sim")
if SIM not in sys.path:
    sys.path.insert(0, SIM)
