"""Language of the transcription from its function words (the Parakeet Ultra model does not return it)."""
import re

FUNCTION_WORDS = {
    "fr": "le la les un une des et est que qui dans pour pas ce il elle nous vous sur avec mais plus très sont être au du",
    "en": "the and is are to of that in it you for with this on was have not but they we can will from be as",
    "de": "der die das und ist nicht ich sie es ein eine zu den mit auf für von wir auch dass im nach wie",
    "es": "el la los las y es que en un una por para con no se del al como más pero sus también muy",
    "it": "il la di che e è un una per non con sono gli le del della anche ma come più questo nel",
    "pt": "o a os as e é que de um uma para com não se do da em mais mas como também muito isso",
    "nl": "de het een en is van dat niet ik je op met voor zijn er maar ook aan bij wat dit",
}
_SETS = {k: set(v.split()) for k, v in FUNCTION_WORDS.items()}

# French names of the languages (used in the translation instructions, which are written in French); a missing code
# is designated by its code.
NAMES = {
    "fr": "français", "en": "anglais", "de": "allemand", "es": "espagnol", "it": "italien", "pt": "portugais",
    "nl": "néerlandais", "pl": "polonais", "ru": "russe", "uk": "ukrainien", "tr": "turc", "ar": "arabe", "ja": "japonais",
    "zh": "chinois", "ko": "coréen", "sv": "suédois", "da": "danois", "no": "norvégien", "fi": "finnois", "cs": "tchèque",
    "ro": "roumain", "hu": "hongrois", "el": "grec", "he": "hébreu", "hi": "hindi", "ca": "catalan", "eu": "basque",
    "br": "breton", "oc": "occitan", "eo": "espéranto",
}


def name(code: str) -> str:
    return NAMES.get(code, f"langue de code « {code} »")


def normalize_codes(value) -> list[str]:
    """Clean list of language codes, without duplicates, from a list or a string "fr, en"."""
    if isinstance(value, str):
        value = re.split(r"[,\s;]+", value)
    seen: list[str] = []
    for v in value or []:
        c = str(v).strip().lower()
        if c and c not in seen:
            seen.append(c)
    return seen


def detect(text: str) -> str:
    """Language code (fr, en…) or an empty string if nothing stands out."""
    words = re.findall(r"[^\W\d_]+", text.lower())
    if len(words) < 5:
        return ""
    scores = {k: sum(w in s for w in words) / len(words) for k, s in _SETS.items()}
    lang, score = max(scores.items(), key=lambda kv: kv[1])
    return lang if score >= 0.06 else ""
