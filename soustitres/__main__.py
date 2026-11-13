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

from . import config, doublage, etapes, i18n, lot_commande, pipeline, source, traduction, transcription, transcription_locale


def journal(msg: str):
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def main(argv=None) -> int:
    avant = argparse.ArgumentParser(add_help=False)
    avant.add_argument("--lang")
    choix = avant.parse_known_args(argv)[0].lang
    cfg = config.charger()
    i18n.definir(choix or cfg.get("langue_interface"))
    p = argparse.ArgumentParser(prog="python -m soustitres", description=i18n.t("cli.description"), formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--lang", help=i18n.t("cli.lang", langs=", ".join(i18n.disponibles())))
    p.add_argument("source", nargs="?", help=i18n.t("cli.source"))
    p.add_argument("--sortie", help=i18n.t("cli.output"))
    p.add_argument("--langue", help=i18n.t("cli.spoken"))
    p.add_argument("--langues", help=i18n.t("cli.targets"))
    p.add_argument("--sans-nettoyage", action="store_true", help=i18n.t("cli.no_cleanup"))
    p.add_argument("--transcription", help=i18n.t("cli.transcription"))
    p.add_argument("--traducteur", help=i18n.t("cli.translator"))
    p.add_argument("--refaire", action="store_true", help=i18n.t("cli.redo"))
    p.add_argument("--liste", action="store_true", help=i18n.t("cli.list"))
    p.add_argument("--jusqua", choices=etapes.ARRETS[1:], help=i18n.t("cli.until"))
    p.add_argument("--nouveau-lot", action="store_true", help=i18n.t("cli.new_batch"))
    p.add_argument("--sans-sous-dossiers", action="store_true", help=i18n.t("cli.no_subfolders"))
    a = p.parse_args(argv)

    if a.liste:
        print(i18n.t("cli.servers", active=cfg.get("transcription_active")))
        for m in cfg.get("transcriptions", []):
            print(f"  {m['id']:10} {config.nom_moteur(m)}  {m.get('url', '')}")
        print(i18n.t("cli.translators", active=cfg.get("traducteur_actif")))
        for m in cfg.get("traducteurs", []):
            print(f"  {m['id']:10} {config.nom_moteur(m)}  {m.get('modele', '')}")
        return 0
    if not a.source:
        p.error(i18n.t("cli.missing_source"))
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
        journal(i18n.t("log.error", error=e))
        return 1
    except KeyboardInterrupt:
        journal(i18n.t("log.interrupted"))
        return 130


if __name__ == "__main__":
    sys.exit(main())
