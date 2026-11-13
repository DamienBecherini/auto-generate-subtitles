"""Starts the web interface: python -m subtitles.web [--port 4420] [--open]"""
import argparse
import webbrowser

from .. import config, i18n
from .server import serve


def main():
    p = argparse.ArgumentParser(prog="python -m subtitles.web")
    i18n.set_language(config.load().get("ui_language"))
    p.add_argument("--port", type=int, help=i18n.t("cli.port"))
    p.add_argument("--open", action="store_true", help=i18n.t("cli.open"))
    p.add_argument("--idle", type=float, help=i18n.t("cli.idle"))
    a = p.parse_args()
    cfg = config.load()
    port = a.port or int(cfg.get("web", {}).get("port", 4420))
    if a.open:
        webbrowser.open(f"http://127.0.0.1:{port}/")
    serve(port, a.idle)


if __name__ == "__main__":
    main()
