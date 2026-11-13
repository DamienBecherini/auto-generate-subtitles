"""The whole chain, shared by the command line and the web interface.

Output folder `output_dir`: the downloaded (YouTube) or copied (drag and drop) video; the intermediate files (extracted
audio, transcription.json, subtitles, translation.<language>.json, .vtt) in `output_dir/.work/<name>/`.
Files produced: `<name>.<original language>.srt` and one `<name>.<target>.srt` per target language, copied next to
the video and/or into the output folder (`[destination]` table), then cleanup of the working folder (`[cleanup]`).

Two phases: `prepare` (source, audio, transcription, original subtitles) then `translate_and_copy`
(translations, copies, cleanup); `run` chains both.
"""
import hashlib
import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import cleanup, config, dubbing, formats, glossary, i18n, language, local_transcription, muxing, segmentation, source, steps, transcription, translation
from .segmentation import Cue, Settings

STEPS = ["source", "transcription", "segmentation", "review", "translation", "files", "dubbing", "mux"]
DEFAULT_DESTINATION = {"next_to_video": True, "output_dir": False}
FINGERPRINTS_FILE = "placed-subtitles.json"   # in the working folder: fingerprint of each .srt as it was placed


class Cancelled(Exception):
    pass


@dataclass
class Request:
    source: str                        # URL or path
    cfg: dict
    transcription_id: str | None = None
    translator_id: str | None = None
    language: str | None = None        # spoken language; None = setting, "" = detection
    output_dir: str | None = None
    redo: bool = False
    on_work: object = None             # callback called with the .work/<name> folder as soon as it is known (log)
    languages: list[str] | None = None   # target languages; None = cfg["target_languages"]
    destination: dict | None = None    # {"next_to_video": bool, "output_dir": bool}; None = cfg["destination"]
    cleanup: bool = True               # applies [cleanup] to the working folder at the end
    on_duration: object = None         # callback called with the audio duration before transcribing it (time left)
    glossary: str | None = None        # file of the validated names (see glossary.py)
    on_progress: object = None         # callback (done, total) at each chunk of translated subtitles


@dataclass
class OutputFile:
    language: str
    path: Path                         # main location (first copy, otherwise the working folder)
    count: int
    translated: bool
    copies: list[Path] = field(default_factory=list)


@dataclass
class Prepared:
    """Result of the first phase (everything that does not depend on the translator)."""
    video: Path
    work: Path
    base: str
    code: str                          # spoken language
    cues: list[Cue]
    source_srt: Path
    output_dir: Path
    start: float


@dataclass
class Result:
    video: Path
    srt: Path                          # main file: first target language, or original language
    source_srt: Path
    language: str
    cue_count: int
    translated: bool
    copies: list[Path] = field(default_factory=list)
    duration_s: float = 0.0
    files: list[OutputFile] = field(default_factory=list)


def target_languages(r: Request) -> list[str]:
    return language.normalize_codes(r.languages if r.languages is not None else r.cfg.get("target_languages", ["fr"]))


def destination(r: Request) -> dict:
    return {**DEFAULT_DESTINATION, **(r.cfg.get("destination") or {}), **(r.destination or {})}


def duration_text(seconds: float) -> str:
    s = round(seconds)
    h, m, s = s // 3600, s % 3600 // 60, s % 60
    return f"{h} h {m:02d} min" if h else f"{m} min {s:02d} s" if m else f"{s} s"


def transcription_timeout(m: dict, duration_s: float) -> int:
    """Longest wait for the answer: 4 times the expected time, at least 5 min; 2 h if the speed is not given."""
    try:
        speed = float(m.get("speed") or 0)
    except (TypeError, ValueError):
        speed = 0
    return max(300, round(4 * duration_s / speed)) if speed > 0 else 7200


def transcription_message(m: dict, duration_s: float) -> str:
    """Expected time from the speed of the chosen server (`speed` field) and the audio duration."""
    audio = duration_text(duration_s)
    try:
        speed = float(m.get("speed") or 0)
    except (TypeError, ValueError):
        speed = 0
    if speed <= 0:
        return i18n.t("log.transcribing_unknown_speed", audio=audio, name=config.engine_name(m))
    return i18n.t("log.transcribing", eta=duration_text(duration_s / speed), audio=audio, name=config.engine_name(m), speed=f"{speed:g}")


def translation_file(work: Path, target: str) -> Path:
    return work / f"translation.{target}.json"


def remaining_languages(p: Prepared, languages: list[str], redo: bool = False) -> list[str]:
    """Languages really to translate: neither the spoken language, nor those whose translation of this segmentation is kept."""
    def done(target: str) -> bool:
        try:
            return len(json.loads(translation_file(p.work, target).read_text(encoding="utf-8"))) == len(p.cues)
        except (OSError, ValueError, TypeError):
            return False
    return [c for c in languages if c != p.code and (redo or not done(c))]


def work_dir(output_dir: str | Path, video: str | Path) -> Path:
    return Path(output_dir) / ".work" / source.slug(Path(video).stem)


def transcription_kept(output_dir: str | Path, video: str | Path) -> bool:
    """Transcription already done: `prepare` will take it back in a few seconds, without a transcription server."""
    return (work_dir(output_dir, video) / "transcription.json").is_file()


def run(r: Request, log=print, step=lambda name: None, cancelled=lambda: False) -> Result:
    p = prepare(r, log, step, cancelled)
    return translate_and_copy(r, p, log, step, cancelled)


def prepare(r: Request, log=print, step=lambda name: None, cancelled=lambda: False) -> Prepared:
    cfg = r.cfg
    settings = Settings(**cfg.get("subtitles", {}))
    output_dir = Path(r.output_dir or cfg["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    m_tr = config.engine(cfg, "transcriptions", "active_transcription", r.transcription_id)
    wanted_language = (r.language if r.language is not None else cfg.get("language", "")) or None
    t_start = time.perf_counter()

    def check():
        if cancelled():
            raise Cancelled()

    # 1. Source
    step("source")
    if source.is_url(r.source):
        video = source.download_youtube(r.source, cfg.get("youtube", {}).get("command", ["yt-dlp"]), log, folder=output_dir)
    else:
        video = Path(r.source).expanduser().resolve()
        if not video.is_file():
            raise source.SourceError(i18n.t("err.file_not_found", path=video))
    base = source.slug(video.stem)
    work = work_dir(output_dir, video)
    work.mkdir(parents=True, exist_ok=True)
    if r.on_work:
        r.on_work(work)
    tr_file = work / "transcription.json"
    audios = [p for p in work.glob(f"{base}.*") if source.is_extracted_audio(p, base)]
    if tr_file.exists() and not r.redo and not audios:
        audio = None                                   # transcription kept, audio cleaned: nothing to extract
    elif audios:
        audio = audios[0]
        log(i18n.t("log.audio_already", file=audio.name))
    else:
        log(i18n.t("log.audio_extracting", file=video.name))
        audio = source.extract_audio(video, work / f"{base}.audio")
    if audio:
        log(i18n.t("log.audio", file=audio.name, mb=f"{audio.stat().st_size / 1e6:.1f}", s=f"{source.duration(audio):.0f}", work=work))
    check()

    # 2. Transcription
    step("transcription")
    if tr_file.exists() and not r.redo:
        tr = json.loads(tr_file.read_text(encoding="utf-8"))
        log(i18n.t("log.transcription_already", n=len(tr["words"])))
    else:
        if m_tr.get("command"):
            local_transcription.LocalServer.for_engine(m_tr).ensure(log)
        log(i18n.t("log.transcription_by", name=config.engine_name(m_tr), url=m_tr["url"], state=transcription.health(m_tr["url"])))
        audio_duration = source.duration(audio)
        if r.on_duration:
            r.on_duration(audio_duration)
        log(transcription_message(m_tr, audio_duration))
        t = time.perf_counter()
        key = config.resolve_key(m_tr.get("key", ""))
        tr_language = wanted_language if wanted_language != "fr" else None
        timeout = transcription_timeout(m_tr, audio_duration)
        try:
            tr = transcription.transcribe(m_tr["url"], audio, tr_language, timeout_s=timeout, key=key)
        except transcription.TranscriptionTimeout as e:
            log(i18n.t("log.retry", error=e))      # answer lost on the way (already seen): a batch must not stay stuck
            tr = transcription.transcribe(m_tr["url"], audio, tr_language, timeout_s=timeout, key=key)
        tr_file.write_text(json.dumps(tr, ensure_ascii=False, indent=1), encoding="utf-8")
        log(i18n.t("log.transcribed", s=round(time.perf_counter() - t), words=len(tr["words"]), segments=len(tr["segments"])))
    if not tr["words"]:
        raise transcription.TranscriptionError(i18n.t("err.no_speech"))
    check()

    # 3. Segmentation in the original language
    step("segmentation")
    code = wanted_language or tr.get("language") or language.detect(tr.get("text", "")) or "xx"
    words, fixes = glossary.fix_words(tr["words"], glossary.load(r.glossary))
    if fixes:
        log(i18n.t("log.glossary_fixed", n=fixes))
    cues = segmentation.build(segmentation.from_json(words), settings)
    source_srt = work / f"{base}.{code}.srt"
    source_srt.write_text(formats.srt(cues, settings), encoding="utf-8")
    log(i18n.t("log.source_subtitles", code=code, lang=i18n.language_name(code), n=len(cues), file=source_srt.name))
    return Prepared(video, work, base, code, cues, source_srt, output_dir, t_start)


def _fingerprint(f: Path) -> str:
    return hashlib.sha256(f.read_bytes()).hexdigest()


def place_srt(source_srt: Path, target: Path, fingerprints: dict, redo: bool = False, log=print):
    """Copies a .srt to its destination, unless the file there was modified since it was created (fixed by hand).
    A different file without a known fingerprint is kept too: when in doubt, nothing is overwritten. "Redo" replaces everything."""
    new = _fingerprint(source_srt)
    if target.exists() and not redo:
        current = _fingerprint(target)
        if current == new:
            fingerprints[str(target)] = new
            return
        if current != fingerprints.get(str(target)):
            log(i18n.t("log.subtitles_kept", file=target.name))
            return
    shutil.copyfile(source_srt, target)
    fingerprints[str(target)] = new


def translate_and_copy(r: Request, p: Prepared, log=print, step=lambda name: None, cancelled=lambda: False) -> Result:
    cfg = r.cfg
    settings = Settings(**cfg.get("subtitles", {}))
    m_trans = config.engine(cfg, "translators", "active_translator", r.translator_id)
    targets = [c for c in target_languages(r) if c != p.code]
    files = [OutputFile(p.code, p.source_srt, len(p.cues), False)]

    # 4. Translations, one per target language
    step("translation")
    if m_trans.get("type", "none") == "none" or not targets:
        log(i18n.t("log.no_translation"))
    else:
        texts = [s.text for s in p.cues]
        translator = None
        try:
            for target in targets:
                trans_file = translation_file(p.work, target)
                translated = None
                if trans_file.exists() and not r.redo:
                    translated = json.loads(trans_file.read_text(encoding="utf-8"))
                    if len(translated) != len(texts):
                        translated = None
                        log(i18n.t("log.translation_other_split", lang=target))
                    else:
                        log(i18n.t("log.translation_already", lang=target))
                if translated is None:
                    if translator is None:
                        translator = translation.create(m_trans, log)
                    log(i18n.t("log.translating", src=p.code, lang=target, name=i18n.language_name(target), translator=config.engine_name(m_trans), n=len(texts)))
                    t = time.perf_counter()
                    terms = glossary.terms_for(glossary.load(r.glossary), texts, target)
                    if terms:
                        log(i18n.t("log.glossary_terms", n=len(terms)))
                    translated = translation.translate_texts(translator, texts, int(cfg.get("translation", {}).get("chunk_size", 25)),
                                                             log, cancelled=cancelled, target=target, terms=terms, progress=r.on_progress)
                    trans_file.write_text(json.dumps(translated, ensure_ascii=False, indent=1), encoding="utf-8")
                    log(i18n.t("log.translated", s=round(time.perf_counter() - t)))
                final = segmentation.split_too_long([Cue(s.start, s.end, t) for s, t in zip(p.cues, translated)], settings)
                final = segmentation.adjust_durations(final, settings)
                srt = p.work / f"{p.base}.{target}.srt"
                srt.write_text(formats.srt(final, settings), encoding="utf-8")
                (p.work / f"{p.base}.{target}.vtt").write_text(formats.vtt(final, settings), encoding="utf-8")
                files.append(OutputFile(target, srt, len(final), True))
                log(i18n.t("log.target_subtitles", n=len(final), lang=i18n.language_name(target), file=srt.name))
        finally:
            if translator is not None:
                translator.finish()

    # 5. Copies next to the video and/or into the output folder, named after the video + language
    step("files")
    dest = destination(r)
    folders = []
    if dest.get("next_to_video"):
        folders.append(p.video.parent)
    if dest.get("output_dir") and p.output_dir.resolve() not in {x.resolve() for x in folders}:
        folders.append(p.output_dir)
    if not folders:
        log(i18n.t("log.no_destination"))
    all_copies = []
    fingerprints_file = p.work / FINGERPRINTS_FILE
    try:
        fingerprints = json.loads(fingerprints_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        fingerprints = {}
    for f in files:
        for folder in folders:
            target = folder / f"{p.video.stem}.{f.language}.srt"
            if target.resolve() != f.path.resolve():
                place_srt(f.path, target, fingerprints, r.redo, log)
                f.copies.append(target)
        if f.copies:
            f.path = f.copies[0]
        all_copies.extend(f.copies)
    if fingerprints:
        fingerprints_file.write_text(json.dumps(fingerprints, ensure_ascii=False, indent=1), encoding="utf-8")
    for c in all_copies:
        log(i18n.t("log.subtitles_file", path=c))

    # 6. Cleanup of the working folder
    if r.cleanup:
        opts = cleanup.options(cfg)
        if not all_copies and opts.get("subtitles"):
            opts = {**opts, "subtitles": False}       # only copy of the results: keep it
        cleanup.clean_folder(p.work, opts, log)

    main = next((f for f in files if f.translated), files[0])
    duration = time.perf_counter() - p.start
    log((i18n.t("log.subtitles_ready") if duration < 5 else i18n.t("log.subtitles_done", s=round(duration))) + " " + i18n.t("log.video", path=p.video))
    return Result(p.video, main.path, p.source_srt, p.code, main.count, main.translated, all_copies, duration, files)


def dub_and_mux(r: Request, p: Prepared, res: Result, until: str, log=print, step=lambda name: None, cancelled=lambda: False, replace: bool = False) -> dict:
    """After the translation: dubbed track in the first translated language, then multi-track MKV, which can replace the video. Returns the paths produced."""
    if steps.integration_mark(p.work).is_file() and not r.redo:
        log(i18n.t("log.dub_integrated"))
        return {}
    target = next((f for f in res.files if f.translated), None)
    if target is None:
        log(i18n.t("log.dub_no_translation"))
        return {}
    with_mkv = steps.goes_to(until, "mux")
    mkv = steps.mkv_path(res.video)
    added = 1 + len(res.files)
    if with_mkv and mkv.exists() and not r.redo:
        log(i18n.t("log.mkv_already", file=mkv.name))
        return _integrate(p, res.video, mkv, added, log) if replace else {"mkv": str(mkv)}
    if cancelled():
        raise Cancelled()
    step("dubbing")
    try:
        audio = dubbing.dub(r.cfg, res.video, p.work, res.files[0].path, target.path, target.language, log, cancelled, r.redo)
    except dubbing.DubbingError:
        if cancelled():
            raise Cancelled() from None
        raise
    if not with_mkv:
        return {"dubbing": str(audio)}
    step("mux")
    mkv = muxing.mux(res.video, audio, target.language, res.language, [(f.language, f.path) for f in res.files], log, r.redo)
    return {"dubbing": str(audio), **(_integrate(p, res.video, mkv, added, log) if replace else {"mkv": str(mkv)})}


def _integrate(p: Prepared, video: Path, mkv: Path, added: int, log) -> dict:
    final = muxing.replace_original(video, mkv, added, log)
    if final == mkv:
        return {"mkv": str(mkv)}
    mark = steps.integration_mark(p.work)
    mark.parent.mkdir(parents=True, exist_ok=True)
    mark.write_text(str(final), encoding="utf-8")
    return {"mkv": str(final), "replaced": True}
