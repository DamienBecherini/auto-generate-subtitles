"""Les tests vérifient les messages en français : langue imposée, quelle que soit celle du système."""
import pytest

from soustitres import i18n


@pytest.fixture(autouse=True)
def _langue_francaise():
    i18n.definir("fr")
    yield
    i18n.definir(None)
