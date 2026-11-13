"""The tests check the French messages: language imposed, whatever the system's."""
import pytest

from subtitles import i18n


@pytest.fixture(autouse=True)
def _french_language():
    i18n.set_language("fr")
    yield
    i18n.set_language(None)
