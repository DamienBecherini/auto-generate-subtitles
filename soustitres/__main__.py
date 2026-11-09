"""Sous-titres (langue d'origine + langues cibles) d'une vidéo YouTube ou d'un fichier vidéo, en ligne de commande.

    python -m soustitres <url YouTube | fichier vidéo> [options]

Interface web : python -m soustitres.web (voir README). Les réglages viennent de config.toml recouvert par
%LOCALAPPDATA%/sous-titres/config.json (écrit par l'interface web) ; les options ci-dessous l'emportent.
"""
import argparse
import sys
import time

from . import config, pipeline, source, traduction, transcription, transcription_locale


def journal(msg: str):
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m soustitres", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("source", nargs="?", help="adresse YouTube ou chemin d'un fichier vidéo")
    p.add_argument("--sortie", help="dossier de sortie (vidéo téléchargée, .srt ; intermédiaires dans .travail/)")
    p.add_argument("--langue", help="langue parlée (en, de…) si la détection se trompe")
    p.add_argument("--langues", help="langues cibles séparées par des virgules (fr,en…) ; défaut : réglage langues_cibles")
    p.add_argument("--sans-nettoyage", action="store_true", help="garde tout le dossier de travail (ignore [nettoyage])")
    p.add_argument("--transcription", help="id d'un serveur de transcription de la configuration")
    p.add_argument("--traducteur", help="id d'un traducteur de la configuration (local, claude, aucun…)")
    p.add_argument("--refaire", action="store_true", help="refait la transcription et la traduction même si elles existent")
    p.add_argument("--liste", action="store_true", help="affiche les moteurs configurés et quitte")
    a = p.parse_args(argv)

    cfg = config.charger()
    if a.liste:
        print("Serveurs de transcription (actif : %s)" % cfg.get("transcription_active"))
        for m in cfg.get("transcriptions", []):
            print(f"  {m['id']:10} {m['nom']}  {m.get('url', '')}")
        print("Traducteurs (actif : %s)" % cfg.get("traducteur_actif"))
        for m in cfg.get("traducteurs", []):
            print(f"  {m['id']:10} {m['nom']}  {m.get('modele', '')}")
        return 0
    if not a.source:
        p.error("source manquante (adresse YouTube ou fichier)")
    try:
        d = pipeline.Demande(a.source, cfg, a.transcription, a.traducteur, a.langue, a.sortie, a.refaire,
                             langues=a.langues.split(",") if a.langues else None, nettoyer=not a.sans_nettoyage)
        pipeline.executer(d, journal)
        return 0
    except (source.ErreurSource, transcription.ErreurTranscription, traduction.ErreurTraduction, config.ErreurConfig,
            transcription_locale.ErreurServeurLocal) as e:
        journal(f"Erreur : {e}")
        return 1
    except KeyboardInterrupt:
        journal("Interrompu.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
