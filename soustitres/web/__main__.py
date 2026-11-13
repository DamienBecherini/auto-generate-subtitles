"""Lance l'interface web : python -m soustitres.web [--port 4420] [--ouvrir]"""
import argparse
import webbrowser

from .. import config, i18n
from .serveur import servir


def main():
    p = argparse.ArgumentParser(prog="python -m soustitres.web")
    i18n.definir(config.charger().get("langue_interface"))
    p.add_argument("--port", type=int, help=i18n.t("cli.port"))
    p.add_argument("--ouvrir", action="store_true", help=i18n.t("cli.open"))
    p.add_argument("--inactivite", type=float, help=i18n.t("cli.idle"))
    a = p.parse_args()
    cfg = config.charger()
    port = a.port or int(cfg.get("web", {}).get("port", 4420))
    if a.ouvrir:
        webbrowser.open(f"http://127.0.0.1:{port}/")
    servir(port, a.inactivite)


if __name__ == "__main__":
    main()
