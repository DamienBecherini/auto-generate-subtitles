from pathlib import Path

from subtitles.web import server
from subtitles.web.batches import Batch
from subtitles.web.idle import IdleWatch, is_activity


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def test_polling_and_actions():
    for path in ("/api/health", "/api/status", "/api/tasks", "/api/tasks/ab12?since=3", "/api/batches/ab12", "/api/batches/ab12/estimate"):
        assert not is_activity("GET", path), path
    assert is_activity("GET", "/") and is_activity("GET", "/api/config") and is_activity("GET", "/api/cleanup")
    assert is_activity("POST", "/api/tasks") and is_activity("PUT", "/api/config")


def test_expires_after_the_timeout_and_an_action_postpones_it():
    c = Clock()
    v = IdleWatch(lambda: 3600, lambda: False, c)
    c.t = 3599
    assert not v.expired()
    v.touch()
    c.t = 3599 + 3599
    assert not v.expired()
    c.t = 3599 + 3600
    assert v.expired()


def test_the_countdown_starts_from_the_end_of_the_work():
    c = Clock()
    busy = [True]
    v = IdleWatch(lambda: 3600, lambda: busy[0], c)
    c.t = 10000
    assert not v.expired()
    busy[0] = False
    c.t = 10000 + 3599
    assert not v.expired()
    c.t = 10000 + 3600
    assert v.expired()


def test_zero_disables():
    c = Clock()
    v = IdleWatch(lambda: 0, lambda: False, c)
    c.t = 1e9
    assert not v.expired()
    assert v.remaining_s() is None


def test_remaining_time():
    c = Clock()
    busy = [False]
    v = IdleWatch(lambda: 3600, lambda: busy[0], c)
    c.t = 600
    assert v.remaining_s() == 3000
    busy[0] = True
    assert v.remaining_s() is None
    c.t = 1e6
    busy[0] = False
    assert v.remaining_s() == 0


def test_progress_of_a_batch():
    videos = [{"state": e} for e in ("done", "prepared", "running", "todo", "todo")]
    batch = Batch("/r", videos, {}, Path("/r/f.json"))
    assert batch.progress_text() == "lot 3/5"
    batch.phase = 2
    assert batch.progress_text() == "lot 4/5 (traduction)"


def test_health_answer_follows_the_contract(monkeypatch):
    class Manager:
        def busy(self):
            return True

        def activity(self):
            return "lot 3/22"

        def remaining_s(self):
            return 720

    monkeypatch.setattr(server, "manager", Manager())
    monkeypatch.setattr(server, "idle_timeout_s", lambda: 3600)
    monkeypatch.setattr(server, "idle", IdleWatch(lambda: 3600, lambda: True))
    d = server.health()
    assert (d["state"], d["contract"], d["service"], d["busy"], d["activity"]) == ("ready", 2, "subtitles", True, "lot 3/22")
    assert d["remaining_work_s"] == 720
    assert d["idle"] == {"timeout_s": 3600, "remaining_s": None}
