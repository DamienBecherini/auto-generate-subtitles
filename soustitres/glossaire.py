"""Glossaire validé d'un lot : corrige les noms mal transcrits et guide leur traduction.

Fichier `sous-titres.glossaire.json` à côté du fichier de lot : [{"transcrit", "graphie", "francais"}], seulement les entrées
acceptées. `transcription.json` n'est jamais réécrit : la correction s'applique aux mots en mémoire, sur le mot exact.
"""
import json
import re
from pathlib import Path

NOM_FICHIER = "sous-titres.glossaire.json"
MAX_TERMES = 80
_MOT = re.compile(r"^(\W*)(.*?)((?:['’]s)?\W*)$", re.S)


def charger(chemin: str | Path | None) -> list[dict]:
    """Entrées du glossaire ; liste vide sans fichier ou s'il est illisible (le traitement continue sans correction)."""
    if not chemin:
        return []
    try:
        donnees = json.loads(Path(chemin).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [e for e in donnees if isinstance(e, dict) and e.get("transcrit")] if isinstance(donnees, list) else []


def chemin(fichier_lot: str | Path) -> Path:
    return Path(fichier_lot).with_name(NOM_FICHIER)


def empreinte(fichier: str | Path | None) -> float:
    """Date de modification du glossaire : un changement oblige à refaire la découpe avant de traduire."""
    try:
        return Path(fichier).stat().st_mtime if fichier else 0.0
    except OSError:
        return 0.0


def ajouter(fichier: str | Path, nouvelles: list[dict]):
    """Ajoute des entrées validées ; à nom transcrit égal, la dernière décision remplace la précédente."""
    fichier = Path(fichier)
    remplaces = {e["transcrit"] for e in nouvelles}
    entrees = [e for e in charger(fichier) if e["transcrit"] not in remplaces] + nouvelles
    tmp = fichier.with_name(fichier.name + ".tmp")
    tmp.write_text(json.dumps(sorted(entrees, key=lambda e: e["transcrit"].lower()), ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(fichier)


def _decouper(mot: str) -> tuple[str, str, str]:
    return _MOT.match(mot).groups()


def corriger_mots(mots: list[dict], entrees: list[dict]) -> tuple[list[dict], int]:
    """Remplace les suites de mots exactes du glossaire ; garde ponctuation, possessif et temps. Renvoie (mots, remplacements)."""
    corrections = {tuple(e["transcrit"].split()): e["graphie"].split() for e in entrees if e.get("graphie") and e["graphie"] != e["transcrit"]}
    if not corrections:
        return mots, 0
    longueurs = sorted({len(k) for k in corrections}, reverse=True)
    sortie, i, nb = [], 0, 0
    while i < len(mots):
        trouve = None
        for n in longueurs:
            morceau = mots[i:i + n]
            coeurs = tuple(_decouper(str(m.get("word", "")).strip())[1] for m in morceau)
            if len(morceau) == n and coeurs in corrections:
                trouve = (n, morceau, corrections[coeurs])
                break
        if trouve is None:
            sortie.append(mots[i])
            i += 1
            continue
        n, morceau, nouveaux = trouve
        avant = _decouper(str(morceau[0].get("word", "")).strip())[0]
        apres = _decouper(str(morceau[-1].get("word", "")).strip())[2]
        if len(nouveaux) == n:                 # autant de mots : chacun garde ses temps
            for j, (m, t) in enumerate(zip(morceau, nouveaux)):
                sortie.append({**m, "word": (avant if j == 0 else "") + t + (apres if j == n - 1 else "")})
        else:
            sortie.append({**morceau[0], "word": avant + " ".join(nouveaux) + apres, "end": morceau[-1]["end"]})
        i += n
        nb += 1
    return sortie, nb


def _present(terme: str, texte: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(terme)}(?!\w)", texte) is not None


def termes_pour(entrees: list[dict], textes: list[str], cible: str) -> list[str]:
    """Consignes de noms utiles à cette vidéo : forme française connue, sinon forme correcte à garder."""
    texte = " ".join(textes)
    lignes = []
    for e in entrees:
        source = e.get("graphie") or e["transcrit"]
        if not _present(source, texte):
            continue
        francais = e.get("francais") if cible == "fr" else None
        lignes.append(f"{source} → {francais}" if francais and francais != source else source)
        if len(lignes) >= MAX_TERMES:
            break
    return lignes


def remplacer_texte(texte: str, paires: list[tuple[str, str]]) -> tuple[str, int]:
    """Remplace des noms exacts dans un texte déjà produit (sous-titres ou traduction) ; les plus longs d'abord."""
    total = 0
    for avant, apres in sorted(paires, key=lambda p: -len(p[0])):
        texte, n = re.subn(rf"(?<!\w){re.escape(avant)}(?!\w)", lambda _m: apres, texte)
        total += n
    return texte, total
