"""AI review of a transcription: spots the distorted proper names and proposes their fix, without changing anything.

Batch file `subtitles.review.json`, next to the batch file: {"examined": [names already submitted], "proposals": [...]}.
A name is submitted only once per batch, even if it appears in 200 videos. In the working folder of each video,
`review.json` lists the names of this video that were waiting for a decision. A video is validated when none of its
names is waiting any more: the accepted names join the glossary (see glossary.py), the others stay in "examined".
The instructions sent to the model are written in French, and so are its reasons.
"""
import collections
import json
import re
import threading
from pathlib import Path

from . import glossary, i18n, translation

FILE_NAME = "subtitles.review.json"
VIDEO_FILE = "review.json"
PACKET_SIZE = 70
_lock = threading.RLock()

_WORD = r"[A-ZÀ-Ý][a-zà-ÿ]+(?:[-'’][A-Za-zà-ÿ]+)*"
_SEQUENCE = re.compile(rf"{_WORD}(?:\s+(?:de |van |von |der |al-)?{_WORD})*")

INSTRUCTIONS = """Tu relis une transcription automatique (reconnaissance vocale). La reconnaissance vocale déforme souvent les noms rares selon leur son.

On te donne le titre de la vidéo, puis des noms propres extraits, avec nombre d'occurrences et contexte.
Ne renvoie QUE les noms utiles, parmi trois cas ; omets tous les autres (noms justes sans forme française différente, mots ordinaires) :
- « to_fix » : le nom est mal transcrit et tu reconnais la vraie personne, le lieu, l'œuvre ou la notion d'après le son et le contexte ;
- « uncertain » : le nom semble déformé mais tu ne sais pas établir le bon ;
- « french_form » : le nom est juste, s'écrit autrement en français, et un traducteur pourrait se tromper (Plato → Platon, titre d'un livre traduit).
  Omets les pays, grandes villes, religions, peuples et personnages que tout traducteur connaît (China, Christianity, Jesus).

Ne devine jamais : si tu ne reconnais pas avec certitude ce dont il s'agit, utilise « uncertain » et laisse « spelling » vide.
Pas de correction pour un simple doute sur l'orthographe d'un nom inconnu, ni pour un nom dont le contexte ne permet pas d'identifier la référence.
Pour chaque entrée : « index » = numéro du nom, « spelling » = forme correcte dans la langue de la vidéo, « french » = forme usuelle en français,
« confidence », « reason » en une phrase courte en français."""

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["entries"], "properties": {"entries": {"type": "array", "items": {
    "type": "object", "additionalProperties": False, "required": ["index", "status", "spelling", "french", "confidence", "reason"],
    "properties": {"index": {"type": "integer"}, "status": {"type": "string", "enum": ["to_fix", "uncertain", "french_form"]},
                   "spelling": {"type": "string"}, "french": {"type": "string"},
                   "confidence": {"type": "string", "enum": ["high", "medium", "low"]}, "reason": {"type": "string"}}}}}}


def path_for(batch_file: str | Path) -> Path:
    return Path(batch_file).with_name(FILE_NAME)


def engine(cfg: dict, translator_id: str | None = None) -> dict | None:
    """The Claude translator chosen for the batch, else the first configured one; None if there is none (no review)."""
    claudes = [m for m in cfg.get("translators", []) if m.get("type") == "claude"]
    return next((m for m in claudes if m.get("id") == translator_id), claudes[0] if claudes else None)


def _read(f: Path) -> dict:
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"examined": [], "proposals": []}
    return {"examined": list(d.get("examined") or []), "proposals": list(d.get("proposals") or [])}


def _write(f: Path, d: dict):
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(f)


def pending_names(batch_file: str | Path) -> set[str]:
    return {p["transcribed"] for p in _read(path_for(batch_file))["proposals"]}


def _without_possessive(n: str) -> str:
    return re.sub(r"['’]s$", "", n)


def candidates(text: str) -> list[dict]:
    """Proper names of a text: [{name, occurrences, examples}]. A single word mostly written in lower case is a common word."""
    lower = collections.Counter(m.lower() for m in re.findall(r"\b[a-zà-ÿ]{2,}\b", text))
    seen, examples = collections.Counter(), collections.defaultdict(list)
    for sentence in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text)):
        for m in _SEQUENCE.finditer(sentence):
            c = _without_possessive(m.group(0))
            at_start = m.start() == 0
            words = c.split()
            if len(words) == 1 and (len(c) < 4 or lower[c.lower()] >= 3 or (at_start and lower[c.lower()] >= 1)):
                continue
            if at_start and len(words) > 1 and lower[words[0].lower()] >= 1:      # "The Gariaev" at the start of a sentence
                c = " ".join(words[1:])
                if len(c) < 4:
                    continue
            seen[c] += 1
            short = sentence.strip()[:170]
            if len(examples[c]) < 2 and short not in examples[c]:
                examples[c].append(short)
    return [{"name": c, "occurrences": seen[c], "examples": examples[c]} for c in sorted(seen, key=str.lower)]


def _submit(translator, title: str, packet: list[dict]) -> list[dict]:
    lines = [f"{i}. {c['name']} ({c['occurrences']} fois) : " + " / ".join(c["examples"]) for i, c in enumerate(packet)]
    raw = translator.complete(INSTRUCTIONS, f"Titre : {title}\n\n" + "\n".join(lines), schema=SCHEMA, effort="medium")
    r = []
    for e in json.loads(raw)["entries"]:
        if 0 <= e["index"] < len(packet):
            c = packet[e["index"]]
            spelling = c["name"] if e["status"] == "french_form" else e["spelling"]    # the name is right: do not rewrite it (cut title, etc.)
            r.append({"transcribed": c["name"], "status": e["status"], "spelling": spelling, "french": e["french"], "confidence": e["confidence"],
                      "reason": e["reason"], "occurrences": c["occurrences"], "examples": c["examples"]})
    return r


def review(m: dict, batch_file: str | Path, work: Path, title: str, texts: list[str], log=print, cancelled=lambda: False, create=translation.create) -> list[str]:
    """Writes the proposals of this video; returns its names waiting for a decision. An API failure leaves the review to redo."""
    f = path_for(batch_file)
    every = candidates(" ".join(texts))
    with _lock:
        d = _read(f)
    entries = glossary.load(glossary.path_for(batch_file))
    known = set(d["examined"]) | {p["transcribed"] for p in d["proposals"]} | {e["transcribed"] for e in entries} | {e.get("spelling") for e in entries} | {e.get("french") for e in entries}
    to_submit = [c for c in every if c["name"] not in known]
    log(i18n.t("log.review_names", n=len(every), new=len(to_submit)))
    if to_submit:
        translator = create(m, log)
        new = []
        try:
            for i in range(0, len(to_submit), PACKET_SIZE):
                if cancelled():
                    raise translation.TranslationError(i18n.t("err.cancelled"))
                new += _submit(translator, title, to_submit[i:i + PACKET_SIZE])
        finally:
            translator.finish()
        with _lock:                            # read again: a validation may have arrived during the call
            d = _read(f)
            d["examined"] = sorted(set(d["examined"]) | {c["name"] for c in to_submit})
            already = {p["transcribed"] for p in d["proposals"]}
            d["proposals"] += [p for p in new if p["transcribed"] not in already]
            _write(f, d)
        log(i18n.t("log.review_to_check", n=len(new)))
    with _lock:
        pending = pending_names(batch_file)
    mine = sorted({c["name"] for c in every} & pending)
    _write(work / VIDEO_FILE, {"names": mine})
    return mine


def proposals(batch_file: str | Path, names: set[str] | None = None) -> list[dict]:
    """Pending proposals, possibly limited to these names; the most frequent first."""
    with _lock:
        items = _read(path_for(batch_file))["proposals"]
    return sorted((p for p in items if names is None or p["transcribed"] in names), key=lambda p: (-p.get("occurrences", 0), p["transcribed"].lower()))


def validate(batch_file: str | Path, decisions: list[dict]) -> tuple[int, int]:
    """Applies the decisions {transcribed, spelling, french, accept}. Returns (accepted, rejected).
    A name accepted without a different spelling and without a French form brings nothing: it is rejected."""
    with _lock:
        f = path_for(batch_file)
        d = _read(f)
        pending = {p["transcribed"]: p for p in d["proposals"]}
        accepted, rejected, new = 0, 0, []
        for x in decisions:
            p = pending.pop(x.get("transcribed"), None)
            if p is None:
                continue
            spelling, french = str(x.get("spelling") or "").strip(), str(x.get("french") or "").strip()
            if x.get("accept") and spelling and (spelling != p["transcribed"] or french):
                new.append({"transcribed": p["transcribed"], "spelling": spelling, "french": french, "status": p.get("status", "to_fix")})
                accepted += 1
            else:
                rejected += 1
        if new:
            glossary.add(glossary.path_for(batch_file), new)
        d["proposals"] = [p for p in d["proposals"] if p["transcribed"] in pending]
        _write(f, d)
        return accepted, rejected
