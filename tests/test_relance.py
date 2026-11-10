"""Relance d'une tâche en erreur, et programme qui occupe le port du serveur de transcription local."""
import os
import socket
import threading

import pytest

from soustitres import transcription_locale
from soustitres.web.taches import Gestionnaire, Tache


def gestionnaire(historique=()):
    g = Gestionnaire.__new__(Gestionnaire)
    g.taches, g.ordre, g.lots, g.historique = {}, [], {}, list(historique)
    g.verrou, g.reveil = threading.Lock(), threading.Event()
    return g


def tache(g, etat, **options):
    t = Tache({"source": "video.mp4", **options})
    t.etat = etat
    g.taches[t.id] = t
    return t


def test_relance_garde_les_reglages():
    g = gestionnaire()
    t = tache(g, "erreur", transcription="local", traducteur="claude", jusqua="assemblage")
    n = g.reessayer(t.id)
    assert n.id != t.id and n.options == t.options


def test_relance_depuis_l_historique():
    g = gestionnaire([{"id": "ab", "etat": "erreur", "source": "v.mp4", "options": {"langue": "en"}}])
    assert g.reessayer("ab").options == {"langue": "en", "source": "v.mp4"}


@pytest.mark.parametrize("etat", ["en_attente", "en_cours", "en_pause", "terminee", "annulee"])
def test_relance_seulement_apres_une_erreur(etat):
    g = gestionnaire()
    assert g.reessayer(tache(g, etat).id) is None


def test_video_d_un_lot_relancee_par_le_lot():
    g = gestionnaire()
    assert g.reessayer(tache(g, "erreur", lot="xyz").id) is None


def test_tache_inconnue():
    assert gestionnaire().reessayer("inconnue") is None


@pytest.mark.skipif(os.name != "nt", reason="netstat et tasklist : Windows seulement")
def test_occupant_du_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        s.listen()
        port = s.getsockname()[1]
        assert "python" in transcription_locale.occupant_du_port(f"http://127.0.0.1:{port}").lower()
    assert transcription_locale.occupant_du_port(f"http://127.0.0.1:{port}") == ""
