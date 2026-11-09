"""Traduction des sous-titres (vers le français ou toute autre langue cible) par lots numérotés, en conservant le découpage temporel.

Moteurs (entrée « traducteurs » de la configuration, champ `type`) :
  openai   API compatible OpenAI : llama-server du PC (démarré par la supervision si besoin), Ollama, OpenAI, Mistral…
  claude   API Anthropic par le SDK officiel (clé dans l'environnement, jamais affichée)
  aucun    pas de traduction
Chaque sous-titre est une ligne numérotée ; la réponse doit rendre les mêmes numéros. Un lot incomplet est
renvoyé une fois, puis découpé en lots plus petits ; en dernier recours la ligne reste dans sa langue.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

from . import config, langue

SIMULTANEES_CLAUDE = 5
SIMULTANEES_MAX = 20

SYSTEME = """Tu es un traducteur professionnel de sous-titres vidéo. Tu traduis en {langue}.
Règles :
- Une ligne numérotée en entrée donne exactement une ligne numérotée en sortie, avec le même numéro, dans le même ordre. Ne fusionne jamais deux lignes, n'en découpe aucune, n'en saute aucune.
- Les lignes sont des fragments consécutifs d'un même discours parlé : sers-toi des lignes voisines (et du contexte déjà traduit) pour le sens et les accords, mais chaque ligne doit rester compréhensible à sa place.
- Traduction naturelle et concise, registre de l'oral : un sous-titre se lit en quelques secondes. Reste proche de la longueur de l'original, jamais plus de 20 % plus long.
- Garde les noms propres, les chiffres, les noms de produits et les termes techniques d'usage courant en {langue}.
- Pas de commentaire, pas d'explication, pas de guillemets ajoutés : uniquement les lignes numérotées « N. texte »."""

_LIGNE = re.compile(r"^\s*(\d+)\s*[.):\-–]\s*(.*)$")


def consigne(cible: str = "fr", termes: list[str] | None = None) -> str:
    """Consigne système pour une langue cible (code ISO), avec les noms validés pour cette vidéo."""
    texte = SYSTEME.format(langue=langue.nom(cible))
    if termes:
        texte += "\n\nNoms à respecter (« source → forme à employer » ; sans flèche, la forme usuelle dans la langue cible) :\n" + "\n".join("- " + t for t in termes)
    return texte


class ErreurTraduction(Exception):
    pass


def formater_lot(textes: list[str], contexte: list[str]) -> str:
    morceaux = []
    if contexte:
        morceaux.append("Contexte déjà traduit (ne pas le reprendre dans la réponse) :\n" + "\n".join(contexte))
    morceaux.append("Lignes à traduire :\n" + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(textes)))
    return "\n\n".join(morceaux)


def analyser_reponse(reponse: str, attendu: int) -> dict[int, str]:
    """Numéros → texte ; une ligne sans numéro prolonge la précédente (retour à la ligne du modèle)."""
    resultat: dict[int, str] = {}
    courant = None
    for ligne in reponse.splitlines():
        m = _LIGNE.match(ligne)
        if m and 1 <= int(m.group(1)) <= attendu and (courant is None or int(m.group(1)) > courant):
            courant = int(m.group(1))
            resultat[courant] = m.group(2).strip()
        elif courant is not None and ligne.strip():
            resultat[courant] = (resultat[courant] + " " + ligne.strip()).strip()
    return {k: v for k, v in resultat.items() if v}


def _json(url, corps=None, cle="", delai=10):
    donnees = json.dumps(corps).encode() if corps is not None else None
    req = urllib.request.Request(url, data=donnees, method="POST" if donnees is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    if cle:
        req.add_header("Authorization", f"Bearer {cle}")
    with urllib.request.urlopen(req, timeout=delai) as rep:
        return json.load(rep)


class TraducteurOpenAI:
    """API compatible OpenAI. Avec `supervision`, démarre l'IA locale du PC si elle est arrêtée (module IA locale),
    et l'arrête à la fin si `arreter_apres` et que c'est nous qui l'avons démarrée."""

    def __init__(self, m: dict, journal=print):
        self.url = m["url"].rstrip("/")
        self.modele = m.get("modele", "")
        self.supervision = (m.get("supervision") or "").rstrip("/")
        self.arreter_apres = bool(m.get("arreter_apres", False))
        self.reference_cle = m.get("cle", "")
        self.cle = ""
        self.journal = journal
        self.demarree_ici = False

    def _repond(self) -> bool:
        try:
            _json(self.url + "/v1/models", cle=self.cle, delai=5)
            return True
        except urllib.error.HTTPError as e:
            if e.code in (401, 403) and not self.cle and self.reference_cle:
                self.cle = config.resoudre_cle(self.reference_cle)
                return self._repond()
            return False
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def preparer(self, attente_s: int = 300):
        if self._repond():
            return
        if not self.supervision:
            raise ErreurTraduction(f"le serveur ne répond pas sur {self.url}")
        try:
            etat = _json(self.supervision + "/api/ia")
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise ErreurTraduction(f"ni le serveur ({self.url}) ni la supervision ({self.supervision}) ne répondent : {e}") from e
        if etat.get("etat") in ("arrete", "erreur", "arret_securite"):
            self.journal("IA locale arrêtée : démarrage par la supervision…")
            _json(self.supervision + "/api/ia/demarrer", {}, delai=30)
            self.demarree_ici = True
        debut = time.time()
        while time.time() - debut < attente_s:
            time.sleep(3)
            if self._repond():
                self.journal(f"IA locale prête ({time.time() - debut:.0f} s).")
                return
        raise ErreurTraduction("l'IA locale n'a pas démarré à temps (voir le module IA locale de la page Nouvel onglet)")

    def completer(self, consigne: str, demande: str) -> str:
        corps = {"model": self.modele, "temperature": 0.2, "max_tokens": 4096, "stream": False,
                 "messages": [{"role": "system", "content": consigne}, {"role": "user", "content": demande}]}
        try:
            r = _json(self.url + "/v1/chat/completions", corps, cle=self.cle, delai=900)
        except urllib.error.HTTPError as e:
            raise ErreurTraduction(f"{self.url} : {e.code} {e.read().decode(errors='replace')[:300]}") from e
        except (urllib.error.URLError, OSError) as e:
            raise ErreurTraduction(f"{self.url} injoignable : {e}") from e
        texte = r["choices"][0]["message"]["content"] or ""
        return re.sub(r"<think>.*?</think>", "", texte, flags=re.S).strip()

    def terminer(self):
        if self.demarree_ici and self.arreter_apres and self.supervision:
            try:
                _json(self.supervision + "/api/ia/arreter", {}, delai=60)
                self.journal("IA locale arrêtée (elle avait été démarrée pour cette tâche).")
            except (urllib.error.URLError, OSError, ValueError) as e:
                self.journal(f"IA locale : arrêt impossible ({e})")


class TraducteurClaude:
    """API Anthropic par le SDK officiel (pip install anthropic, dans le .venv du dépôt)."""

    def __init__(self, m: dict, journal=print):
        try:
            import anthropic
        except ImportError as e:
            raise ErreurTraduction("SDK anthropic absent : .venv/Scripts/pip install anthropic") from e
        cle = config.resoudre_cle(m.get("cle", "")) or os.environ.get("ANTHROPIC_API_KEY", "")
        if not cle:
            raise ErreurTraduction("clé Anthropic introuvable (référence « cle » de la configuration)")
        self.anthropic = anthropic
        # Plusieurs vidéos à la fois peuvent atteindre la limite de débit : le SDK attend et réessaie (`retry-after`).
        self.client = anthropic.Anthropic(api_key=cle, max_retries=8)
        self.modele = m.get("modele", "claude-sonnet-5-5")
        self.effort = m.get("effort") or "low"
        self.journal = journal
        self.jetons_entree = 0
        self.jetons_sortie = 0

    def preparer(self):
        pass

    def completer(self, consigne: str, demande: str, schema: dict | None = None, effort: str | None = None) -> str:
        a = self.anthropic
        sortie = {"effort": effort or self.effort, **({"format": {"type": "json_schema", "schema": schema}} if schema else {})}
        try:
            # Repli serveur en cas de refus par les classificateurs (routage par catégorie, sans liste de modèles).
            rep = self.client.beta.messages.create(
                model=self.modele, max_tokens=16000, system=consigne,
                messages=[{"role": "user", "content": demande}],
                output_config=sortie,
                betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        except a.RateLimitError as e:
            raise ErreurTraduction(f"API Anthropic : limite de débit ({e.response.headers.get('retry-after', '?')} s)") from e
        except a.APIStatusError as e:
            raise ErreurTraduction(f"API Anthropic {e.status_code} : {e.message}") from e
        except a.APIConnectionError as e:
            raise ErreurTraduction(f"API Anthropic injoignable : {e}") from e
        if rep.stop_reason == "refusal":
            detail = getattr(rep, "stop_details", None)
            raise ErreurTraduction(f"API Anthropic : requête refusée ({getattr(detail, 'category', '?')})")
        u = getattr(rep, "usage", None)
        if u:
            self.jetons_entree += getattr(u, "input_tokens", 0) or 0
            self.jetons_sortie += getattr(u, "output_tokens", 0) or 0
        return "".join(b.text for b in rep.content if b.type == "text").strip()

    def terminer(self):
        if self.jetons_entree or self.jetons_sortie:
            from . import estimation
            cout = estimation.cout_usd(config.charger(), self.modele, self.jetons_entree, self.jetons_sortie)
            self.journal(f"API Anthropic ({self.modele}) : {self.jetons_entree} jetons d'entrée, {self.jetons_sortie} de sortie"
                         + (f" ≈ {cout:.2f} $" if cout is not None else ""))


def creer(m: dict, journal=print):
    t = m.get("type", "openai")
    if t == "openai":
        return TraducteurOpenAI(m, journal)
    if t == "claude":
        return TraducteurClaude(m, journal)
    raise ErreurTraduction(f"type de traducteur inconnu : {t}")


def simultanees(m: dict) -> int:
    """Vidéos d'un lot traduites en même temps : champ `parallele` du traducteur, sinon 5 pour Claude (API distante)
    et 1 pour les autres (une IA locale n'en traite qu'une)."""
    try:
        n = int(m.get("parallele") or (SIMULTANEES_CLAUDE if m.get("type") == "claude" else 1))
    except (TypeError, ValueError):
        n = 1
    return max(1, min(n, SIMULTANEES_MAX))


def tester(m: dict) -> str:
    """Petit échange pour vérifier qu'un traducteur répond ; retourne sa réponse."""
    tr = creer(m, lambda _msg: None)
    try:
        tr.preparer()
        return tr.completer("Réponds en un mot.", "Dis « prêt ».")[:80]
    finally:
        tr.terminer()


def traduire_textes(traducteur, textes: list[str], taille_lot: int = 25, journal=print, annule=lambda: False,
                    cible: str = "fr", termes: list[str] | None = None) -> list[str]:
    """Traduit dans l'ordre vers la langue `cible` ; une ligne que le modèle n'a pas rendue reste telle quelle (signalée)."""
    systeme = consigne(cible, termes)
    traducteur.preparer()
    resultat = [None] * len(textes)
    contexte: list[str] = []
    position = 0
    lot = taille_lot
    while position < len(textes):
        if annule():
            raise ErreurTraduction("annulée")
        morceau = textes[position:position + lot]
        obtenu = _traduire_lot(traducteur, morceau, contexte, systeme)
        manquants = [i for i in range(len(morceau)) if (i + 1) not in obtenu]
        if manquants and lot > 1:
            lot = max(1, lot // 2)
            journal(f"  lot incomplet ({len(manquants)} ligne(s) manquante(s)) : lots de {lot}")
            continue
        for i, t in enumerate(morceau):
            resultat[position + i] = obtenu.get(i + 1, t)
            if (i + 1) not in obtenu:
                journal(f"  ligne {position + i + 1} non traduite, gardée telle quelle : {t[:60]}")
        contexte = [f"{position + i + 1}. {resultat[position + i]}" for i in range(max(0, len(morceau) - 2), len(morceau))]
        position += len(morceau)
        journal(f"  {position}/{len(textes)} sous-titres traduits")
        lot = min(taille_lot, lot * 2) if lot < taille_lot else lot
    return resultat


def _traduire_lot(traducteur, morceau: list[str], contexte: list[str], systeme: str) -> dict[int, str]:
    reponse = traducteur.completer(systeme, formater_lot(morceau, contexte))
    obtenu = analyser_reponse(reponse, len(morceau))
    if len(obtenu) < len(morceau):
        rappel = formater_lot(morceau, contexte) + (f"\n\nRappel : réponds avec exactement {len(morceau)} lignes numérotées de 1 à "
                                                    f"{len(morceau)}, une par ligne d'entrée.")
        obtenu = {**analyser_reponse(traducteur.completer(systeme, rappel), len(morceau)), **obtenu}
    return obtenu
