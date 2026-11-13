"""Cost estimate of a translation before running it: requests, tokens, price per Claude model, duration locally.

Constants in `[estimation]` of the configuration. Values measured on 08/11/2026 on two 30 min videos translated
end to end (494 and 562 English subtitles, chunks of 25 lines, Sonnet 5.5 effort low): 36 input tokens and 23 output
tokens per subtitle; Qwen3.6-35B-A3B locally: 0.6 s per subtitle (331 lines in 197 s). Prices in dollars per million
tokens (input, output), Anthropic table of 06/10/2026.
"""
import math

from . import config, i18n

DEFAULTS = {
    "input_tokens_per_line": 36,
    "output_tokens_per_line": 23,
    "local_seconds_per_line": 0.6,
    "prices": {"claude-opus-5-5": [4.0, 20.0], "claude-sonnet-5-5": [2.0, 10.0], "claude-haiku-5-5": [0.10, 0.50]},
}


def settings(cfg: dict) -> dict:
    r = {**DEFAULTS, **(cfg.get("estimation") or {})}
    r["prices"] = {**DEFAULTS["prices"], **((cfg.get("estimation") or {}).get("prices") or {})}
    return r


def model_price(cfg: dict, model: str) -> tuple[float, float] | None:
    """(input, output) in $/MTok; a name without the exact version is matched by prefix (claude-sonnet-5-5-20261001…)."""
    table = settings(cfg)["prices"]
    if model in table:
        return tuple(table[model])
    for name, p in sorted(table.items(), key=lambda kv: -len(kv[0])):
        if model.startswith(name):
            return tuple(p)
    return None


def cost_usd(cfg: dict, model: str, input_tokens: int, output_tokens: int) -> float | None:
    p = model_price(cfg, model)
    if not p:
        return None
    return round(input_tokens * p[0] / 1e6 + output_tokens * p[1] / 1e6, 4)


def estimate(cfg: dict, line_count: int, languages: list[str], source_language: str) -> dict:
    """For `line_count` subtitles to translate into `languages` (the source language is skipped): requests, tokens
    and, for each configured translator, the cost (Claude) or the duration (local)."""
    r = settings(cfg)
    targets = [c for c in languages if c != source_language]
    chunk = max(1, int((cfg.get("translation") or {}).get("chunk_size", 25)))
    n = line_count * len(targets)
    e = {"count": line_count, "languages": targets, "requests": math.ceil(line_count / chunk) * len(targets),
         "input_tokens": round(n * float(r["input_tokens_per_line"])), "output_tokens": round(n * float(r["output_tokens_per_line"])),
         "translators": []}
    for m in cfg.get("translators", []):
        t = {"id": m.get("id"), "name": config.engine_name(m), "type": m.get("type", "openai"), "model": m.get("model", "")}
        if t["type"] == "claude":
            t["cost_usd"] = cost_usd(cfg, t["model"], e["input_tokens"], e["output_tokens"])
        elif t["type"] == "openai":
            t["duration_s"] = round(n * float(r["local_seconds_per_line"]))
            t["cost_usd"] = 0.0 if is_local(m) else None
        else:
            t["cost_usd"] = 0.0
        e["translators"].append(t)
    return e


def is_local(m: dict) -> bool:
    """Translator that runs on this machine (no cost): loopback URL."""
    url = (m.get("url") or "").lower()
    return m.get("type") == "openai" and any(h in url for h in ("127.0.0.1", "localhost", "[::1]"))


def pause_by_default(m: dict) -> bool:
    """Pause before translation to see the estimate: yes except for a local AI (free) or no translation."""
    t = m.get("type", "openai")
    return not (t == "none" or is_local(m))


def text(e: dict, cfg: dict | None = None) -> str:
    """Readable summary for the log."""
    if not e["languages"]:
        return i18n.t("est.nothing")
    parts = [i18n.t("est.summary", n=e["count"], langs=len(e["languages"]), requests=e["requests"], tokens_in=e["input_tokens"], tokens_out=e["output_tokens"])]
    for t in e["translators"]:
        if t["type"] == "claude":
            parts.append("  " + i18n.t("est.line", name=f"{t['name']} ({t['model']})", detail=f"≈ {t['cost_usd']:.2f} $" if t.get("cost_usd") is not None else i18n.t("est.unknown_price")))
        elif t["type"] == "openai":
            parts.append("  " + i18n.t("est.line", name=t["name"], detail=f"≈ {t['duration_s'] // 60} min {t['duration_s'] % 60:02d} s") + (" (" + i18n.t("est.free") + ")" if t.get("cost_usd") == 0 else ""))
    return "\n".join(parts)
