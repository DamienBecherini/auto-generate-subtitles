"""Validated glossary of a batch: fixes badly transcribed names and guides their translation.

File `subtitles.glossary.json` next to the batch file: [{"transcribed", "spelling", "french"}], only the accepted
entries. `transcription.json` is never rewritten: the fix applies to the words in memory, on the exact word.
"""
import json
import re
from pathlib import Path

FILE_NAME = "subtitles.glossary.json"
MAX_TERMS = 80
_WORD = re.compile(r"^(\W*)(.*?)((?:['’]s)?\W*)$", re.S)


def load(path: str | Path | None) -> list[dict]:
    """Entries of the glossary; empty list without a file or if it is unreadable (the work goes on without fixes)."""
    if not path:
        return []
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [e for e in data if isinstance(e, dict) and e.get("transcribed")] if isinstance(data, list) else []


def path_for(batch_file: str | Path) -> Path:
    return Path(batch_file).with_name(FILE_NAME)


def fingerprint(file: str | Path | None) -> float:
    """Modification time of the glossary: a change forces the segmentation to be redone before translating."""
    try:
        return Path(file).stat().st_mtime if file else 0.0
    except OSError:
        return 0.0


def add(file: str | Path, new_entries: list[dict]):
    """Adds validated entries; for the same transcribed name, the last decision replaces the previous one."""
    file = Path(file)
    replaced = {e["transcribed"] for e in new_entries}
    entries = [e for e in load(file) if e["transcribed"] not in replaced] + new_entries
    tmp = file.with_name(file.name + ".tmp")
    tmp.write_text(json.dumps(sorted(entries, key=lambda e: e["transcribed"].lower()), ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(file)


def _split(word: str) -> tuple[str, str, str]:
    return _WORD.match(word).groups()


def fix_words(words: list[dict], entries: list[dict]) -> tuple[list[dict], int]:
    """Replaces the exact word sequences of the glossary; keeps punctuation, possessive and timings. Returns (words, replacements)."""
    fixes = {tuple(e["transcribed"].split()): e["spelling"].split() for e in entries if e.get("spelling") and e["spelling"] != e["transcribed"]}
    if not fixes:
        return words, 0
    lengths = sorted({len(k) for k in fixes}, reverse=True)
    out, i, count = [], 0, 0
    while i < len(words):
        found = None
        for n in lengths:
            piece = words[i:i + n]
            cores = tuple(_split(str(w.get("word", "")).strip())[1] for w in piece)
            if len(piece) == n and cores in fixes:
                found = (n, piece, fixes[cores])
                break
        if found is None:
            out.append(words[i])
            i += 1
            continue
        n, piece, new_words = found
        before = _split(str(piece[0].get("word", "")).strip())[0]
        after = _split(str(piece[-1].get("word", "")).strip())[2]
        if len(new_words) == n:                 # as many words: each one keeps its timings
            for j, (w, t) in enumerate(zip(piece, new_words)):
                out.append({**w, "word": (before if j == 0 else "") + t + (after if j == n - 1 else "")})
        else:
            out.append({**piece[0], "word": before + " ".join(new_words) + after, "end": piece[-1]["end"]})
        i += n
        count += 1
    return out, count


def _present(term: str, text: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text) is not None


def terms_for(entries: list[dict], texts: list[str], target: str) -> list[str]:
    """Name instructions useful for this video: known French form, otherwise correct form to keep."""
    text = " ".join(texts)
    lines = []
    for e in entries:
        src = e.get("spelling") or e["transcribed"]
        if not _present(src, text):
            continue
        french = e.get("french") if target == "fr" else None
        lines.append(f"{src} → {french}" if french and french != src else src)
        if len(lines) >= MAX_TERMS:
            break
    return lines


def replace_text(text: str, pairs: list[tuple[str, str]]) -> tuple[str, int]:
    """Replaces exact names in an already produced text (subtitles or translation); the longest first."""
    total = 0
    for before, after in sorted(pairs, key=lambda p: -len(p[0])):
        text, n = re.subn(rf"(?<!\w){re.escape(before)}(?!\w)", lambda _m, a=after: a, text)
        total += n
    return text, total
