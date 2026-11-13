"""Retry of a task in error, and program that holds the port of the local transcription server."""
import os
import socket

import pytest

from subtitles import local_transcription
from subtitles.web.manager import Manager
from subtitles.web.tasks import Task, TaskQueue


def manager(history=()):
    g = Manager(TaskQueue(history=list(history)), start=False)
    return g


def task(g, state, **options):
    t = Task({"source": "video.mp4", **options})
    t.state = state
    g.queue.tasks[t.id] = t
    return t


def test_retry_keeps_the_settings():
    g = manager()
    t = task(g, "error", transcription="local", translator="claude", until="mux")
    n = g.queue.retry(t.id)
    assert n.id != t.id and n.options == t.options


def test_retry_from_the_history():
    g = manager([{"id": "ab", "state": "error", "source": "v.mp4", "options": {"language": "en"}}])
    assert g.queue.retry("ab").options == {"language": "en", "source": "v.mp4"}


@pytest.mark.parametrize("state", ["queued", "running", "paused", "done", "cancelled"])
def test_retry_only_after_an_error(state):
    g = manager()
    assert g.queue.retry(task(g, state).id) is None


def test_video_of_a_batch_retried_by_the_batch():
    g = manager()
    assert g.queue.retry(task(g, "error", batch="xyz").id) is None


def test_unknown_task():
    assert manager().queue.retry("unknown") is None


@pytest.mark.skipif(os.name != "nt", reason="netstat and tasklist: Windows only")
def test_port_owner():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        assert "python" in local_transcription.port_owner(f"http://127.0.0.1:{port}").lower()
    assert local_transcription.port_owner(f"http://127.0.0.1:{port}") == ""
