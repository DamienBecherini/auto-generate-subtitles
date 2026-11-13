"""The tests check the French messages: language imposed, whatever the system's. They never touch the files of the
user folder (history, last batch, speeds): an archived task would otherwise replace the real history."""
import pytest

from subtitles import i18n
from subtitles.web import batch_manager, tasks, timing


@pytest.fixture(autouse=True)
def _french_language():
    i18n.set_language("fr")
    yield
    i18n.set_language(None)


@pytest.fixture(autouse=True)
def _user_files_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks, "HISTORY_FILE", tmp_path / "user" / "history.json")
    monkeypatch.setattr(batch_manager, "LAST_BATCH_FILE", tmp_path / "user" / "last_batch.json")
    monkeypatch.setattr(timing, "FILE", tmp_path / "user" / "speeds.json")
    monkeypatch.setattr(timing, "_memory", None)
