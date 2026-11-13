"""Subtitles (spoken language + target languages) for a YouTube video, a video file or every video of a folder,
from the command line.

    python -m subtitles <YouTube URL | video file | folder | .batch.json file> [options]

A folder becomes a batch, as in the web interface: a subtitles.batch.json file is written as the work goes, and
running the same command again after an interruption resumes where the batch stopped.

Web interface: python -m subtitles.web (see README). Settings come from config.toml overridden by
%LOCALAPPDATA%/subtitles/config.json (written by the web interface); the options below take precedence.
"""
import argparse
import subprocess
import sys
import time

from . import batch_cli, config, dubbing, i18n, local_transcription, pipeline, source, steps, transcription, translation


def log(msg: str):
    print(time.strftime("[%H:%M:%S] ") + msg, flush=True)


def download_playlist(url: str, cfg: dict, output_dir: str | None, whole: bool):
    """Folder where the playlist was downloaded, to process as a batch; None if the address is one video to process
    alone (not a playlist, or one video of a list without --playlist)."""
    command = source.ytdlp_command(cfg)
    info = source.playlist_info(url, command)
    if info is None:
        return None
    if info["with_video"] and not whole:
        log(i18n.t("log.cli_video_of_playlist", n=len(info["entries"])))
        return None
    folder = source.playlist_folder(source.download_dir(cfg, output_dir), info)
    log(i18n.t("log.cli_playlist", title=info["title"], n=len(info["entries"]), folder=folder))
    files, failures = source.download_playlist(info, command, folder, log)
    if failures:
        log(i18n.t("log.playlist_failures", n=len(failures)))
    if not files:
        raise source.SourceError(i18n.t("err.playlist_nothing", folder=folder))
    return folder


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):     # output redirected on Windows (cp1252): "→" must not stop the work
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--lang")
    chosen = pre.parse_known_args(argv)[0].lang
    cfg = config.load()
    i18n.set_language(chosen or cfg.get("ui_language"))
    p = argparse.ArgumentParser(prog="python -m subtitles", description=i18n.t("cli.description"), formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--lang", help=i18n.t("cli.lang", langs=", ".join(i18n.available())))
    p.add_argument("source", nargs="?", help=i18n.t("cli.source"))
    p.add_argument("--output-dir", help=i18n.t("cli.output"))
    p.add_argument("--language", help=i18n.t("cli.spoken"))
    p.add_argument("--languages", help=i18n.t("cli.targets"))
    p.add_argument("--no-cleanup", action="store_true", help=i18n.t("cli.no_cleanup"))
    p.add_argument("--transcription", help=i18n.t("cli.transcription"))
    p.add_argument("--translator", help=i18n.t("cli.translator"))
    p.add_argument("--redo", action="store_true", help=i18n.t("cli.redo"))
    p.add_argument("--list", action="store_true", help=i18n.t("cli.list"))
    p.add_argument("--until", choices=steps.STOPS[1:], help=i18n.t("cli.until"))
    p.add_argument("--new-batch", action="store_true", help=i18n.t("cli.new_batch"))
    p.add_argument("--no-subfolders", action="store_true", help=i18n.t("cli.no_subfolders"))
    p.add_argument("--playlist", action="store_true", help=i18n.t("cli.playlist"))
    a = p.parse_args(argv)

    if a.list:
        print(i18n.t("cli.servers", active=cfg.get("active_transcription")))
        for m in cfg.get("transcriptions", []):
            print(f"  {m['id']:10} {config.engine_name(m)}  {m.get('url', '')}")
        print(i18n.t("cli.translators", active=cfg.get("active_translator")))
        for m in cfg.get("translators", []):
            print(f"  {m['id']:10} {config.engine_name(m)}  {m.get('model', '')}")
        return 0
    if not a.source:
        p.error(i18n.t("cli.missing_source"))
    batch = dict(transcription=a.transcription, translator=a.translator, spoken_language=a.language,
                 languages=a.languages.split(",") if a.languages else None, output_dir=a.output_dir, redo=a.redo, stop=a.until,
                 new=a.new_batch, recursive=not a.no_subfolders)
    try:
        if source.is_url(a.source):
            folder = download_playlist(a.source, cfg, a.output_dir, a.playlist)
            if folder is not None:
                return batch_cli.run(str(folder), cfg, log, **batch)
        elif batch_cli.is_batch(a.source):
            return batch_cli.run(a.source, cfg, log, **batch)
        r = pipeline.Request(a.source, cfg, a.transcription, a.translator, a.language, a.output_dir, a.redo,
                             languages=a.languages.split(",") if a.languages else None, cleanup=not a.no_cleanup)
        pipeline.run(r, log)
        return 0
    except (source.SourceError, transcription.TranscriptionError, translation.TranslationError, config.ConfigError,
            local_transcription.LocalServerError, dubbing.DubbingError, subprocess.SubprocessError) as e:
        log(i18n.t("log.error", error=e))
        return 1
    except KeyboardInterrupt:
        log(i18n.t("log.interrupted"))
        return 130


if __name__ == "__main__":
    sys.exit(main())
