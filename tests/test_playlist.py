"""Playlists: read with yt-dlp (simulated here), downloaded into a folder of their own, then a batch like any folder."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from subtitles import __main__ as cli, source
from subtitles.states import DownloadState
from subtitles.web.playlist import PlaylistDownload

YTDLP = ["yt-dlp"]
ENTRIES = [{"_type": "url", "id": "a", "url": "https://v.example/a", "title": "First", "duration": 60},
           {"_type": "url", "id": "b", "url": "https://v.example/b", "title": "Second", "duration": 90},
           {"_type": "url", "id": "c", "url": "https://v.example/c", "title": "Private video", "duration": None}]


class FakeYtdlp:
    """Answers like yt-dlp: a playlist (with or without a video of its own), downloads that create the file."""

    def __init__(self, tmp_path, with_video=False, broken=("c",)):
        self.tmp_path, self.with_video, self.broken, self.downloads = tmp_path, with_video, broken, []

    def __call__(self, cmd, **_kw):
        args = cmd[1:]
        url = args[-1]
        if "-J" in args:
            alone = "--no-playlist" in args
            if alone and self.with_video:
                d = {"_type": "video", "id": "b", "title": "Second"}
            else:
                d = {"_type": "playlist", "id": "PL1", "title": "My: course/2026", "entries": ENTRIES}
            return SimpleNamespace(returncode=0, stdout=json.dumps(d), stderr="")
        ident = url.rsplit("/", 1)[-1]
        if ident in self.broken:
            return SimpleNamespace(returncode=1, stdout="", stderr="ERROR: Private video")
        out = args[args.index("-o") + 1]
        name = out.replace("%(title)s", next(e["title"] for e in ENTRIES if e["id"] == ident)).replace("%(ext)s", "mp4")
        if "--print" in args:
            return SimpleNamespace(returncode=0, stdout=json.dumps({"id": ident, "title": "t", "duration": 1, "filename": name}) + "\n", stderr="")
        self.downloads.append(ident)
        open(name, "wb").write(b"video")
        return SimpleNamespace(returncode=0)


@pytest.fixture
def ytdlp(tmp_path, monkeypatch):
    fake = FakeYtdlp(tmp_path)
    monkeypatch.setattr(source.subprocess, "run", fake)
    monkeypatch.setattr(source, "tool", lambda name: name)
    return fake


def test_a_playlist_is_read_without_downloading(ytdlp):
    p = source.playlist_info("https://v.example/list", YTDLP)
    assert p["title"] == "My: course/2026" and [e["title"] for e in p["entries"]] == ["First", "Second", "Private video"]
    assert p["with_video"] is False and ytdlp.downloads == []


def test_a_video_inside_a_playlist_can_be_processed_alone(ytdlp):
    ytdlp.with_video = True
    assert source.playlist_info("https://v.example/watch?v=b&list=PL1", YTDLP)["with_video"] is True


def test_a_single_video_is_not_a_playlist(monkeypatch):
    monkeypatch.setattr(source, "tool", lambda name: name)
    monkeypatch.setattr(source.subprocess, "run", lambda *_a, **_k: SimpleNamespace(returncode=0, stdout='{"_type": "video", "id": "x"}', stderr=""))
    assert source.playlist_info("https://v.example/x", YTDLP) is None


def test_folder_name_is_readable_and_valid_on_windows():
    assert source.folder_name("My: course/2026") == "My course 2026"
    assert source.folder_name('Vidéos "été" ?') == "Vidéos été"
    assert source.folder_name("...") == "playlist"


def test_download_numbers_the_videos_skips_failures_and_resumes(ytdlp, tmp_path):
    info = source.playlist_info("https://v.example/list", YTDLP)
    folder = source.playlist_folder(tmp_path, info)
    lines = []
    files, failures = source.download_playlist(info, YTDLP, folder, lines.append)
    assert [f.name for f in files] == ["001 - First.mp4", "002 - Second.mp4"]
    assert [f["title"] for f in failures] == ["Private video"] and "Private video" in failures[0]["error"]
    assert ytdlp.downloads == ["a", "b"]
    files, _ = source.download_playlist(info, YTDLP, folder, lines.append)    # again: nothing downloaded twice
    assert ytdlp.downloads == ["a", "b"] and len(files) == 2


def test_cancel_stops_between_two_videos(ytdlp, tmp_path):
    info = source.playlist_info("https://v.example/list", YTDLP)
    files, _ = source.download_playlist(info, YTDLP, tmp_path / "p", print, cancelled=lambda: bool(ytdlp.downloads))
    assert ytdlp.downloads == ["a"] and len(files) == 1


def test_the_address_of_a_whole_playlist_is_refused_as_a_single_video(monkeypatch):
    monkeypatch.setattr(source, "tool", lambda name: name)
    out = "\n".join(json.dumps({"id": e["id"], "title": e["title"], "duration": 1, "filename": f"/v/{e['id']}.mp4"}) for e in ENTRIES)
    monkeypatch.setattr(source.subprocess, "run", lambda *_a, **_k: SimpleNamespace(returncode=0, stdout=out, stderr=""))
    with pytest.raises(source.SourceError, match="playlist"):
        source.youtube_info("https://v.example/list", YTDLP)


def test_background_download_for_the_page(ytdlp, tmp_path):
    job = PlaylistDownload("https://v.example/list", YTDLP, tmp_path)
    job.run()
    d = job.to_dict()
    assert d["state"] == DownloadState.DONE and d["total"] == 3 and d["done"] == 3
    assert d["folder"].endswith("/My course 2026") and len(d["failures"]) == 1 and not job.working()


def test_background_download_of_a_single_video_is_an_error(monkeypatch, tmp_path):
    monkeypatch.setattr(source, "playlist_info", lambda *_a: None)
    job = PlaylistDownload("https://v.example/x", YTDLP, tmp_path)
    job.run()
    assert job.state == DownloadState.ERROR and "playlist" in job.error


def test_command_line_downloads_then_runs_the_folder_as_a_batch(ytdlp, tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(cli.config, "load", lambda: {"output_dir": str(tmp_path), "youtube": {"command": YTDLP}})
    monkeypatch.setattr(cli.batch_cli, "run", lambda path, cfg, log, **kw: seen.update(path=path, kw=kw) or 0)
    assert cli.main(["https://v.example/list", "--languages", "fr"]) == 0
    assert seen["path"].endswith("My course 2026") and seen["kw"]["languages"] == ["fr"]
    assert sorted(p.name for p in (tmp_path / "My course 2026").iterdir()) == ["001 - First.mp4", "002 - Second.mp4"]


def test_command_line_video_of_a_playlist_alone_unless_asked(ytdlp, tmp_path, monkeypatch):
    ytdlp.with_video = True
    monkeypatch.setattr(cli.config, "load", lambda: {"output_dir": str(tmp_path), "youtube": {"command": YTDLP}})
    assert cli.download_playlist("https://v.example/watch?v=b&list=PL1", cli.config.load(), None, False) is None
    assert cli.download_playlist("https://v.example/watch?v=b&list=PL1", cli.config.load(), None, True).name == "My course 2026"


def test_subprocess_errors_are_reported(monkeypatch):
    monkeypatch.setattr(source, "tool", lambda name: name)

    def slow(*_a, **_k):
        raise subprocess.TimeoutExpired("yt-dlp", 1)
    monkeypatch.setattr(source.subprocess, "run", slow)
    with pytest.raises(subprocess.TimeoutExpired):
        source.playlist_info("https://v.example/list", YTDLP)


def test_download_settings_become_ytdlp_options(tmp_path):
    cfg = {"output_dir": str(tmp_path / "out"), "youtube": {"command": ["wsl", "yt-dlp"], "max_height": 1080, "max_audio_kbps": 128,
                                                           "merge_format": "mp4", "retries": 10, "extra_args": ["--cookies-from-browser", "firefox"]}}
    c = source.ytdlp_command(cfg)
    assert c[:2] == ["wsl", "yt-dlp"] and c[-2:] == ["--cookies-from-browser", "firefox"]
    assert c[c.index("-f") + 1] == "bv*[height<=1080]+ba[abr<=128]/bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b"
    assert c[c.index("--merge-output-format") + 1] == "mp4" and "--fragment-retries" in c
    assert source.ytdlp_format() == "bv*+ba/b"
    assert source.ytdlp_command({"output_dir": "x"}) == ["yt-dlp", "-f", "bv*+ba/b"]
    assert source.download_dir(cfg) == tmp_path / "out"
    assert source.download_dir({**cfg, "youtube": {"download_dir": str(tmp_path / "dl")}}, "page") == tmp_path / "dl"
