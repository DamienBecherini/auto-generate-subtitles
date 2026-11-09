"""La chaîne complète, partagée par la ligne de commande et l'interface web.

Dossier de sortie `sortie` : la vidéo téléchargée (YouTube) ou copiée (glisser-déposer) ; les intermédiaires (audio
extrait, transcription.json, sous-titres, traduction.<langue>.json, .vtt) dans `sortie/.travail/<nom>/`.
Fichiers produits : `<nom>.<langue d'origine>.srt` et un `<nom>.<cible>.srt` par langue cible, copiés à côté de la
vidéo et/ou dans le dossier de sortie (table `[destination]`), puis nettoyage du dossier de travail (`[nettoyage]`).

Deux phases : `preparer` (source, audio, transcription, sous-titres d'origine) puis `traduire_et_copier`
(traductions, copies, nettoyage) ; `executer` enchaîne les deux.
"""
import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config, decoupe, formats, langue, nettoyage, source, traduction, transcription, transcription_locale
from .decoupe import Reglages, SousTitre

ETAPES = ["source", "transcription", "decoupe", "traduction", "fin"]
DESTINATION_DEFAUT = {"a_cote": True, "sortie": False}


class Annulee(Exception):
    pass


@dataclass
class Demande:
    source: str                        # URL ou chemin
    cfg: dict
    transcription_id: str | None = None
    traducteur_id: str | None = None
    langue: str | None = None          # langue parlée ; None = réglage, "" = détection
    sortie: str | None = None
    refaire: bool = False
    sur_travail: object = None         # rappel appelé avec le dossier .travail/<nom> dès qu'il est connu (journal)
    langues: list[str] | None = None   # langues cibles ; None = cfg["langues_cibles"]
    destination: dict | None = None    # {"a_cote": bool, "sortie": bool} ; None = cfg["destination"]
    nettoyer: bool = True              # applique [nettoyage] au dossier de travail à la fin
    sur_duree: object = None           # rappel appelé avec la durée de l'audio avant de le transcrire (temps restant)


@dataclass
class Fichier:
    langue: str
    chemin: Path                       # emplacement principal (première copie, sinon le dossier de travail)
    nb: int
    traduit: bool
    copies: list[Path] = field(default_factory=list)


@dataclass
class Prepare:
    """Résultat de la première phase (tout ce qui ne dépend pas du traducteur)."""
    video: Path
    travail: Path
    base: str
    code: str                          # langue parlée
    sous_titres: list[SousTitre]
    srt_origine: Path
    sortie: Path
    debut: float


@dataclass
class Resultat:
    video: Path
    srt: Path                          # fichier principal : première langue cible, ou langue d'origine
    srt_origine: Path
    langue: str
    nb_sous_titres: int
    traduit: bool
    copies: list[Path] = field(default_factory=list)
    duree_s: float = 0.0
    fichiers: list[Fichier] = field(default_factory=list)


def langues_cibles(d: Demande) -> list[str]:
    return langue.normaliser_codes(d.langues if d.langues is not None else d.cfg.get("langues_cibles", ["fr"]))


def destination(d: Demande) -> dict:
    return {**DESTINATION_DEFAUT, **(d.cfg.get("destination") or {}), **(d.destination or {})}


def texte_duree(secondes: float) -> str:
    s = round(secondes)
    h, m, s = s // 3600, s % 3600 // 60, s % 60
    return f"{h} h {m:02d} min" if h else f"{m} min {s:02d} s" if m else f"{s} s"


def delai_transcription(m: dict, duree_s: float) -> int:
    """Attente maximale de la réponse : 4 fois le temps prévu, au moins 5 min ; 2 h si la vitesse n'est pas renseignée."""
    try:
        vitesse = float(m.get("vitesse") or 0)
    except (TypeError, ValueError):
        vitesse = 0
    return max(300, round(4 * duree_s / vitesse)) if vitesse > 0 else 7200


def message_transcription(m: dict, duree_s: float) -> str:
    """Temps prévu d'après la vitesse du serveur choisi (champ `vitesse`) et la durée de l'audio."""
    audio = texte_duree(duree_s)
    try:
        vitesse = float(m.get("vitesse") or 0)
    except (TypeError, ValueError):
        vitesse = 0
    if vitesse <= 0:
        return f"Transcription en cours ({audio} d'audio, vitesse de « {m.get('nom')} » non renseignée)…"
    return f"Transcription en cours : environ {texte_duree(duree_s / vitesse)} pour {audio} d'audio (« {m.get('nom')} », {vitesse:g} s d'audio par seconde)…"


def fichier_traduction(travail: Path, cible: str) -> Path:
    f = travail / f"traduction.{cible}.json"
    if cible == "fr" and not f.exists() and (travail / "traduction.json").exists():
        return travail / "traduction.json"     # ancien nom (avant les langues multiples)
    return f


def langues_restantes(p: Prepare, langues: list[str], refaire: bool = False) -> list[str]:
    """Langues réellement à traduire : ni la langue parlée, ni celles dont la traduction de ce découpage est conservée."""
    def faite(cible: str) -> bool:
        try:
            return len(json.loads(fichier_traduction(p.travail, cible).read_text(encoding="utf-8"))) == len(p.sous_titres)
        except (OSError, ValueError, TypeError):
            return False
    return [c for c in langues if c != p.code and (refaire or not faite(c))]


def dossier_travail(sortie: str | Path, video: str | Path) -> Path:
    return Path(sortie) / ".travail" / source.slug(Path(video).stem)


def transcription_conservee(sortie: str | Path, video: str | Path) -> bool:
    """Transcription déjà faite : `preparer` la reprendra en quelques secondes, sans serveur de transcription."""
    return (dossier_travail(sortie, video) / "transcription.json").is_file()


def executer(d: Demande, journal=print, etape=lambda nom: None, annule=lambda: False) -> Resultat:
    p = preparer(d, journal, etape, annule)
    return traduire_et_copier(d, p, journal, etape, annule)


def preparer(d: Demande, journal=print, etape=lambda nom: None, annule=lambda: False) -> Prepare:
    cfg = d.cfg
    reglages = Reglages(**cfg.get("sous_titres", {}))
    sortie = Path(d.sortie or cfg["sortie"])
    sortie.mkdir(parents=True, exist_ok=True)
    m_tr = config.moteur(cfg, "transcriptions", "transcription_active", d.transcription_id)
    langue_voulue = (d.langue if d.langue is not None else cfg.get("langue", "")) or None
    t_debut = time.perf_counter()

    def verifier():
        if annule():
            raise Annulee()

    # 1. Source
    etape("source")
    if source.est_url(d.source):
        video = source.telecharger_youtube(d.source, cfg.get("youtube", {}).get("commande", ["yt-dlp"]), journal, dossier=sortie)
    else:
        video = Path(d.source).expanduser().resolve()
        if not video.is_file():
            raise source.ErreurSource(f"fichier introuvable : {video}")
    base = source.slug(video.stem)
    travail = dossier_travail(sortie, video)
    travail.mkdir(parents=True, exist_ok=True)
    if d.sur_travail:
        d.sur_travail(travail)
    fichier_tr = travail / "transcription.json"
    audios = [p for p in travail.glob(f"{base}.*") if source.est_audio_extrait(p, base)]
    if fichier_tr.exists() and not d.refaire and not audios:
        audio = None                                   # transcription conservée, audio nettoyé : rien à extraire
    elif audios:
        audio = audios[0]
        journal(f"Audio déjà extrait : {audio.name}")
    else:
        journal(f"Extraction de l'audio de {video.name}…")
        audio = source.extraire_audio(video, travail / f"{base}.audio")
    if audio:
        journal(f"Audio : {audio.name} ({audio.stat().st_size / 1e6:.1f} Mo, {source.duree(audio):.0f} s) ; travail dans {travail}")
    verifier()

    # 2. Transcription
    etape("transcription")
    if fichier_tr.exists() and not d.refaire:
        tr = json.loads(fichier_tr.read_text(encoding="utf-8"))
        journal(f"Transcription déjà faite ({len(tr['words'])} mots)")
    else:
        serveur_local = None
        if m_tr.get("commande"):
            serveur_local = transcription_locale.ServeurLocal.pour(m_tr)
            serveur_local.assurer(journal)
        journal(f"Transcription par « {m_tr['nom']} » ({m_tr['url']}) : {transcription.sante(m_tr['url'])}")
        duree_audio = source.duree(audio)
        if d.sur_duree:
            d.sur_duree(duree_audio)
        journal(message_transcription(m_tr, duree_audio))
        t = time.perf_counter()
        cle = config.resoudre_cle(m_tr.get("cle", ""))
        langue_tr = langue_voulue if langue_voulue != "fr" else None
        delai = delai_transcription(m_tr, duree_audio)
        try:
            tr = transcription.transcrire(m_tr["url"], audio, langue_tr, delai_s=delai, cle=cle)
        except transcription.DelaiDepasse as e:
            journal(f"{e} Nouvel essai.")      # réponse perdue en route (déjà vu) : un lot ne doit pas rester bloqué
            tr = transcription.transcrire(m_tr["url"], audio, langue_tr, delai_s=delai, cle=cle)
        fichier_tr.write_text(json.dumps(tr, ensure_ascii=False, indent=1), encoding="utf-8")
        journal(f"Transcrit en {time.perf_counter() - t:.0f} s : {len(tr['words'])} mots, {len(tr['segments'])} morceaux")
    if not tr["words"]:
        raise transcription.ErreurTranscription("aucune parole détectée : rien à sous-titrer")
    verifier()

    # 3. Découpe dans la langue d'origine
    etape("decoupe")
    code = langue_voulue or tr.get("language") or langue.detecter(tr.get("text", "")) or "xx"
    st = decoupe.construire(decoupe.depuis_json(tr["words"]), reglages)
    srt_origine = travail / f"{base}.{code}.srt"
    srt_origine.write_text(formats.srt(st, reglages), encoding="utf-8")
    journal(f"Langue {code} ({langue.nom(code)}) : {len(st)} sous-titres → {srt_origine.name}")
    return Prepare(video, travail, base, code, st, srt_origine, sortie, t_debut)


def traduire_et_copier(d: Demande, p: Prepare, journal=print, etape=lambda nom: None, annule=lambda: False) -> Resultat:
    cfg = d.cfg
    reglages = Reglages(**cfg.get("sous_titres", {}))
    m_trad = config.moteur(cfg, "traducteurs", "traducteur_actif", d.traducteur_id)
    cibles = [c for c in langues_cibles(d) if c != p.code]
    fichiers = [Fichier(p.code, p.srt_origine, len(p.sous_titres), False)]

    # 4. Traductions, une par langue cible
    etape("traduction")
    if m_trad.get("type", "aucun") == "aucun" or not cibles:
        journal("Pas de traduction (traducteur « aucun », ou la vidéo est déjà dans la ou les langues voulues).")
    else:
        textes = [s.texte for s in p.sous_titres]
        tradr = None
        try:
            for cible in cibles:
                fichier_trad = fichier_traduction(p.travail, cible)
                traduits = None
                if fichier_trad.exists() and not d.refaire:
                    traduits = json.loads(fichier_trad.read_text(encoding="utf-8"))
                    if len(traduits) != len(textes):
                        traduits = None
                        journal(f"Traduction {cible} existante d'un autre découpage : refaite")
                    else:
                        journal(f"Traduction {cible} déjà faite")
                if traduits is None:
                    if tradr is None:
                        tradr = traduction.creer(m_trad, journal)
                    journal(f"Traduction {p.code} → {cible} ({langue.nom(cible)}) par « {m_trad['nom']} » ({len(textes)} sous-titres)…")
                    t = time.perf_counter()
                    traduits = traduction.traduire_textes(tradr, textes, int(cfg.get("traduction", {}).get("lot", 25)),
                                                          journal, annule=annule, cible=cible)
                    fichier_trad.write_text(json.dumps(traduits, ensure_ascii=False, indent=1), encoding="utf-8")
                    journal(f"Traduit en {time.perf_counter() - t:.0f} s")
                final = decoupe.scinder_trop_longs([SousTitre(s.debut, s.fin, t) for s, t in zip(p.sous_titres, traduits)], reglages)
                final = decoupe.ajuster_durees(final, reglages)
                srt = p.travail / f"{p.base}.{cible}.srt"
                srt.write_text(formats.srt(final, reglages), encoding="utf-8")
                (p.travail / f"{p.base}.{cible}.vtt").write_text(formats.vtt(final, reglages), encoding="utf-8")
                fichiers.append(Fichier(cible, srt, len(final), True))
                journal(f"{len(final)} sous-titres {langue.nom(cible)} → {srt.name} (+ .vtt)")
        finally:
            if tradr is not None:
                tradr.terminer()

    # 5. Copies à côté de la vidéo et/ou dans le dossier de sortie, au nom de la vidéo + langue
    etape("fin")
    dest = destination(d)
    dossiers = []
    if dest.get("a_cote"):
        dossiers.append(p.video.parent)
    if dest.get("sortie") and p.sortie.resolve() not in {x.resolve() for x in dossiers}:
        dossiers.append(p.sortie)
    if not dossiers:
        journal("Aucune destination cochée : les sous-titres restent dans le dossier de travail.")
    toutes_copies = []
    for f in fichiers:
        for dossier in dossiers:
            cible = dossier / f"{p.video.stem}.{f.langue}.srt"
            if cible.resolve() != f.chemin.resolve():
                shutil.copyfile(f.chemin, cible)
                f.copies.append(cible)
        if f.copies:
            f.chemin = f.copies[0]
        toutes_copies.extend(f.copies)
    for c in toutes_copies:
        journal(f"Sous-titres : {c}")

    # 6. Nettoyage du dossier de travail
    if d.nettoyer:
        opts = nettoyage.options(cfg)
        if not toutes_copies and opts.get("sous_titres"):
            opts = {**opts, "sous_titres": False}       # seule copie des résultats : on la garde
        nettoyage.nettoyer_dossier(p.travail, opts, journal)

    principal = next((f for f in fichiers if f.traduit), fichiers[0])
    duree = time.perf_counter() - p.debut
    journal(f"Terminé en {duree:.0f} s. Vidéo : {p.video}")
    return Resultat(p.video, principal.chemin, p.srt_origine, p.code, principal.nb, principal.traduit, toutes_copies, duree, fichiers)
