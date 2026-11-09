"""Langue de la transcription par les mots-outils (le modèle Parakeet Ultra ne la renvoie pas)."""
import re

MOTS_OUTILS = {
    "fr": "le la les un une des et est que qui dans pour pas ce il elle nous vous sur avec mais plus très sont être au du",
    "en": "the and is are to of that in it you for with this on was have not but they we can will from be as",
    "de": "der die das und ist nicht ich sie es ein eine zu den mit auf für von wir auch dass im nach wie",
    "es": "el la los las y es que en un una por para con no se del al como más pero sus también muy",
    "it": "il la di che e è un una per non con sono gli le del della anche ma come più questo nel",
    "pt": "o a os as e é que de um uma para com não se do da em mais mas como também muito isso",
    "nl": "de het een en is van dat niet ik je op met voor zijn er maar ook aan bij wat dit",
}
_ENSEMBLES = {k: set(v.split()) for k, v in MOTS_OUTILS.items()}

# Noms français des langues (consignes de traduction, interface) ; un code absent est désigné par son code.
NOMS = {
    "fr": "français", "en": "anglais", "de": "allemand", "es": "espagnol", "it": "italien", "pt": "portugais",
    "nl": "néerlandais", "pl": "polonais", "ru": "russe", "uk": "ukrainien", "tr": "turc", "ar": "arabe", "ja": "japonais",
    "zh": "chinois", "ko": "coréen", "sv": "suédois", "da": "danois", "no": "norvégien", "fi": "finnois", "cs": "tchèque",
    "ro": "roumain", "hu": "hongrois", "el": "grec", "he": "hébreu", "hi": "hindi", "ca": "catalan", "eu": "basque",
    "br": "breton", "oc": "occitan", "eo": "espéranto",
}


def nom(code: str) -> str:
    return NOMS.get(code, f"langue de code « {code} »")


def normaliser_codes(valeur) -> list[str]:
    """Liste de codes de langue propres, sans doublon, à partir d'une liste ou d'une chaîne « fr, en »."""
    if isinstance(valeur, str):
        valeur = re.split(r"[,\s;]+", valeur)
    vus: list[str] = []
    for v in valeur or []:
        c = str(v).strip().lower()
        if c and c not in vus:
            vus.append(c)
    return vus


def detecter(texte: str) -> str:
    """Code de langue (fr, en…) ou chaîne vide si rien ne ressort."""
    mots = re.findall(r"[^\W\d_]+", texte.lower())
    if len(mots) < 5:
        return ""
    scores = {k: sum(m in e for m in mots) / len(mots) for k, e in _ENSEMBLES.items()}
    langue, score = max(scores.items(), key=lambda kv: kv[1])
    return langue if score >= 0.06 else ""
