import threading

from subtitles import __main__ as cli, batch_cli, config, folder
from subtitles.web import tasks
from subtitles.web.batches import Batch


def manager(tmp_path, monkeypatch, run):
    """Manager whose loop runs, but where each task is simulated by `run(t)` (nothing is transcribed)."""
    monkeypatch.setattr(tasks, "LAST_BATCH_FILE", tmp_path / "last_batch.json")
    g = tasks.Manager.__new__(tasks.Manager)
    g.tasks, g.order, g.batches, g.history = {}, [], {}, []
    g.lock, g.wake = threading.Lock(), threading.Event()

    def simulate(t):
        t.state = "running"
        run(t)
        g._update_batch(t)
    monkeypatch.setattr(g, "_run", simulate)
    threading.Thread(target=g._loop, daemon=True).start()
    return g


def prepare(tmp_path, monkeypatch, names=("a.mp4", "b.mp4")):
    videos = tmp_path / "videos"
    videos.mkdir()
    for n in names:
        (videos / n).write_bytes(b"")
    cfg = {"output_dir": str(tmp_path / "output"), "target_languages": ["fr"], "destination": {"next_to_video": True, "output_dir": False}}
    monkeypatch.setattr(config, "load", lambda: cfg)
    return videos, cfg


def done(t):
    t.write("finished: " + t.options["source"])
    t.state = "done"


def test_folder_creates_a_batch_and_runs_it_to_the_end(tmp_path, monkeypatch, capsys):
    videos, cfg = prepare(tmp_path, monkeypatch)
    g = manager(tmp_path, monkeypatch, done)
    lines = []
    code = batch_cli.run(str(videos), cfg, lines.append, languages=["fr", "de"], manager=g)
    assert code == 0
    batch = folder.read_batch(videos / folder.BATCH_FILE_NAME)
    assert batch["state"] == "done" and [v["state"] for v in batch["videos"]] == ["done", "done"]
    assert batch["options"]["pause"] is False and batch["options"]["languages"] == ["fr", "de"]
    assert any("1/2 : a.mp4" in x for x in lines) and "finished: " in capsys.readouterr().out


def test_folder_with_existing_batch_resumes_it(tmp_path, monkeypatch):
    videos, cfg = prepare(tmp_path, monkeypatch)
    a, b = (str(videos / n).replace("\\", "/") for n in ("a.mp4", "b.mp4"))
    old = Batch(str(videos), [{"path": a, "name": "a.mp4", "language": "", "state": "done", "task": "x", "count": 3, "detected_language": "en", "error": ""},
                              {"path": b, "name": "b.mp4", "language": "", "state": "running", "task": "y", "count": None, "detected_language": "", "error": ""}],
                {"translator": "local", "pause": True}, videos / folder.BATCH_FILE_NAME)
    old.save()
    started = []
    g = manager(tmp_path, monkeypatch, lambda t: (started.append(t.options["source"]), done(t)))
    assert batch_cli.run(str(videos), cfg, lambda _m: None, translator="claude", manager=g) == 0
    assert started == [b]                                     # the finished video is skipped
    batch = folder.read_batch(videos / folder.BATCH_FILE_NAME)
    assert batch["id"] == old.id and batch["options"]["translator"] == "claude" and batch["options"]["pause"] is False


def test_error_of_a_video_gives_exit_code_1(tmp_path, monkeypatch):
    videos, cfg = prepare(tmp_path, monkeypatch)

    def fails_on_b(t):
        if t.options["source"].endswith("b.mp4"):
            t.state, t.error = "error", "serveur injoignable"
        else:
            done(t)
    g = manager(tmp_path, monkeypatch, fails_on_b)
    lines = []
    assert batch_cli.run(str(videos), cfg, lines.append, manager=g) == 1
    assert any("b.mp4 : serveur injoignable" in x for x in lines)


def test_batch_of_another_folder_in_the_output_dir_ignored(tmp_path, monkeypatch):
    videos, cfg = prepare(tmp_path, monkeypatch)
    other = Batch(str(tmp_path / "elsewhere"), [], {}, tmp_path / "output" / folder.BATCH_FILE_NAME)
    other.save()
    assert batch_cli.find_batch_file(videos, cfg, None) is None
    assert batch_cli.find_batch_file(other.file, cfg, None) == other.file


def test_the_command_sends_a_folder_to_the_batch(tmp_path, monkeypatch):
    videos, _ = prepare(tmp_path, monkeypatch)
    got = {}
    monkeypatch.setattr(batch_cli, "run", lambda path, cfg, log, **o: got.update(o, path=path) or 0)
    assert cli.main([str(videos), "--languages", "fr,en", "--until", "mux", "--no-subfolders"]) == 0
    assert got["path"] == str(videos) and got["languages"] == ["fr", "en"] and got["stop"] == "mux" and got["recursive"] is False


def test_batch_summary_gives_the_step_of_the_running_video(tmp_path):
    batch = Batch("D:/", [{"path": "D:/a.mp4", "name": "a.mp4", "state": "running", "task": "t1"}], {}, tmp_path / folder.BATCH_FILE_NAME)
    assert batch.summary()["step"] == ""                      # no manager plugged in
    batch.step_of = {"t1": "translation"}.get
    assert batch.phase == 1 and batch.summary()["step"] == "translation"
