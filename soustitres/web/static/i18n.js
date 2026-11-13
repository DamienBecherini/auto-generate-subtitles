/* Traductions de la page. Les catalogues sont ceux du serveur (soustitres/locales/<code>.json, servis par /locales/) :
   mêmes clés, paramètres {nom}, pluriels par catégorie CLDR (one, few, many, other) choisie d'après le paramètre n.
   Langue : réglage « langue_interface » s'il est fixé, sinon celle du navigateur, sinon l'anglais.
   Dans le HTML : data-i18n (texte), data-i18n-html (texte avec balises, catalogue de confiance), data-i18n-title,
   data-i18n-placeholder, data-i18n-aria-label. Dans le code : T("clé", { paramètres }). */
"use strict";

const I18N = { langue: "en", textes: {}, repli: {}, disponibles: ["en"], reglage: "" };

function choisirLangue(voulues, dispo) {
  for (const v of voulues) {
    if (!v) continue;
    const c = String(v).toLowerCase();
    if (dispo.includes(c)) return c;
    if (dispo.includes(c.split("-")[0])) return c.split("-")[0];
  }
  return dispo.includes("en") ? "en" : dispo[0] || "en";
}

async function chargerTraductions() {
  const lire = async (code) => {
    try { const r = await fetch(`/locales/${code}.json`); return r.ok ? await r.json() : {}; } catch (e) { return {}; }
  };
  try {
    const d = await (await fetch("/api/langues")).json();
    I18N.disponibles = d.disponibles?.length ? d.disponibles : ["en"];
    I18N.reglage = d.reglage || "";
  } catch (e) { /* service injoignable : la page le signalera ; textes de repli */ }
  I18N.langue = choisirLangue([I18N.reglage, ...(navigator.languages || [navigator.language])], I18N.disponibles);
  [I18N.textes, I18N.repli] = await Promise.all([lire(I18N.langue), I18N.langue === "en" ? {} : lire("en")]);
  document.documentElement.lang = I18N.langue;
  document.title = T("ui.title");
  traduire(document);
}

function T(cle, p = {}) {
  let v = I18N.textes[cle] ?? I18N.repli[cle];
  if (v && typeof v === "object") v = v[new Intl.PluralRules(I18N.langue).select(Number(p.n) || 0)] ?? v.other;
  if (v === undefined || v === null) return cle;
  return String(v).replace(/\{(\w+)\}/g, (m, k) => (k in p ? String(p[k]) : m));
}

/* Libellé d'un code d'état (« en_cours ») : clé préfixe + code, sinon le code lui-même. */
function etiquette(prefixe, code) {
  const v = T(prefixe + code);
  return v === prefixe + code ? code : v;
}

const ATTRIBUTS_I18N = [["title", "i18nTitle", "data-i18n-title"], ["placeholder", "i18nPlaceholder", "data-i18n-placeholder"],
                        ["aria-label", "i18nAriaLabel", "data-i18n-aria-label"]];

function traduire(racine) {
  for (const el of racine.querySelectorAll("[data-i18n]")) el.textContent = T(el.dataset.i18n);
  for (const el of racine.querySelectorAll("[data-i18n-html]")) el.innerHTML = T(el.dataset.i18nHtml);
  for (const [attr, cle, selecteur] of ATTRIBUTS_I18N)
    for (const el of racine.querySelectorAll(`[${selecteur}]`)) el.setAttribute(attr, T(el.dataset[cle]));
  for (const tpl of racine.querySelectorAll("template")) traduire(tpl.content);
}

/* Nom d'une langue dans la langue de la page (« anglais », « English ») ; le navigateur les connaît toutes. */
function nomLangue(code) {
  try { return new Intl.DisplayNames([I18N.langue], { type: "language" }).of(code) || code; } catch (e) { return code; }
}

/* Nom d'une langue dans cette langue même (« Français », « English ») : sélecteur de langue de l'interface. */
function autonyme(code) {
  try { const n = new Intl.DisplayNames([code], { type: "language" }).of(code) || code; return n[0].toLocaleUpperCase(code) + n.slice(1); }
  catch (e) { return code; }
}

const nombre = (n, options) => Number(n).toLocaleString(I18N.langue, options);
