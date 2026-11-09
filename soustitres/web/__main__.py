"""Lance l'interface web : python -m soustitres.web [--port 4420] [--ouvrir]"""
import argparse
import webbrowser

from .. import config
from .serveur import servir


def main():
    p = argparse.ArgumentParser(prog="python -m soustitres.web")
    p.add_argument("--port", type=int, help="port d'écoute (défaut : [web] port de la configuration, 4420)")
    p.add_argument("--ouvrir", action="store_true", help="ouvre la page dans le navigateur")
    p.add_argument("--inactivite", type=float, help="minutes sans activité avant l'arrêt (défaut : [web] inactivite_min, 60 ; 0 = jamais)")
    a = p.parse_args()
    cfg = config.charger()
    port = a.port or int(cfg.get("web", {}).get("port", 4420))
    if a.ouvrir:
        webbrowser.open(f"http://127.0.0.1:{port}/")
    servir(port, a.inactivite)


if __name__ == "__main__":
    main()
