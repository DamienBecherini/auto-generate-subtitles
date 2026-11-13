"""Step-by-step flow: review, validation, continuing an episode, batch phases, mux and starting the dubbing."""
import json
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from subtitles import config, dubbing, folder, glossary, muxing, pipeline, review, steps
from subtitles.web import tasks
from subtitles.web.batches import Batch
from subtitles.web.tasks import Manager, Task

CLAUDE = {"id": "claude", "name": "Claude", "type": "claude"}


class FakeTranslator:
    def __init__(self, answers):
        self.answers, self.requests, self.finished = list(answers), [], False

    def complete(self, system, request, schema=None, effort=None):
        self.requests.append(request)
        return json.dumps({"entries": self.answers.pop(0)})

    def finish(self):
        self.finished = True


def entry(i, status="to_fix", spelling="Gariaev", french="Gariaev", confidence="high"):
    return {"index": i, "status": status, "spelling": spelling, "french": french, "confidence": confidence, "reason": "son proche"}


def test_candidates_drop_common_words_and_the_possessive():
    t = "We read Gary Iev's work. Science is good. The science of science. the Pyotr Gary Iev spoke."
    names = {c["name"] for c in review.candidates(t)}
    assert "Gary Iev" in names and "Pyotr Gary Iev" in names
    assert "Science" not in names


def test_a_name_is_submitted_only_once_per_batch(tmp_path):
    batch = tmp_path / "batch.json"
    t1, t2 = tmp_path / "a", tmp_path / "b"
    t1.mkdir(), t2.mkdir()
    fake = FakeTranslator([[entry(0)], []])
    create = lambda m, j: fake  # noqa: E731
    assert review.review(CLAUDE, batch, t1, "Episode", ["We saw Gary Iev come."], log=lambda _m: None, create=create) == ["Gary Iev"]
    assert review.review(CLAUDE, batch, t2, "Episode", ["We saw Gary Iev and Walter Russell speak."], log=lambda _m: None, create=create) == ["Gary Iev"]
    assert "0. Walter Russell" in fake.requests[1] and "Gary Iev (" not in fake.requests[1]
    assert fake.finished


def test_validation_feeds_the_glossary_and_releases_the_video(tmp_path):
    batch = tmp_path / "batch.json"
    t = tmp_path / "a"
    t.mkdir()
    fake = FakeTranslator([[entry(0), entry(1, status="uncertain", spelling="", french="")]])
    review.review(CLAUDE, batch, t, "E", ["We saw Gary Iev and Zork Blim come."], log=lambda _m: None, create=lambda m, j: fake)
    pending = review.pending_names(batch)
    video = tmp_path / "v.mp4"
    assert steps.observe(t, video, ["fr"], "en", pending)["validated"] is False
    assert review.validate(batch, [{"transcribed": "Gary Iev", "spelling": "Gariaev", "french": "Gariaev", "accept": True},
                                   {"transcribed": "Zork Blim", "spelling": "", "french": "", "accept": False}]) == (1, 1)
    assert review.pending_names(batch) == set()
    assert steps.observe(t, video, ["fr"], "en", set())["validated"] is True
    assert [e["spelling"] for e in glossary.load(glossary.path_for(batch))] == ["Gariaev"]
    fake2 = FakeTranslator([])
    review.review(CLAUDE, batch, t, "E", ["We saw Gary Iev and Zork Blim come."], log=lambda _m: None, create=lambda m, j: fake2)
    assert fake2.requests == []                # rejected name: never proposed again


def test_a_french_form_does_not_rewrite_the_name(tmp_path):
    t = tmp_path / "a"
    t.mkdir()
    fake = FakeTranslator([[entry(0, status="french_form", spelling="Fingerprints of the Gods", french="Les Empreintes des dieux")]])
    review.review(CLAUDE, tmp_path / "batch.json", t, "E", ["We read Fingerprints twice."], log=lambda _m: None, create=lambda m, j: fake)
    assert review.proposals(tmp_path / "batch.json")[0]["spelling"] == "Fingerprints"


def test_api_failure_leaves_the_review_to_redo(tmp_path):
    class Failure(FakeTranslator):
        def complete(self, *a, **k):
            raise ValueError("failure")
    t = tmp_path / "a"
    t.mkdir()
    with pytest.raises(ValueError):
        review.review(CLAUDE, tmp_path / "batch.json", t, "E", ["We saw Gary Iev come."], log=lambda _m: None, create=lambda m, j: Failure([]))
    assert not (t / review.VIDEO_FILE).exists()


def test_observe_reads_the_steps_on_the_disk(tmp_path):
    t, v = tmp_path / "t", tmp_path / "v.mp4"
    t.mkdir()
    e = steps.observe(t, v, ["fr"], "en", set())
    assert not any(e.values())
    (t / "transcription.json").write_text("{}")
    (t / "translation.fr.json").write_text("[]")
    steps.dubbing_file(t, "fr").parent.mkdir()
    steps.dubbing_file(t, "fr").write_bytes(b"x")
    e = steps.observe(t, v, ["fr"], "en", set())
    assert e["transcribed"] and e["translated"] and e["dubbed"] and not e["muxed"]
    steps.mkv_path(v).write_bytes(b"x")
    assert steps.observe(t, v, ["fr"], "en", set())["muxed"]


def test_the_dubbing_escapes_the_cleanup(tmp_path):
    from subtitles import cleanup
    f = steps.dubbing_file(tmp_path, "fr")
    f.parent.mkdir()
    f.write_bytes(b"x")
    cleanup.clean_folder(tmp_path, {**cleanup.DEFAULTS, "audio": True})
    assert f.exists()


def test_the_scan_ignores_the_muxed_mkv(tmp_path):
    (tmp_path / "a.mp4").write_bytes(b"x")
    (tmp_path / "a.multi.mkv").write_bytes(b"x")
    assert [v["name"] for v in folder.scan(tmp_path)] == ["a.mp4"]


# --- Batch phases and "Continue" ---------------------------------------------------------------------------------

def manager(monkeypatch, tmp_path, with_dubbing=True):
    monkeypatch.setattr(tasks, "LAST_BATCH_FILE", tmp_path / "last_batch.json")
    d = {"enabled": with_dubbing, "command": ["python"], "model": "m"}
    monkeypatch.setattr(config, "load", lambda: {"translators": [CLAUDE], "active_translator": "claude", "output_dir": str(tmp_path), "dubbing": d})
    g = Manager.__new__(Manager)
    g.tasks, g.order, g.batches, g.history = {}, [], {}, []
    g.lock, g.wake = threading.Lock(), threading.Event()
    return g


def create_batch(g, tmp_path, states, **options):
    videos = [{"path": f"v{i}.mp4", "name": f"v{i}.mp4", "state": e, "task": None, "error": ""} for i, e in enumerate(states)]
    batch = Batch("r", videos, {"translator": "claude", **options}, tmp_path / "batch.json", "L")
    g.batches["L"] = batch
    return batch


def test_after_the_translation_the_batch_moves_to_dubbing(tmp_path):
    batch = Batch("r", [{"path": "a", "state": "translated"}, {"path": "b", "state": "done"}], {}, tmp_path / "l.json", "L")
    batch.phase = 2
    batch.end_phase()
    assert (batch.phase, batch.state) == (3, "running")
    assert batch.next_video()["path"] == "a"
    batch.videos[0]["state"] = "done"
    batch.end_phase()
    assert batch.state == "done"


def test_dubbing_phase_one_video_at_a_time_up_to_the_stop(monkeypatch, tmp_path):
    g = manager(monkeypatch, tmp_path)
    batch = create_batch(g, tmp_path, ["translated", "translated"], stop="mux")
    batch.phase = 3
    g._feed_batch(batch)
    assert [v["state"] for v in batch.videos] == ["running", "translated"]
    assert g.tasks[batch.videos[0]["task"]].options["until"] == "mux"


def test_translated_video_waits_for_dubbing_only_if_the_batch_asks(monkeypatch, tmp_path):
    g = manager(monkeypatch, tmp_path)
    t = Task({"source": "v", "batch": "L"})
    assert g._left_to_dub(create_batch(g, tmp_path, ["done"], stop="dubbing"), t) is True
    assert g._left_to_dub(create_batch(g, tmp_path, ["done"], stop="translation"), t) is False
    assert g._left_to_dub(create_batch(g, tmp_path, ["done"]), t) is False


def test_continue_resumes_the_paused_task_to_the_end(monkeypatch, tmp_path):
    g = manager(monkeypatch, tmp_path)
    batch = create_batch(g, tmp_path, ["prepared", "prepared"], stop="mux")
    batch.state = "awaiting_translation"
    t = Task({"source": "v0.mp4", "batch": "L"})
    t.state, t.prepared = "paused", SimpleNamespace(cues=[1], code="en")
    g.tasks[t.id] = t
    g.order.append(t.id)
    batch.videos[0]["task"] = t.id
    assert g.batch_continue("L", "v0.mp4")
    assert t.state == "queued" and t.options["until"] == "mux" and t.options["pause"] is False
    assert [v["state"] for v in batch.videos] == ["running", "prepared"]
    assert batch.state == "awaiting_translation"
    assert not g.batch_continue("L", "v0.mp4")   # already running


def test_continue_without_dubbing_stops_at_the_translation(monkeypatch, tmp_path):
    g = manager(monkeypatch, tmp_path, with_dubbing=False)
    batch = create_batch(g, tmp_path, ["todo"])
    assert g.batch_continue("L", "v0.mp4")
    assert g.tasks[batch.videos[0]["task"]].options["until"] == "translation"


def test_subtitles_only_batch_never_starts_the_dubbing(monkeypatch, tmp_path):
    g = manager(monkeypatch, tmp_path)
    batch = create_batch(g, tmp_path, ["todo"], stop="translation")
    assert g.batch_continue("L", "v0.mp4")
    assert g.tasks[batch.videos[0]["task"]].options["until"] == "translation"


def test_dubbing_goal_afterwards_starts_nothing(monkeypatch, tmp_path):
    g = manager(monkeypatch, tmp_path)
    batch = create_batch(g, tmp_path, ["done", "done", "done"], stop="translation")
    batch.state = "done"
    batch.videos[0].update(translated=True)
    batch.videos[1].update(translated=True, muxed=True)
    assert g.batch_goal("L", "mux")
    assert [v["state"] for v in batch.videos] == ["translated", "done", "done"]
    assert batch.state == "suspended" and not g.tasks
    assert g.batch_goal("L", "translation")
    assert [v["state"] for v in batch.videos] == ["done", "done", "done"]
    assert not g.batch_goal("L", "anything")


def test_dubbing_time_announced_only_if_asked(tmp_path):
    videos = [{"path": "a", "state": "translated", "duration": 1000.0}, {"path": "b", "state": "done", "duration": 1000.0, "muxed": True}]
    batch = Batch("r", videos, {"stop": "translation"}, tmp_path / "l.json", "L")
    assert batch.dubbing_left_s() == 0
    batch.options["stop"] = "mux"
    assert batch.dubbing_left_s() > 0


def test_reloading_a_batch_keeps_the_videos_to_dub(tmp_path):
    f = tmp_path / "batch.json"
    folder.write_batch(f, {"id": "L", "root": "r", "options": {"stop": "mux"}, "state": "running", "videos": [{"path": "a", "state": "translated"}, {"path": "b", "state": "running"}]})
    assert [v["state"] for v in Batch.from_file(f).videos] == ["translated", "todo"]


def test_continue_restarts_a_finished_batch_for_this_video(monkeypatch, tmp_path):
    g = manager(monkeypatch, tmp_path)
    batch = create_batch(g, tmp_path, ["done", "done"])
    batch.state, batch.phase, batch.finished = "done", 2, 123.0
    assert g.batch_continue("L", "v1.mp4")
    assert (batch.state, batch.phase, batch.finished) == ("running", 3, None)
    assert g.tasks[batch.videos[1]["task"]].options["check_names"] is True


def test_the_dubbing_is_never_started_in_parallel(monkeypatch, tmp_path):
    g = manager(monkeypatch, tmp_path)
    batch = create_batch(g, tmp_path, ["running"])
    batch.concurrency = 5
    t = Task({"source": "v", "batch": "L", "until": "mux"})
    t.prepared = SimpleNamespace(cues=[], code="en")
    seen = []
    monkeypatch.setattr(g, "_run", lambda x: seen.append(threading.current_thread()), raising=False)
    g._start(t)
    assert seen == [threading.current_thread()]


def test_reloaded_batch_keeps_the_names_to_validate(monkeypatch, tmp_path):
    g = manager(monkeypatch, tmp_path)
    batch = create_batch(g, tmp_path, ["prepared"], review=True)
    review._write(review.path_for(batch.file), {"examined": ["Gary Iev"], "proposals": [{"transcribed": "Gary Iev", "spelling": "Gariaev", "occurrences": 2}]})
    t = pipeline.work_dir(tmp_path, "v0.mp4")
    t.mkdir(parents=True)
    (t / review.VIDEO_FILE).write_text(json.dumps({"names": ["Gary Iev"]}))
    g._observe(batch)
    assert batch.videos[0]["reviewed"] and not batch.videos[0]["validated"]
    assert [p["transcribed"] for p in g.batch_proposals("L", "v0.mp4")["proposals"]] == ["Gary Iev"]
    assert g.validate_names("L", [{"transcribed": "Gary Iev", "spelling": "Gariaev", "french": "", "accept": True}]) == {"accepted": 1, "rejected": 0}
    assert batch.videos[0]["validated"]


# --- Mux ---------------------------------------------------------------------------------------------------------

INFO = {"streams": [{"index": 0, "codec_type": "video", "codec_name": "h264"}, {"index": 1, "codec_type": "audio", "codec_name": "aac", "tags": {"language": "und"}},
                    {"index": 2, "codec_type": "subtitle", "codec_name": "mov_text"}], "format": {"duration": "10.0"}}


def test_command_keeps_the_tracks_and_adds_the_dubbing():
    c = muxing.command("ffmpeg", Path("v.mp4"), INFO, Path("d.m4a"), "fr", "en", [("en", Path("v.en.srt")), ("fr", Path("v.fr.srt"))], Path("s.part"))
    t = " ".join(c)
    assert "-map 0:a" in t and "-map 1:a:0" in t and "-map 2:0" in t and "-map 3:0" in t
    assert "-c:v copy" in t and "-c:a copy" in t and "-c:s:0 srt" in t
    assert "language=eng" in t and "language=fre" in t and "title=Français (doublage IA)" in t
    assert "-c:a aac" not in t                 # the dubbed track is already encoded


def tools_present():
    from shutil import which
    return which("ffmpeg") and which("ffprobe")


@pytest.mark.skipif(not tools_present(), reason="ffmpeg missing")
def test_real_mux_does_not_touch_the_original(tmp_path):
    v, d = tmp_path / "v.mp4", tmp_path / "d.m4a"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=2", "-f", "lavfi", "-i", "sine=f=220:d=2", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(v)], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=440:d=2", "-c:a", "aac", str(d)], check=True)
    subs = {c: tmp_path / f"v.{c}.srt" for c in ("en", "fr")}
    for f in subs.values():
        f.write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n", encoding="utf-8")
    before = v.read_bytes()
    m = muxing.mux(v, d, "fr", "en", list(subs.items()), log=lambda _m: None)
    assert m.name == "v.multi.mkv" and v.read_bytes() == before and not m.with_name(m.name + ".part").exists()
    info = muxing.probe(m)
    assert [s["codec_type"] for s in info["streams"]] == ["video", "audio", "audio", "subtitle", "subtitle"]
    assert [s["tags"].get("language") for s in info["streams"] if s["codec_type"] == "subtitle"] == ["eng", "fre"]


def clip(tmp_path, name="v.mp4"):
    v, d = tmp_path / name, tmp_path / "d.m4a"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=2", "-f", "lavfi", "-i", "sine=f=220:d=2", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(v)], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=2", "-c:a", "aac", str(d)], check=True)
    st = tmp_path / f"{Path(name).stem}.en.srt"
    st.write_text("1\n00:00:00,000 --> 00:00:01,000\nx\n", encoding="utf-8")
    return v, muxing.mux(v, d, "fr", "en", [("en", st)], log=lambda _m: None)


@pytest.mark.skipif(not tools_present(), reason="ffmpeg missing")
def test_replace_puts_the_mkv_in_place_of_the_original(monkeypatch, tmp_path):
    v, m = clip(tmp_path)
    trashed = []
    monkeypatch.setattr(muxing, "to_trash", lambda f: (trashed.append(f), Path(f).unlink(), True)[2])
    final = muxing.replace_original(v, m, 2, log=lambda _m: None)
    assert final == tmp_path / "v.mkv" and final.exists() and trashed == [v] and not m.exists() and not v.exists()


@pytest.mark.skipif(not tools_present(), reason="ffmpeg missing")
def test_without_recycle_bin_everything_is_kept(monkeypatch, tmp_path):
    v, m = clip(tmp_path)
    monkeypatch.setattr(muxing, "to_trash", lambda f: False)
    assert muxing.replace_original(v, m, 2, log=lambda _m: None) == m
    assert v.exists() and m.exists()


@pytest.mark.skipif(not tools_present(), reason="ffmpeg missing")
def test_incomplete_mkv_never_replaces(monkeypatch, tmp_path):
    v, m = clip(tmp_path)
    monkeypatch.setattr(muxing, "to_trash", lambda f: pytest.fail("must not throw the original away"))
    with pytest.raises(muxing.MuxError):
        muxing.replace_original(v, m, 5, log=lambda _m: None)
    assert v.exists() and m.exists()


@pytest.mark.skipif(not tools_present(), reason="ffmpeg missing")
def test_final_name_already_taken_does_not_replace(monkeypatch, tmp_path):
    v, m = clip(tmp_path)
    (tmp_path / "v.mkv").write_bytes(b"other")
    monkeypatch.setattr(muxing, "to_trash", lambda f: pytest.fail("must not throw the original away"))
    assert muxing.replace_original(v, m, 2, log=lambda _m: None) == m
    assert (tmp_path / "v.mkv").read_bytes() == b"other"


def test_replaced_video_still_counted_as_muxed(tmp_path):
    t = tmp_path / "t"
    m = steps.integration_mark(t)
    m.parent.mkdir(parents=True)
    m.write_text("x")
    assert steps.observe(t, tmp_path / "v.mkv", ["fr"], "en", set())["muxed"]


def test_replacement_changes_the_path_of_the_batch_video(monkeypatch, tmp_path):
    g = manager(monkeypatch, tmp_path)
    batch = create_batch(g, tmp_path, ["running"], stop="mux", replace=True)
    t = Task({"source": "v0.mp4", "batch": "L", "until": "mux"})
    t.state, t.prepared, t.result = "done", SimpleNamespace(cues=[1], code="en"), {"mkv": "D:\\x\\v0.mkv", "replaced": True}
    batch.videos[0]["task"] = t.id
    g.tasks[t.id] = t
    g._update_batch(t)
    assert (batch.videos[0]["path"], batch.videos[0]["name"], batch.videos[0]["state"]) == ("D:/x/v0.mkv", "v0.mkv", "done")


def test_subtitles_goal_cancels_the_replacement(monkeypatch, tmp_path):
    g = manager(monkeypatch, tmp_path)
    batch = create_batch(g, tmp_path, ["done"], stop="mux", replace=True)
    assert g.batch_goal("L", "translation", True)
    assert batch.options["replace"] is False


# --- Subtitles fixed by hand ---------------------------------------------------------------------------------------

def test_regenerated_srt_replaces_only_what_the_service_placed(tmp_path):
    a, c, e = tmp_path / "a.srt", tmp_path / "c.srt", {}
    a.write_text("v1", encoding="utf-8")
    pipeline.place_srt(a, c, e, log=lambda _m: None)
    assert c.read_text(encoding="utf-8") == "v1"
    a.write_text("v2", encoding="utf-8")
    pipeline.place_srt(a, c, e, log=lambda _m: None)
    assert c.read_text(encoding="utf-8") == "v2"


def test_srt_fixed_by_hand_is_not_overwritten(tmp_path):
    a, c, e, seen = tmp_path / "a.srt", tmp_path / "c.srt", {}, []
    a.write_text("v1", encoding="utf-8")
    pipeline.place_srt(a, c, e, log=seen.append)
    c.write_text("corrigé", encoding="utf-8")
    a.write_text("v2", encoding="utf-8")
    pipeline.place_srt(a, c, e, log=seen.append)
    assert c.read_text(encoding="utf-8") == "corrigé" and "gardés" in seen[0]
    pipeline.place_srt(a, c, e, redo=True, log=seen.append)
    assert c.read_text(encoding="utf-8") == "v2"


def test_different_srt_without_fingerprint_is_kept(tmp_path):
    a, c = tmp_path / "a.srt", tmp_path / "c.srt"
    a.write_text("new", encoding="utf-8")
    c.write_text("old", encoding="utf-8")
    pipeline.place_srt(a, c, {}, log=lambda _m: None)
    assert c.read_text(encoding="utf-8") == "old"


# --- Starting the dubbing ------------------------------------------------------------------------------------------

def test_path_for_wsl():
    assert dubbing.path_for({"dubbing": {"wsl": True}}, "D:/Vidéos/a b.mp4") == "/mnt/d/Vidéos/a b.mp4"
    assert dubbing.path_for({"dubbing": {}}, "D:/a.mp4") == "D:/a.mp4"


def fake_program(tmp_path, body):
    f = tmp_path / "fake.py"
    f.write_text("import json, sys\nsys.stdout.reconfigure(encoding='utf-8')\nt = json.loads(sys.stdin.readline())\n" + body, encoding="utf-8")
    return f


def dubbing_cfg():
    return {"dubbing": {"enabled": True, "command": [sys.executable], "model": "m"}}


def test_dubbing_logs_and_produces_the_file(monkeypatch, tmp_path):
    p = fake_program(tmp_path, "print('» Doublage : 1/1', flush=True)\nprint('noise', flush=True)\nopen(t['output'], 'wb').write(b'x')\n")
    monkeypatch.setattr(dubbing, "WORKER", p)
    lines = []
    r = dubbing.dub(dubbing_cfg(), Path("v.mp4"), tmp_path, Path("a.srt"), Path("b.srt"), "fr", log=lines.append)
    assert r.read_bytes() == b"x" and lines == ["Doublage : 1/1"]


def test_dubbing_reports_the_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(dubbing, "WORKER", fake_program(tmp_path, "print('CUDA out of memory', flush=True)\nsys.exit(3)\n"))
    with pytest.raises(dubbing.DubbingError, match="CUDA out of memory"):
        dubbing.dub(dubbing_cfg(), Path("v.mp4"), tmp_path, Path("a.srt"), Path("b.srt"), "fr", log=lambda _m: None)


def test_cancelled_dubbing_stops_the_program(monkeypatch, tmp_path):
    p = fake_program(tmp_path, "import time\nfor i in range(600):\n    print('tick', flush=True)\n    time.sleep(0.1)\nopen(t['output'], 'wb').write(b'x')\n")
    monkeypatch.setattr(dubbing, "WORKER", p)
    with pytest.raises(dubbing.DubbingError, match="annulée"):
        dubbing.dub(dubbing_cfg(), Path("v.mp4"), tmp_path, Path("a.srt"), Path("b.srt"), "fr", log=lambda _m: None, cancelled=lambda: True)
    assert not steps.dubbing_file(tmp_path, "fr").exists()


def test_dubbing_already_done_is_not_redone(tmp_path):
    f = steps.dubbing_file(tmp_path, "fr")
    f.parent.mkdir()
    f.write_bytes(b"x")
    assert dubbing.dub({}, Path("v.mp4"), tmp_path, Path("a"), Path("b"), "fr", log=lambda _m: None) == f
