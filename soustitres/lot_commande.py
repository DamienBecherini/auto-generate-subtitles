"""Un dossier entier en ligne de commande : le même lot que dans l'interface web (`sous-titres.lot.json`, reprise après
coupure), mené par le même gestionnaire de tâches, sans serveur ni pause avant la traduction.

    python -m soustitres <dossier | fichier .lot.json> [options]

Si le dossier a déjà un fichier de lot (dans le dossier lui-même, ou dans le dossier de sortie quand la destination
« dossier de sortie » est cochée), la commande le reprend : les vidéos faites sont sautées, les autres repartent de
la transcription conservée. `--nouveau-lot` repart d'un inventaire du dossier.
"""
import time
from pathlib import Path

from . import dossier, langue, source

ATTENTE_S = 1.0


def est_lot(chemin: str) -> bool:
    p = Path(chemin)
    return p.is_dir() or (p.is_file() and p.name.endswith(".lot.json"))


def trouver_fichier_lot(chemin: Path, cfg: dict, sortie: str | None) -> Path | None:
    """Fichier de lot existant pour ce dossier (ou ce fichier lui-même), None s'il n'y en a pas."""
    if chemin.is_file():
        return chemin
    candidats = [chemin / dossier.NOM_LOT, Path(sortie or cfg["sortie"]) / dossier.NOM_LOT]
    for f in candidats:
        if not f.is_file():
            continue
        try:
            racine = dossier.lire_lot(f).get("racine", "")
        except source.ErreurSource:
            continue
        if racine and Path(racine).resolve() == chemin.resolve():      # le dossier de sortie peut servir à un autre lot
            return f
    return None


def executer(chemin: str, cfg: dict, journal, *, transcription: str | None = None, traducteur: str | None = None,
             langue_parlee: str | None = None, langues: list[str] | None = None, sortie: str | None = None, refaire: bool = False,
             arret: str | None = None, nouveau: bool = False, recursif: bool = True, gestionnaire=None) -> int:
    """Crée ou reprend le lot, le suit jusqu'au bout en recopiant le journal des vidéos. 0 si tout est fait, 1 sinon."""
    from .web.taches import Gestionnaire        # pas d'import du paquet web pour une vidéo seule

    g = gestionnaire or Gestionnaire()
    p = Path(chemin.strip().strip('"'))
    fichier = None if nouveau and p.is_dir() else trouver_fichier_lot(p, cfg, sortie)
    if fichier:
        lot = g.charger_lot(str(fichier))
        modifs = {"traducteur": traducteur, "transcription": transcription, "pause": False, "arret": arret}
        if langues is not None:
            modifs["langues"] = langues
        g.lot_reprendre(lot.id, modifs)
        faites = sum(1 for v in lot.videos if v["etat"] == "fait")
        journal(f"Reprise du lot {lot.fichier} : {faites}/{len(lot.videos)} vidéo(s) déjà faite(s).")
    else:
        if not p.is_dir():
            raise source.ErreurSource(f"dossier introuvable : {p}")
        videos = dossier.parcourir(p, recursif)
        if not videos:
            raise source.ErreurSource(f"aucune vidéo dans {p}")
        options = {"transcription": transcription or "", "traducteur": traducteur or "", "pause": False, "refaire": refaire}
        if langues is not None:
            options["langues"] = langue.normaliser_codes(langues)
        if sortie:
            options["sortie"] = sortie
        if arret:
            options["arret"] = arret
        lot = g.creer_lot(str(p).replace("\\", "/"), [{"chemin": v["chemin"], "langue": langue_parlee or ""} for v in videos], options)
        journal(f"Lot de {len(videos)} vidéo(s), fichier de lot : {lot.fichier}")
    try:
        return suivre(g, lot, journal)
    except KeyboardInterrupt:
        g.lot_suspendre(lot.id)
        journal(f"Interrompu. Relancer la même commande reprend le lot ({lot.fichier}).")
        return 130


def suivre(g, lot, journal, attente_s: float = ATTENTE_S) -> int:
    lus: dict[str, int] = {}                    # tâche → lignes de journal déjà affichées
    annoncees: set[str] = set()
    while True:
        if lot.etat == "attente_traduction":    # ne devrait pas arriver sans pause ; on traduit tout de suite
            g.lot_traduire(lot.id, None, None)
        fini = lot.etat in ("termine", "annule")    # lu avant le journal : les dernières lignes s'affichent
        plusieurs = lot.simultanees > 1
        for v in list(lot.videos):
            t = g.taches.get(v.get("tache") or "")
            if t is None or t.etat == "en_attente":
                continue
            if t.id not in annoncees:
                annoncees.add(t.id)
                rang = lot.videos.index(v) + 1
                journal(f"── {rang}/{len(lot.videos)} : {v['nom']} ──")
            debut = lus.get(t.id, 0)
            for ligne in t.journal[debut:]:
                print(("  " + v["nom"] + " " if plusieurs else "  ") + ligne, flush=True)
            lus[t.id] = len(t.journal)
        if fini:
            break
        time.sleep(attente_s)
    c = {e: sum(1 for v in lot.videos if v["etat"] == e) for e in ("fait", "erreur", "annule")}
    journal(f"Lot {'terminé' if lot.etat == 'termine' else 'annulé'} : {c['fait']} faite(s), {c['erreur']} en erreur, {c['annule']} annulée(s).")
    for v in lot.videos:
        if v["etat"] == "erreur":
            journal(f"  {v['nom']} : {v.get('erreur') or 'erreur'}")
    return 0 if lot.etat == "termine" and not c["erreur"] else 1
