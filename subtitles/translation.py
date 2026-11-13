"""Translation of the subtitles (into French or any other target language) in numbered chunks, keeping the timing.

Engines ("translators" entries of the configuration, `type` field):
  openai   OpenAI-compatible API: llama-server of the PC (started by the supervision if needed), Ollama, OpenAI, Mistral…
  claude   Anthropic API through the official SDK (key in the environment, never displayed)
  none     no translation
Each subtitle is a numbered line; the answer must give back the same numbers. An incomplete chunk is sent again
once, then split into smaller chunks; as a last resort the line stays in its language.
The instructions sent to the model are written in French.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

from . import config, i18n, language

CLAUDE_CONCURRENCY = 5
MAX_CONCURRENCY = 20

SYSTEM_PROMPT = """Tu es un traducteur professionnel de sous-titres vidéo. Tu traduis en {langue}.
Règles :
- Une ligne numérotée en entrée donne exactement une ligne numérotée en sortie, avec le même numéro, dans le même ordre. Ne fusionne jamais deux lignes, n'en découpe aucune, n'en saute aucune.
- Les lignes sont des fragments consécutifs d'un même discours parlé : sers-toi des lignes voisines (et du contexte déjà traduit) pour le sens et les accords, mais chaque ligne doit rester compréhensible à sa place.
- Traduction naturelle et concise, registre de l'oral : un sous-titre se lit en quelques secondes. Reste proche de la longueur de l'original, jamais plus de 20 % plus long.
- Garde les noms propres, les chiffres, les noms de produits et les termes techniques d'usage courant en {langue}.
- Pas de commentaire, pas d'explication, pas de guillemets ajoutés : uniquement les lignes numérotées « N. texte »."""

_LINE = re.compile(r"^\s*(\d+)\s*[.):\-–]\s*(.*)$")


def instructions(target: str = "fr", terms: list[str] | None = None) -> str:
    """System instructions for a target language (ISO code), with the validated names for this video."""
    text = SYSTEM_PROMPT.format(langue=language.name(target))
    if terms:
        text += "\n\nNoms à respecter (« source → forme à employer » ; sans flèche, la forme usuelle dans la langue cible) :\n" + "\n".join("- " + t for t in terms)
    return text


class TranslationError(Exception):
    pass


def format_chunk(texts: list[str], context: list[str]) -> str:
    parts = []
    if context:
        parts.append("Contexte déjà traduit (ne pas le reprendre dans la réponse) :\n" + "\n".join(context))
    parts.append("Lignes à traduire :\n" + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts)))
    return "\n\n".join(parts)


def parse_answer(answer: str, expected: int) -> dict[int, str]:
    """Numbers → text; a line without a number continues the previous one (line break by the model)."""
    result: dict[int, str] = {}
    current = None
    for line in answer.splitlines():
        m = _LINE.match(line)
        if m and 1 <= int(m.group(1)) <= expected and (current is None or int(m.group(1)) > current):
            current = int(m.group(1))
            result[current] = m.group(2).strip()
        elif current is not None and line.strip():
            result[current] = (result[current] + " " + line.strip()).strip()
    return {k: v for k, v in result.items() if v}


def _json(url, body=None, key="", timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET",
                                 headers={"Content-Type": "application/json"})
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


class OpenAITranslator:
    """OpenAI-compatible API. With `supervision`, starts the local AI of the PC if it is stopped (local AI module),
    and stops it at the end if `stop_after` and if we started it."""

    def __init__(self, m: dict, log=print):
        self.url = m["url"].rstrip("/")
        self.model = m.get("model", "")
        self.supervision = (m.get("supervision") or "").rstrip("/")
        self.stop_after = bool(m.get("stop_after", False))
        self.key_reference = m.get("key", "")
        self.key = ""
        self.log = log
        self.started_here = False

    def _responds(self) -> bool:
        try:
            _json(self.url + "/v1/models", key=self.key, timeout=5)
            return True
        except urllib.error.HTTPError as e:
            if e.code in (401, 403) and not self.key and self.key_reference:
                self.key = config.resolve_key(self.key_reference)
                return self._responds()
            return False
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def prepare(self, wait_s: int = 300):
        if self._responds():
            return
        if not self.supervision:
            raise TranslationError(i18n.t("err.llm_no_response", url=self.url))
        try:
            ai = _json(self.supervision + "/api/ai")
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise TranslationError(i18n.t("err.llm_and_supervision_down", url=self.url, supervision=self.supervision, error=e)) from e
        if ai.get("state") in ("stopped", "error", "safety_stop"):
            self.log(i18n.t("log.local_ai_starting"))
            _json(self.supervision + "/api/ai/start", {}, timeout=30)
            self.started_here = True
        start = time.time()
        while time.time() - start < wait_s:
            time.sleep(3)
            if self._responds():
                self.log(i18n.t("log.local_ai_ready", s=round(time.time() - start)))
                return
        raise TranslationError(i18n.t("err.local_ai_timeout"))

    def complete(self, system: str, request: str) -> str:
        body = {"model": self.model, "temperature": 0.2, "max_tokens": 4096, "stream": False,
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": request}]}
        try:
            r = _json(self.url + "/v1/chat/completions", body, key=self.key, timeout=900)
        except urllib.error.HTTPError as e:
            raise TranslationError(f"{self.url} : {e.code} {e.read().decode(errors='replace')[:300]}") from e
        except (urllib.error.URLError, OSError) as e:
            raise TranslationError(i18n.t("err.url_unreachable", url=self.url, error=e)) from e
        text = r["choices"][0]["message"]["content"] or ""
        return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()

    def finish(self):
        if self.started_here and self.stop_after and self.supervision:
            try:
                _json(self.supervision + "/api/ai/stop", {}, timeout=60)
                self.log(i18n.t("log.local_ai_stopped"))
            except (urllib.error.URLError, OSError, ValueError) as e:
                self.log(i18n.t("log.local_ai_stop_failed", error=e))


class ClaudeTranslator:
    """Anthropic API through the official SDK (pip install anthropic, in the .venv of the repository)."""

    def __init__(self, m: dict, log=print):
        try:
            import anthropic
        except ImportError as e:
            raise TranslationError(i18n.t("err.anthropic_sdk_missing")) from e
        key = config.resolve_key(m.get("key", "")) or os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            raise TranslationError(i18n.t("err.anthropic_key_missing"))
        self.anthropic = anthropic
        # Several videos at once can reach the rate limit: the SDK waits and retries (`retry-after`).
        self.client = anthropic.Anthropic(api_key=key, max_retries=8)
        self.model = m.get("model", "claude-sonnet-5-5")
        self.effort = m.get("effort") or "low"
        self.log = log
        self.input_tokens = 0
        self.output_tokens = 0

    def prepare(self):
        pass

    def complete(self, system: str, request: str, schema: dict | None = None, effort: str | None = None) -> str:
        a = self.anthropic
        output = {"effort": effort or self.effort, **({"format": {"type": "json_schema", "schema": schema}} if schema else {})}
        try:
            # Server-side fallback when the classifiers refuse (routing by category, without a list of models).
            resp = self.client.beta.messages.create(
                model=self.model, max_tokens=16000, system=system,
                messages=[{"role": "user", "content": request}],
                output_config=output,
                betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        except a.RateLimitError as e:
            raise TranslationError(i18n.t("err.anthropic_rate_limit", s=e.response.headers.get("retry-after", "?"))) from e
        except a.APIStatusError as e:
            raise TranslationError(i18n.t("err.anthropic_status", code=e.status_code, detail=e.message)) from e
        except a.APIConnectionError as e:
            raise TranslationError(i18n.t("err.anthropic_unreachable", error=e)) from e
        if resp.stop_reason == "refusal":
            detail = getattr(resp, "stop_details", None)
            raise TranslationError(i18n.t("err.anthropic_refused", category=getattr(detail, "category", "?")))
        u = getattr(resp, "usage", None)
        if u:
            self.input_tokens += getattr(u, "input_tokens", 0) or 0
            self.output_tokens += getattr(u, "output_tokens", 0) or 0
        return "".join(b.text for b in resp.content if b.type == "text").strip()

    def finish(self):
        if self.input_tokens or self.output_tokens:
            from . import estimation
            cost = estimation.cost_usd(config.load(), self.model, self.input_tokens, self.output_tokens)
            self.log(i18n.t("log.anthropic_usage", model=self.model, tokens_in=self.input_tokens, tokens_out=self.output_tokens)
                     + (f" ≈ {cost:.2f} $" if cost is not None else ""))


def create(m: dict, log=print):
    t = m.get("type", "openai")
    if t == "openai":
        return OpenAITranslator(m, log)
    if t == "claude":
        return ClaudeTranslator(m, log)
    raise TranslationError(i18n.t("err.unknown_translator_type", type=t))


def concurrency(m: dict) -> int:
    """Videos of a batch translated at once: `parallel` field of the translator, else 5 for Claude (remote API)
    and 1 for the others (a local AI handles only one)."""
    try:
        n = int(m.get("parallel") or (CLAUDE_CONCURRENCY if m.get("type") == "claude" else 1))
    except (TypeError, ValueError):
        n = 1
    return max(1, min(n, MAX_CONCURRENCY))


def test(m: dict) -> str:
    """Short exchange to check that a translator answers; returns its answer."""
    tr = create(m, lambda _msg: None)
    try:
        tr.prepare()
        return tr.complete("Réponds en un mot.", "Dis « prêt ».")[:80]
    finally:
        tr.finish()


def translate_texts(translator, texts: list[str], chunk_size: int = 25, log=print, cancelled=lambda: False,
                    target: str = "fr", terms: list[str] | None = None, progress=None) -> list[str]:
    """Translates in order into the `target` language; a line the model did not give back stays as it is (reported)."""
    system = instructions(target, terms)
    translator.prepare()
    result = [None] * len(texts)
    context: list[str] = []
    position = 0
    chunk = chunk_size
    while position < len(texts):
        if cancelled():
            raise TranslationError(i18n.t("err.cancelled"))
        piece = texts[position:position + chunk]
        got = _translate_chunk(translator, piece, context, system)
        missing = [i for i in range(len(piece)) if (i + 1) not in got]
        if missing and chunk > 1:
            chunk = max(1, chunk // 2)
            log("  " + i18n.t("log.chunk_incomplete", n=len(missing), size=chunk))
            continue
        for i, t in enumerate(piece):
            result[position + i] = got.get(i + 1, t)
            if (i + 1) not in got:
                log("  " + i18n.t("log.line_untranslated", line=position + i + 1, text=t[:60]))
        context = [f"{position + i + 1}. {result[position + i]}" for i in range(max(0, len(piece) - 2), len(piece))]
        position += len(piece)
        log("  " + i18n.t("log.translated_progress", done=position, total=len(texts)))
        if progress:
            progress(position, len(texts))
        chunk = min(chunk_size, chunk * 2) if chunk < chunk_size else chunk
    return result


def _translate_chunk(translator, piece: list[str], context: list[str], system: str) -> dict[int, str]:
    answer = translator.complete(system, format_chunk(piece, context))
    got = parse_answer(answer, len(piece))
    if len(got) < len(piece):
        reminder = format_chunk(piece, context) + (f"\n\nRappel : réponds avec exactement {len(piece)} lignes numérotées de 1 à "
                                                   f"{len(piece)}, une par ligne d'entrée.")
        got = {**parse_answer(translator.complete(system, reminder), len(piece)), **got}
    return got
