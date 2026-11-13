"""A whole folder from the command line: the same batch as in the web interface (`subtitles.batch.json`, resumed
after an interruption), run by the same task manager, without a server and without a pause before translation.

    python -m subtitles <folder | .batch.json file> [options]

If the folder already has a batch file (in the folder itself, or in the output folder when the "output folder"
destination is ticked), the command resumes it: finished videos are skipped, the others start again from the kept
transcription. `--new-batch` starts again from an inventory of the folder.
"""
import time
from pathlib import Path

from . import folder, i18n, language, source

WAIT_S = 1.0


def is_batch(path: str) -> bool:
    p = Path(path)
    return p.is_dir() or (p.is_file() and p.name.endswith(".batch.json"))


def find_batch_file(path: Path, cfg: dict, output_dir: str | None) -> Path | None:
    """Existing batch file for this folder (or this file itself), None if there is none."""
    if path.is_file():
        return path
    candidates = [path / folder.BATCH_FILE_NAME, Path(output_dir or cfg["output_dir"]) / folder.BATCH_FILE_NAME]
    for f in candidates:
        if not f.is_file():
            continue
        try:
            root = folder.read_batch(f).get("root", "")
        except source.SourceError:
            continue
        if root and Path(root).resolve() == path.resolve():      # the output folder may serve another batch
            return f
    return None


def run(path: str, cfg: dict, log, *, transcription: str | None = None, translator: str | None = None,
        spoken_language: str | None = None, languages: list[str] | None = None, output_dir: str | None = None, redo: bool = False,
        stop: str | None = None, new: bool = False, recursive: bool = True, manager=None) -> int:
    """Creates or resumes the batch, follows it to the end while copying the log of the videos. 0 if all is done, 1 otherwise."""
    from .web.tasks import Manager        # no import of the web package for a single video

    g = manager or Manager()
    p = Path(path.strip().strip('"'))
    file = None if new and p.is_dir() else find_batch_file(p, cfg, output_dir)
    if file:
        batch = g.load_batch(str(file))
        changes = {"translator": translator, "transcription": transcription, "pause": False, "stop": stop}
        if languages is not None:
            changes["languages"] = languages
        g.batch_resume(batch.id, changes)
        done = sum(1 for v in batch.videos if v["state"] == "done")
        log(i18n.t("log.cli_batch_resume", file=batch.file, done=done, n=len(batch.videos)))
    else:
        if not p.is_dir():
            raise source.SourceError(i18n.t("err.folder_not_found", path=p))
        videos = folder.scan(p, recursive)
        if not videos:
            raise source.SourceError(i18n.t("err.no_videos_in", path=p))
        options = {"transcription": transcription or "", "translator": translator or "", "pause": False, "redo": redo}
        if languages is not None:
            options["languages"] = language.normalize_codes(languages)
        if output_dir:
            options["output_dir"] = output_dir
        if stop:
            options["stop"] = stop
        batch = g.create_batch(str(p).replace("\\", "/"), [{"path": v["path"], "language": spoken_language or ""} for v in videos], options)
        log(i18n.t("log.cli_batch_new", n=len(videos), file=batch.file))
    try:
        return follow(g, batch, log)
    except KeyboardInterrupt:
        g.batch_suspend(batch.id)
        log(i18n.t("log.cli_interrupted_batch", file=batch.file))
        return 130


def follow(g, batch, log, wait_s: float = WAIT_S) -> int:
    read: dict[str, int] = {}                   # task → log lines already displayed
    announced: set[str] = set()
    while True:
        if batch.state == "awaiting_translation":   # should not happen without a pause; translate at once
            g.batch_translate(batch.id, None, None)
        finished = batch.state in ("done", "cancelled")    # read before the log: the last lines are displayed
        several = batch.concurrency > 1
        for v in list(batch.videos):
            t = g.tasks.get(v.get("task") or "")
            if t is None or t.state == "queued":
                continue
            if t.id not in announced:
                announced.add(t.id)
                rank = batch.videos.index(v) + 1
                log(f"── {rank}/{len(batch.videos)} : {v['name']} ──")
            start = read.get(t.id, 0)
            for line in t.log[start:]:
                print(("  " + v["name"] + " " if several else "  ") + line, flush=True)
            read[t.id] = len(t.log)
        if finished:
            break
        time.sleep(wait_s)
    c = {e: sum(1 for v in batch.videos if v["state"] == e) for e in ("done", "error", "cancelled")}
    log(i18n.t("log.cli_batch_finished" if batch.state == "done" else "log.cli_batch_cancelled", done=c["done"], errors=c["error"], cancelled=c["cancelled"]))
    for v in batch.videos:
        if v["state"] == "error":
            log(f"  {v['name']} : {v.get('error') or i18n.t('state.error')}")
    return 0 if batch.state == "done" and not c["error"] else 1
