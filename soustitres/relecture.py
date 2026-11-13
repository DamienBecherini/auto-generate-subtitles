"""Relecture IA d'une transcription : repère les noms propres déformés et propose leur correction, sans rien modifier.

Fichier du lot `sous-titres.relecture.json`, à côté du fichier de lot : {"examines": [noms déjà soumis], "propositions": [...]}.
Un nom n'est soumis qu'une fois par lot, même présent dans 200 vidéos. Dans le dossier de travail de chaque vidéo,
`relecture.json` liste les noms de cette vidéo qui attendaient une décision. Une vidéo est validée quand aucun de ses
noms n'attend plus : les noms acceptés rejoignent le glossaire (voir glossaire.py), les autres restent dans « examines ».
"""
import collections
import json
import re
import threading
from pathlib import Path

from . import glossaire, i18n, traduction

NOM_FICHIER = "sous-titres.relecture.json"
FICHIER_VIDEO = "relecture.json"
TAILLE_PAQUET = 70
_verrou = threading.RLock()

_MOT = r"[A-ZÀ-Ý][a-zà-ÿ]+(?:[-'’][A-Za-zà-ÿ]+)*"
_SUITE = re.compile(rf"{_MOT}(?:\s+(?:de |van |von |der |al-)?{_MOT})*")

CONSIGNE = """Tu relis une transcription automatique (reconnaissance vocale). La reconnaissance vocale déforme souvent les noms rares selon leur son.

On te donne le titre de la vidéo, puis des noms propres extraits, avec nombre d'occurrences et contexte.
Ne renvoie QUE les noms utiles, parmi trois cas ; omets tous les autres (noms justes sans forme française différente, mots ordinaires) :
- « a_corriger » : le nom est mal transcrit et tu reconnais la vraie personne, le lieu, l'œuvre ou la notion d'après le son et le contexte ;
- « incertain » : le nom semble déformé mais tu ne sais pas établir le bon ;
- « forme_francaise » : le nom est juste, s'écrit autrement en français, et un traducteur pourrait se tromper (Plato → Platon, titre d'un livre traduit).
  Omets les pays, grandes villes, religions, peuples et personnages que tout traducteur connaît (China, Christianity, Jesus).

Ne devine jamais : si tu ne reconnais pas avec certitude ce dont il s'agit, utilise « incertain » et laisse « graphie » vide.
Pas de correction pour un simple doute sur l'orthographe d'un nom inconnu, ni pour un nom dont le contexte ne permet pas d'identifier la référence.
Pour chaque entrée : « index » = numéro du nom, « graphie » = forme correcte dans la langue de la vidéo, « francais » = forme usuelle en français,
« confiance », « raison » en une phrase courte en français."""

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["entrees"], "properties": {"entrees": {"type": "array", "items": {
    "type": "object", "additionalProperties": False, "required": ["index", "statut", "graphie", "francais", "confiance", "raison"],
    "properties": {"index": {"type": "integer"}, "statut": {"type": "string", "enum": ["a_corriger", "incertain", "forme_francaise"]},
                   "graphie": {"type": "string"}, "francais": {"type": "string"},
                   "confiance": {"type": "string", "enum": ["haute", "moyenne", "faible"]}, "raison": {"type": "string"}}}}}}


def chemin(fichier_lot: str | Path) -> Path:
    return Path(fichier_lot).with_name(NOM_FICHIER)


def moteur(cfg: dict, traducteur_id: str | None = None) -> dict | None:
    """Le traducteur Claude choisi pour le lot, sinon le premier configuré ; None s'il n'y en a pas (relecture impossible)."""
    claudes = [m for m in cfg.get("traducteurs", []) if m.get("type") == "claude"]
    return next((m for m in claudes if m.get("id") == traducteur_id), claudes[0] if claudes else None)


def _lire(f: Path) -> dict:
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"examines": [], "propositions": []}
    return {"examines": list(d.get("examines") or []), "propositions": list(d.get("propositions") or [])}


def _ecrire(f: Path, d: dict):
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(f)


def noms_en_attente(fichier_lot: str | Path) -> set[str]:
    return {p["transcrit"] for p in _lire(chemin(fichier_lot))["propositions"]}


def _sans_possessif(n: str) -> str:
    return re.sub(r"['’]s$", "", n)


def candidats(texte: str) -> list[dict]:
    """Noms propres d'un texte : [{nom, occurrences, exemples}]. Un mot seul surtout écrit en minuscules est un mot courant."""
    minuscules = collections.Counter(m.lower() for m in re.findall(r"\b[a-zà-ÿ]{2,}\b", texte))
    vus, exemples = collections.Counter(), collections.defaultdict(list)
    for phrase in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", texte)):
        for m in _SUITE.finditer(phrase):
            c = _sans_possessif(m.group(0))
            debut = m.start() == 0
            mots = c.split()
            if len(mots) == 1 and (len(c) < 4 or minuscules[c.lower()] >= 3 or (debut and minuscules[c.lower()] >= 1)):
                continue
            if debut and len(mots) > 1 and minuscules[mots[0].lower()] >= 1:      # « The Gariaev » en tête de phrase
                c = " ".join(mots[1:])
                if len(c) < 4:
                    continue
            vus[c] += 1
            court = phrase.strip()[:170]
            if len(exemples[c]) < 2 and court not in exemples[c]:
                exemples[c].append(court)
    return [{"nom": c, "occurrences": vus[c], "exemples": exemples[c]} for c in sorted(vus, key=str.lower)]


def _soumettre(traducteur, titre: str, paquet: list[dict]) -> list[dict]:
    lignes = [f"{i}. {c['nom']} ({c['occurrences']} fois) : " + " / ".join(c["exemples"]) for i, c in enumerate(paquet)]
    brut = traducteur.completer(CONSIGNE, f"Titre : {titre}\n\n" + "\n".join(lignes), schema=SCHEMA, effort="medium")
    r = []
    for e in json.loads(brut)["entrees"]:
        if 0 <= e["index"] < len(paquet):
            c = paquet[e["index"]]
            graphie = c["nom"] if e["statut"] == "forme_francaise" else e["graphie"]    # le nom est juste : ne pas le réécrire (titre coupé, etc.)
            r.append({"transcrit": c["nom"], "statut": e["statut"], "graphie": graphie, "francais": e["francais"], "confiance": e["confiance"],
                      "raison": e["raison"], "occurrences": c["occurrences"], "exemples": c["exemples"]})
    return r


def relire(m: dict, fichier_lot: str | Path, travail: Path, titre: str, textes: list[str], journal=print, annule=lambda: False, creer=traduction.creer) -> list[str]:
    """Écrit les propositions de cette vidéo ; renvoie ses noms en attente de décision. Une panne d'API laisse la relecture à refaire."""
    f = chemin(fichier_lot)
    tous = candidats(" ".join(textes))
    with _verrou:
        d = _lire(f)
    entrees = glossaire.charger(glossaire.chemin(fichier_lot))
    connus = set(d["examines"]) | {p["transcrit"] for p in d["propositions"]} | {e["transcrit"] for e in entrees} | {e.get("graphie") for e in entrees} | {e.get("francais") for e in entrees}
    a_soumettre = [c for c in tous if c["nom"] not in connus]
    journal(i18n.t("log.review_names", n=len(tous), new=len(a_soumettre)))
    if a_soumettre:
        traducteur = creer(m, journal)
        nouvelles = []
        try:
            for i in range(0, len(a_soumettre), TAILLE_PAQUET):
                if annule():
                    raise traduction.ErreurTraduction(i18n.t("err.cancelled"))
                nouvelles += _soumettre(traducteur, titre, a_soumettre[i:i + TAILLE_PAQUET])
        finally:
            traducteur.terminer()
        with _verrou:                          # relu : une validation a pu arriver pendant l'appel
            d = _lire(f)
            d["examines"] = sorted(set(d["examines"]) | {c["nom"] for c in a_soumettre})
            deja = {p["transcrit"] for p in d["propositions"]}
            d["propositions"] += [p for p in nouvelles if p["transcrit"] not in deja]
            _ecrire(f, d)
        journal(i18n.t("log.review_to_check", n=len(nouvelles)))
    with _verrou:
        attente = noms_en_attente(fichier_lot)
    miens = sorted({c["nom"] for c in tous} & attente)
    _ecrire(travail / FICHIER_VIDEO, {"noms": miens})
    return miens


def propositions(fichier_lot: str | Path, noms: set[str] | None = None) -> list[dict]:
    """Propositions en attente, éventuellement limitées à ces noms ; les plus fréquentes d'abord."""
    with _verrou:
        liste = _lire(chemin(fichier_lot))["propositions"]
    return sorted((p for p in liste if noms is None or p["transcrit"] in noms), key=lambda p: (-p.get("occurrences", 0), p["transcrit"].lower()))


def valider(fichier_lot: str | Path, decisions: list[dict]) -> tuple[int, int]:
    """Applique les décisions {transcrit, graphie, francais, accepter}. Renvoie (acceptés, écartés).
    Un nom accepté sans graphie différente et sans forme française n'apporte rien : il est écarté."""
    with _verrou:
        f = chemin(fichier_lot)
        d = _lire(f)
        en_attente = {p["transcrit"]: p for p in d["propositions"]}
        acceptes, ecartes, nouvelles = 0, 0, []
        for x in decisions:
            p = en_attente.pop(x.get("transcrit"), None)
            if p is None:
                continue
            graphie, francais = str(x.get("graphie") or "").strip(), str(x.get("francais") or "").strip()
            if x.get("accepter") and graphie and (graphie != p["transcrit"] or francais):
                nouvelles.append({"transcrit": p["transcrit"], "graphie": graphie, "francais": francais, "statut": p.get("statut", "a_corriger")})
                acceptes += 1
            else:
                ecartes += 1
        if nouvelles:
            glossaire.ajouter(glossaire.chemin(fichier_lot), nouvelles)
        d["propositions"] = [p for p in d["propositions"] if p["transcrit"] in en_attente]
        _ecrire(f, d)
        return acceptes, ecartes

