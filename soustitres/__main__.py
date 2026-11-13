"""Sous-titres (langue d'origine + langues cibles) d'une vidéo YouTube, d'un fichier vidéo ou de toutes les vidéos
d'un dossier, en ligne de commande.

    python -m soustitres <url YouTube | fichier vidéo | dossier | fichier .lot.json> [options]

Un dossier devient un lot, comme dans l'interface web : fichier sous-titres.lot.json écrit au fil du travail, et
relancer la même commande après une coupure reprend là où le lot s'était arrêté.

Interface web : python -m soustitres.web (voir README). Les réglages viennent de config.toml recouvert par
%LOCALAPPDATA%/sous-titres/config.json (écrit par l'interface web) ; les options ci-dessous l'emportent.
"""
import argparse
import sys
import time

from . import config, doublage, etapes, lot_commande, pipeline, source, traduction, transcription, transcription_locale


def journal(msg: str):
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m soustitres", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("source", nargs="?", help="adresse YouTube, chemin d'un fichier vidéo, d'un dossier (lot) ou d'un fichier .lot.json")
    p.add_argument("--sortie", help="dossier de sortie (vidéo téléchargée, .srt ; intermédiaires dans .travail/)")
    p.add_argument("--langue", help="langue parlée (en, de…) si la détection se trompe")
    p.add_argument("--langues", help="langues cibles séparées par des virgules (fr,en…) ; défaut : réglage langues_cibles")
    p.add_argument("--sans-nettoyage", action="store_true", help="garde tout le dossier de travail (ignore [nettoyage])")
    p.add_argument("--transcription", help="id d'un serveur de transcription de la configuration")
    p.add_argument("--traducteur", help="id d'un traducteur de la configuration (local, claude, aucun…)")
    p.add_argument("--refaire", action="store_true", help="refait la transcription et la traduction même si elles existent")
    p.add_argument("--liste", action="store_true", help="affiche les moteurs configurés et quitte")
    p.add_argument("--jusqua", choices=etapes.ARRETS[1:], help="lot : dernière étape (défaut : traduction ; doublage, assemblage en MKV)")
    p.add_argument("--nouveau-lot", action="store_true", help="lot : ignore le fichier de lot existant et repart de l'inventaire du dossier")
    p.add_argument("--sans-sous-dossiers", action="store_true", help="lot : seulement les vidéos du dossier lui-même")
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
        if not source.est_url(a.source) and lot_commande.est_lot(a.source):
            return lot_commande.executer(a.source, cfg, journal, transcription=a.transcription, traducteur=a.traducteur,
                                         langue_parlee=a.langue, langues=a.langues.split(",") if a.langues else None,
                                         sortie=a.sortie, refaire=a.refaire, arret=a.jusqua, nouveau=a.nouveau_lot,
                                         recursif=not a.sans_sous_dossiers)
        d = pipeline.Demande(a.source, cfg, a.transcription, a.traducteur, a.langue, a.sortie, a.refaire,
                             langues=a.langues.split(",") if a.langues else None, nettoyer=not a.sans_nettoyage)
        pipeline.executer(d, journal)
        return 0
    except (source.ErreurSource, transcription.ErreurTranscription, traduction.ErreurTraduction, config.ErreurConfig,
            transcription_locale.ErreurServeurLocal, doublage.ErreurDoublage) as e:
        journal(f"Erreur : {e}")
        return 1
    except KeyboardInterrupt:
        journal("Interrompu.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
