"""Des mots horodatés aux sous-titres : phrases, découpe en blocs lisibles, durées d'affichage, retours à la ligne.

Règles habituelles des sous-titres : 2 lignes de 42 caractères au plus, 1 à 6 s à l'écran, coupe de
préférence après une ponctuation ou dans une pause, lignes équilibrées, pas de chevauchement.
"""
import re
from dataclasses import dataclass, field

FIN_DE_PHRASE = re.compile(r"[.!?…]+[\"»”’)\]]*$")
PONCTUATION_FAIBLE = re.compile(r"[,;:—–-]+[\"»”’)\]]*$")


@dataclass
class Mot:
    texte: str
    debut: float
    fin: float


@dataclass
class SousTitre:
    debut: float
    fin: float
    texte: str                     # une seule ligne (les retours à la ligne sont posés à l'écriture)
    mots: list[Mot] = field(default_factory=list)


@dataclass
class Reglages:
    caracteres_par_ligne: int = 42
    lignes: int = 2
    duree_max: float = 6.0
    duree_min: float = 1.0
    pause_coupe: float = 0.6
    ecart_min: float = 0.08
    lecture_cps: float = 17

    @property
    def caracteres(self) -> int:
        return self.caracteres_par_ligne * self.lignes


def depuis_json(words: list[dict]) -> list[Mot]:
    mots = []
    for w in words:
        t = str(w.get("word", "")).strip()
        if t:
            mots.append(Mot(t, float(w["start"]), float(w["end"])))
    return mots


def phrases(mots: list[Mot], r: Reglages) -> list[list[Mot]]:
    """Coupe après une fin de phrase ou devant une pause de `pause_coupe` secondes ou plus."""
    resultat, courante = [], []
    for i, m in enumerate(mots):
        courante.append(m)
        suivant = mots[i + 1] if i + 1 < len(mots) else None
        if suivant is None or FIN_DE_PHRASE.search(m.texte) or suivant.debut - m.fin >= r.pause_coupe:
            resultat.append(courante)
            courante = []
    return resultat


def _longueur(mots: list[Mot]) -> int:
    return sum(len(m.texte) for m in mots) + max(0, len(mots) - 1)


def _tient(mots: list[Mot], r: Reglages) -> bool:
    return _longueur(mots) <= r.caracteres and mots[-1].fin - mots[0].debut <= r.duree_max


def _score_coupe(mots: list[Mot], i: int) -> float:
    """Coupe entre mots[i] et mots[i+1] : pause longue, ponctuation, équilibre des deux parties."""
    gauche, droite = mots[: i + 1], mots[i + 1:]
    s = 2.0 * min(droite[0].debut - gauche[-1].fin, 1.0)
    if FIN_DE_PHRASE.search(gauche[-1].texte):
        s += 1.5
    elif PONCTUATION_FAIBLE.search(gauche[-1].texte):
        s += 1.0
    total = _longueur(mots) or 1
    s -= 1.5 * abs(_longueur(gauche) / total - 0.5)
    if len(gauche) == 1 or len(droite) == 1:
        s -= 1.0            # pas de mot orphelin
    return s


def decouper(mots: list[Mot], r: Reglages) -> list[list[Mot]]:
    """Découpe récursive d'une phrase trop longue (en caractères ou en secondes) au meilleur endroit."""
    if len(mots) <= 1 or _tient(mots, r):
        return [mots]
    i = max(range(len(mots) - 1), key=lambda k: _score_coupe(mots, k))
    return decouper(mots[: i + 1], r) + decouper(mots[i + 1:], r)


def ajuster_durees(st: list[SousTitre], r: Reglages) -> list[SousTitre]:
    """Chaque sous-titre reste assez longtemps pour être lu, sans empiéter sur le suivant."""
    for i, s in enumerate(st):
        if i and s.debut < st[i - 1].fin + r.ecart_min:
            s.debut = round(st[i - 1].fin + r.ecart_min, 3)      # parole très rapide : le suivant est repoussé
        souhaite = max(r.duree_min, len(s.texte) / r.lecture_cps)
        limite = st[i + 1].debut - r.ecart_min if i + 1 < len(st) else float("inf")
        fin = min(max(s.fin, s.debut + souhaite), limite)
        s.fin = round(max(fin, s.debut + 0.3), 3)                 # 0,3 s au moins, quitte à repousser le suivant
    return st


def construire(mots: list[Mot], r: Reglages | None = None) -> list[SousTitre]:
    r = r or Reglages()
    st = []
    for p in phrases(mots, r):
        for bloc in decouper(p, r):
            st.append(SousTitre(round(bloc[0].debut, 3), round(bloc[-1].fin, 3), " ".join(m.texte for m in bloc), bloc))
    return ajuster_durees(st, r)


def couper_lignes(texte: str, r: Reglages) -> list[str]:
    """Une ou deux lignes équilibrées, coupées après une ponctuation quand c'est possible."""
    texte = " ".join(texte.split())
    if len(texte) <= r.caracteres_par_ligne or r.lignes < 2 or " " not in texte:
        return [texte]
    return list(couper_en_deux(texte, r.caracteres_par_ligne))


def couper_en_deux(texte: str, largeur: int) -> tuple[str, str]:
    """Meilleure coupe à une espace : deux parties de `largeur` au plus si possible, équilibrées, après une ponctuation."""
    meilleur, score_max = None, float("-inf")
    for m in re.finditer(r" ", texte):
        gauche, droite = texte[: m.start()], texte[m.end():]
        if len(gauche) > largeur or len(droite) > largeur:
            penalite = -5.0 - (max(len(gauche), len(droite)) - largeur)
        else:
            penalite = 0.0
        s = penalite - abs(len(gauche) - len(droite)) / 10
        if FIN_DE_PHRASE.search(gauche):
            s += 2.0
        elif PONCTUATION_FAIBLE.search(gauche):
            s += 1.5
        if len(droite.split()) == 1 and len(droite) < 6:
            s -= 1.0        # pas un petit mot seul sur la seconde ligne
        if s > score_max:
            meilleur, score_max = (gauche, droite), s
    return meilleur


def scinder_trop_longs(st: list[SousTitre], r: Reglages) -> list[SousTitre]:
    """Après traduction, un texte qui ne tient plus sur deux lignes est partagé en deux sous-titres,
    le temps réparti au prorata des caractères (les mots d'origine ne correspondent plus)."""
    resultat = []
    for s in st:
        texte = " ".join(s.texte.split())
        if len(texte) <= r.caracteres or " " not in texte:
            resultat.append(SousTitre(s.debut, s.fin, texte))
            continue
        g, d = couper_en_deux(texte, r.caracteres)
        part = len(g) / max(len(texte), 1)
        milieu = round(s.debut + (s.fin - s.debut) * part, 3)
        resultat.extend(scinder_trop_longs([SousTitre(s.debut, max(milieu - r.ecart_min / 2, s.debut + 0.3), g),
                                            SousTitre(min(milieu + r.ecart_min / 2, s.fin - 0.3), s.fin, d)], r))
    return resultat
