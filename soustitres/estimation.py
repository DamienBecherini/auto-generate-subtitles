"""Estimation du coût d'une traduction avant de la lancer : requêtes, jetons, prix par modèle Claude, durée en local.

Constantes dans `[estimation]` de la configuration. Valeurs mesurées le 08/11/2026 sur deux vidéos de 30 min traduites
de bout en bout (494 et 562 sous-titres anglais, lots de 25 lignes, Sonnet 5.5 effort low) : 36 jetons d'entrée et 23 jetons
de sortie par sous-titre ; Qwen3.6-35B-A3B en local : 0,6 s par sous-titre (331 lignes en 197 s). Prix en dollars par million de
jetons (entrée, sortie), table Anthropic du 06/10/2026.
"""
import math

from . import config, i18n

DEFAUTS = {
    "jetons_entree_par_ligne": 36,
    "jetons_sortie_par_ligne": 23,
    "secondes_par_ligne_local": 0.6,
    "prix": {"claude-opus-5-5": [4.0, 20.0], "claude-sonnet-5-5": [2.0, 10.0], "claude-haiku-5-5": [0.10, 0.50]},
}


def reglages(cfg: dict) -> dict:
    r = {**DEFAUTS, **(cfg.get("estimation") or {})}
    r["prix"] = {**DEFAUTS["prix"], **((cfg.get("estimation") or {}).get("prix") or {})}
    return r


def prix_modele(cfg: dict, modele: str) -> tuple[float, float] | None:
    """(entrée, sortie) en $/MTok ; un nom sans la version exacte est rapproché par préfixe (claude-sonnet-5-5-20261001…)."""
    table = reglages(cfg)["prix"]
    if modele in table:
        return tuple(table[modele])
    for nom, p in sorted(table.items(), key=lambda kv: -len(kv[0])):
        if modele.startswith(nom):
            return tuple(p)
    return None


def cout_usd(cfg: dict, modele: str, jetons_entree: int, jetons_sortie: int) -> float | None:
    p = prix_modele(cfg, modele)
    if not p:
        return None
    return round(jetons_entree * p[0] / 1e6 + jetons_sortie * p[1] / 1e6, 4)


def estimer(cfg: dict, nb_lignes: int, langues: list[str], langue_source: str) -> dict:
    """Pour `nb_lignes` sous-titres à traduire vers `langues` (la langue source est sautée) : requêtes, jetons et,
    pour chaque traducteur configuré, le coût (Claude) ou la durée (local)."""
    r = reglages(cfg)
    cibles = [c for c in langues if c != langue_source]
    lot = max(1, int((cfg.get("traduction") or {}).get("lot", 25)))
    n = nb_lignes * len(cibles)
    e = {"nb": nb_lignes, "langues": cibles, "requetes": math.ceil(nb_lignes / lot) * len(cibles),
         "jetons_entree": round(n * float(r["jetons_entree_par_ligne"])), "jetons_sortie": round(n * float(r["jetons_sortie_par_ligne"])),
         "traducteurs": []}
    for m in cfg.get("traducteurs", []):
        t = {"id": m.get("id"), "nom": config.nom_moteur(m), "type": m.get("type", "openai"), "modele": m.get("modele", "")}
        if t["type"] == "claude":
            t["cout_usd"] = cout_usd(cfg, t["modele"], e["jetons_entree"], e["jetons_sortie"])
        elif t["type"] == "openai":
            t["duree_s"] = round(n * float(r["secondes_par_ligne_local"]))
            t["cout_usd"] = 0.0 if est_local(m) else None
        else:
            t["cout_usd"] = 0.0
        e["traducteurs"].append(t)
    return e


def est_local(m: dict) -> bool:
    """Traducteur qui tourne sur cette machine (pas de coût) : URL en boucle locale."""
    url = (m.get("url") or "").lower()
    return m.get("type") == "openai" and any(h in url for h in ("127.0.0.1", "localhost", "[::1]"))


def pause_par_defaut(m: dict) -> bool:
    """Pause avant la traduction pour voir l'estimation : oui sauf pour une IA locale (gratuite) ou pas de traduction."""
    t = m.get("type", "openai")
    return not (t == "aucun" or est_local(m))


def texte(e: dict, cfg: dict | None = None) -> str:
    """Résumé lisible pour le journal."""
    if not e["langues"]:
        return i18n.t("est.nothing")
    morceaux = [i18n.t("est.summary", n=e["nb"], langs=len(e["langues"]), requests=e["requetes"], tokens_in=e["jetons_entree"], tokens_out=e["jetons_sortie"])]
    for t in e["traducteurs"]:
        if t["type"] == "claude":
            morceaux.append("  " + i18n.t("est.line", name=f"{t['nom']} ({t['modele']})", detail=f"≈ {t['cout_usd']:.2f} $" if t.get("cout_usd") is not None else i18n.t("est.unknown_price")))
        elif t["type"] == "openai":
            morceaux.append("  " + i18n.t("est.line", name=t["nom"], detail=f"≈ {t['duree_s'] // 60} min {t['duree_s'] % 60:02d} s") + (" (" + i18n.t("est.free") + ")" if t.get("cout_usd") == 0 else ""))
    return "\n".join(morceaux)
