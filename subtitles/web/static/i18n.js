/* Translations of the page. The catalogs are the server's (subtitles/locales/<code>.json, served by /locales/):
   same keys, {name} parameters, plurals by CLDR category (one, few, many, other) chosen from the parameter n.
   Language: the "ui_language" setting if it is set, otherwise the browser's, otherwise English.
   In the HTML: data-i18n (text), data-i18n-html (text with tags, trusted catalog), data-i18n-title,
   data-i18n-placeholder, data-i18n-aria-label. In the code: T("key", { parameters }). */
"use strict";

const I18N = { language: "en", texts: {}, fallback: {}, available: ["en"], setting: "", titleKey: "ui.title" };

function pickLanguage(wanted, available) {
  for (const v of wanted) {
    if (!v) continue;
    const c = String(v).toLowerCase();
    if (available.includes(c)) return c;
    if (available.includes(c.split("-")[0])) return c.split("-")[0];
  }
  return available.includes("en") ? "en" : available[0] || "en";
}

async function readCatalog(code) {
  try { const r = await fetch(`/locales/${code}.json`); return r.ok ? await r.json() : {}; } catch (e) { return {}; }
}

async function loadTranslations() {
  try {
    const d = await (await fetch("/api/languages")).json();
    I18N.available = d.available?.length ? d.available : ["en"];
    I18N.setting = d.setting || "";
  } catch (e) { /* service unreachable: the page will say so; fallback texts */ }
  await useLanguage(I18N.setting);
}

/* Language chosen ("" = the browser's): catalogs loaded, then the fixed texts of the page translated in place.
   The texts built by the script are redone by the page itself (app.js, retranslate). */
async function useLanguage(setting) {
  const code = pickLanguage([setting, ...(navigator.languages || [navigator.language])], I18N.available);
  const [texts, fallback] = await Promise.all([readCatalog(code), code === "en" ? {} : readCatalog("en")]);
  Object.assign(I18N, { setting: setting || "", language: code, texts, fallback });
  document.documentElement.lang = code;
  document.title = T(I18N.titleKey);
  translatePage(document);
}

function T(key, p = {}) {
  let v = I18N.texts[key] ?? I18N.fallback[key];
  if (v && typeof v === "object") v = v[new Intl.PluralRules(I18N.language).select(Number(p.n) || 0)] ?? v.other;
  if (v === undefined || v === null) return key;
  return String(v).replace(/\{(\w+)\}/g, (m, k) => (k in p ? String(p[k]) : m));
}

/* Label of a state code ("running"): key prefix + code, otherwise the code itself. */
function label(prefix, code) {
  const v = T(prefix + code);
  return v === prefix + code ? code : v;
}

const I18N_ATTRIBUTES = [["title", "i18nTitle", "data-i18n-title"], ["placeholder", "i18nPlaceholder", "data-i18n-placeholder"],
                         ["aria-label", "i18nAriaLabel", "data-i18n-aria-label"]];

function translatePage(root) {
  for (const el of root.querySelectorAll("[data-i18n]")) el.textContent = T(el.dataset.i18n);
  for (const el of root.querySelectorAll("[data-i18n-html]")) el.innerHTML = T(el.dataset.i18nHtml);
  for (const [attr, key, selector] of I18N_ATTRIBUTES)
    for (const el of root.querySelectorAll(`[${selector}]`)) el.setAttribute(attr, T(el.dataset[key]));
  for (const tpl of root.querySelectorAll("template")) translatePage(tpl.content);
}

/* Name of a language in the language of the page ("anglais", "English"); the browser knows them all. */
function languageName(code) {
  try { return new Intl.DisplayNames([I18N.language], { type: "language" }).of(code) || code; } catch (e) { return code; }
}

/* Name of a language in that language itself ("Français", "English"): interface language selector. */
function autonym(code) {
  try { const n = new Intl.DisplayNames([code], { type: "language" }).of(code) || code; return n[0].toLocaleUpperCase(code) + n.slice(1); }
  catch (e) { return code; }
}

const formatNumber = (n, options) => Number(n).toLocaleString(I18N.language, options);
