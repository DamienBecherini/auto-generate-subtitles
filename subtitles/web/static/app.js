/* Interface of the subtitle generator: one task at a time, followed by regular polling of the API. */
"use strict";

const $ = (s) => document.querySelector(s);
let config = null;
let currentTask = null;       // id of the displayed task
let logRead = 0;
let timer = null;

async function api(method, path, body) {
  const r = await fetch(path, { method, headers: { "X-Language": I18N.language, ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
                                body: body !== undefined ? JSON.stringify(body) : undefined });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(d.error || `${r.status} ${r.statusText}`); e.data = d; throw e; }
  return d;
}

/* "My server (details…)" becomes "My server" in the header; the full name stays on hover. */
/* Displayed name of an engine: its name, otherwise a name translated from its id (engines provided by default). */
function engineName(m) { return m?.name || (m ? label("engine.", m.id) : "?"); }
function shortName(m) { return engineName(m).replace(/\s*\(.*$/, "") || engineName(m); }

function engine(list, id) { return (config?.[list] || []).find((m) => m.id === id); }

// --- Light or dark theme (choice remembered in this browser, otherwise the system's) ------------------------------

function currentTheme() {
  return document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}
function updateThemeButton() {
  const dark = currentTheme() === "dark", b = $("#btn-theme");
  b.textContent = dark ? "☀" : "☾";
  b.title = dark ? T("ui.theme.to_light") : T("ui.theme.to_dark");
  b.setAttribute("aria-label", b.title);
}
$("#btn-theme").addEventListener("click", () => {
  const t = currentTheme() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("theme", t); } catch (e) { /* private browsing: valid for this page only */ }
  updateThemeButton();
});
// --- Scrolling zones: shadow and arrow as long as content remains --------------------------------------------------

function watchScrolling(el) {
  const zone = document.createElement("div"); zone.className = "scroll-zone";
  el.parentNode.insertBefore(zone, el);
  zone.appendChild(el);
  const createArrow = (className, text, title, direction) => {
    const b = document.createElement("button");
    b.type = "button"; b.className = className; b.textContent = text; b.hidden = true; b.title = title;
    b.addEventListener("click", () => el.scrollBy({ top: direction * el.clientHeight * 0.8, behavior: "smooth" }));
    zone.appendChild(b);
    return b;
  };
  const up = createArrow("scroll-arrow up", "▴", T("ui.scroll_up"), -1), down = createArrow("scroll-arrow", "▾", T("ui.scroll_down"), 1);
  let planned = false;
  const update = () => {
    planned = false;
    const top = el.scrollTop > 2, bottom = el.scrollTop + el.clientHeight < el.scrollHeight - 2;
    zone.classList.toggle("top", top);
    zone.classList.toggle("bottom", bottom);
    up.hidden = !top;
    down.hidden = !bottom;
  };
  const plan = () => { if (!planned) { planned = true; requestAnimationFrame(update); } };
  el.addEventListener("scroll", plan, { passive: true });
  new ResizeObserver(plan).observe(el);
  new MutationObserver(plan).observe(el, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ["hidden"] });
  update();
}

// --- Loading -----------------------------------------------------------------------------------------------------

async function load() {
  config = await api("GET", "/api/config");
  fillSelect($("#opt-transcription"), config.transcriptions, config.active_transcription);
  fillSelect($("#opt-translator"), config.translators, config.active_translator);
  $("#opt-language").value = config.language || "";
  $("#opt-output-dir").value = config.output_dir || "";
  fillLanguages($("#opt-languages"), config.target_languages || ["fr"]);
  $("#opt-dest-next-to").checked = config.destination?.next_to_video ?? true;
  $("#opt-dest-output").checked = config.destination?.output_dir ?? false;
  updateOutputZone();
  updateDefaultPause();
  showTranslatorSettings();
  const wanted = document.querySelector(`input[name=goal][value=${dubbingEnabled() && config.goal === "mux" ? "mux" : "translation"}]`);
  wanted.checked = true;
  updateGoal();
  $("#opt-review").checked = claudeAvailable(); $("#review-zone").hidden = !claudeAvailable();
  const github = (config.web || {}).github || "";          // link greyed out as long as the public repository does not exist
  const link = $("#github-link");
  link.href = github || "#";
  link.setAttribute("aria-disabled", github ? "false" : "true");
  link.title = github ? T("ui.github") : T("ui.github_unpublished");
  await updateStatus();
  await updateHistory();
  await checkLastBatch();
}

// --- Target languages (checkboxes + free field for the other codes) ----------------------------------------------

const LANGUAGES = ["fr", "en", "de", "es", "it", "pt", "nl"];      // offered as checkboxes; the others are typed by their code

/* Spoken language: automatic detection or one of the offered languages. */
function fillSpokenLanguages() {
  $("#opt-language").replaceChildren(new Option(T("ui.auto_detect"), ""), ...LANGUAGES.map((c) => new Option(languageName(c), c)));
}

/* Language of the interface: automatic (browser) or imposed; saved in the configuration, for the log too. */
function fillLanguageChoice() {
  const sel = $("#language-choice");
  const auto = pickLanguage(navigator.languages || [navigator.language], I18N.available);
  sel.replaceChildren(new Option(T("ui.lang_auto", { lang: autonym(auto) }), ""), ...I18N.available.map((c) => new Option(autonym(c), c)));
  sel.value = I18N.setting;
  sel.hidden = I18N.available.length < 2;
}
$("#language-choice").addEventListener("change", async () => {
  try { config = await api("PUT", "/api/config", { ...config, ui_language: $("#language-choice").value }); location.reload(); }
  catch (err) { alert(err.message); }
});

function fillLanguages(zone, codes) {
  zone.innerHTML = "";
  for (const code of LANGUAGES) {
    const l = document.createElement("label"); l.className = "check";
    const c = document.createElement("input"); c.type = "checkbox"; c.value = code; c.checked = codes.includes(code);
    l.append(c, document.createTextNode(" " + languageName(code)));
    zone.appendChild(l);
  }
  const l = document.createElement("label"); l.className = "check";
  const others = document.createElement("input"); others.type = "text"; others.dataset.role = "others"; others.spellcheck = false;
  others.placeholder = T("ui.langs.others_placeholder"); others.title = T("ui.langs.others_title");
  others.value = codes.filter((c) => !LANGUAGES.includes(c)).join(", ");
  l.append(document.createTextNode(T("ui.langs.others") + " "), others);
  zone.appendChild(l);
}

function readLanguages(zone = $("#opt-languages")) {
  const codes = [...zone.querySelectorAll("input[type=checkbox]:checked")].map((c) => c.value);
  for (const c of zone.querySelector("[data-role=others]").value.toLowerCase().split(/[\s,;]+/)) if (c && !codes.includes(c)) codes.push(c);
  return codes;
}

/* Pause before the translation: ticked by default except for a local AI (free) or "none". */
function defaultPause(m) {
  if (!m || m.type === "none") return false;
  return !(m.type === "openai" && /127\.0\.0\.1|localhost|\[::1\]/.test(m.url || ""));
}
function updateDefaultPause() { $("#opt-pause").checked = defaultPause(engine("translators", $("#opt-translator").value)); }

function fillSelect(sel, list, active) {
  sel.innerHTML = "";
  for (const m of list || []) {
    const o = document.createElement("option");
    o.value = m.id; o.textContent = engineName(m);
    if (m.id === active) o.selected = true;
    sel.appendChild(o);
  }
}

async function updateStatus() {
  try {
    const e = await api("GET", "/api/status").catch((err) => { $("#offline").hidden = false; throw err; });
    $("#offline").hidden = true;
    // An open batch has its own settings: the header shows what really runs, not the list of the page.
    const tr = engine("transcriptions", e.batch?.options?.transcription || $("#opt-transcription").value || e.active_transcription);
    const trans = engine("translators", e.batch?.options?.translator || $("#opt-translator").value || e.active_translator);
    const cTr = $("#chip-transcription");
    cTr.textContent = T("ui.header.transcription", { name: shortName(tr) }); cTr.title = tr ? engineName(tr) : "";
    cTr.className = "chip " + (tr?.command ? ({ ready: "ok", starting: "attention", stopped: "" }[e.local_transcription?.[tr.id]] || "") : "ok");
    if (tr?.command) cTr.title += " · " + T("ui.header.local_server", { state: label("ui.server.", e.local_transcription?.[tr.id] || "?") });
    const cTrans = $("#chip-translator");
    cTrans.textContent = T("ui.header.translator", { name: shortName(trans) }); cTrans.title = trans ? engineName(trans) : "";
    cTrans.className = "chip ok";
    const cAi = $("#chip-ai");
    if (e.local_ai && trans?.supervision) {
      cAi.hidden = false;
      cAi.textContent = T("ui.header.local_ai", { state: label("ui.ai.", e.local_ai.state) });
      cAi.className = "chip subtle " + ({ ready: "ok", starting: "attention", error: "error", safety_stop: "error" }[e.local_ai.state] || "");
    } else cAi.hidden = true;
    if (e.task && !currentTask && !parallelMode) await follow(e.task.id);
    if (e.batch && !currentBatch) await followBatch(e.batch.id);
  } catch (err) { console.warn(err); }
}

// --- Settings of the chosen translator, shown on the page -----------------------------------------------------------

const TRANSLATOR_FIELDS = {
  claude: [["model", "ui.model", { list: "claude-models", placeholder: "claude-sonnet-5-5" }],
           ["effort", "ui.effort", { select: [["", "ui.effort_default"], ["low", "low"], ["medium", "medium"], ["high", "high"]] }]],
  openai: [["url", "ui.server_url", { placeholder: "http://127.0.0.1:8080" }], ["model", "ui.model", {}],
           ["supervision", "ui.supervision", { placeholder: "ui.supervision_none" }],
           ["stop_after", "ui.stop_after", { checkbox: true }]],
  none: [],
};

const textOrKey = (x) => (x.startsWith("ui.") ? T(x) : x);     // labels of TRANSLATOR_FIELDS

function showTranslatorSettings() {
  const zone = $("#translator-engine"), m = engine("translators", $("#opt-translator").value);
  const fields = m ? TRANSLATOR_FIELDS[m.type || "openai"] || [] : [];
  zone.innerHTML = "";
  zone.hidden = !fields.length;
  if (!fields.length) return;
  const title = document.createElement("div");
  title.className = "title";
  title.innerHTML = `<span>${T("ui.engine.settings_of", { name: "<strong></strong>" })}</span><span class="help">${T("ui.engine.key_in_settings")}</span>`;
  title.querySelector("strong").textContent = engineName(m);
  zone.appendChild(title);
  for (const [key, caption, o] of fields) {
    const l = document.createElement("label");
    if (o.checkbox) {
      l.className = "check";
      const c = document.createElement("input"); c.type = "checkbox"; c.dataset.key = key; c.checked = !!m[key];
      l.append(c, document.createTextNode(" " + textOrKey(caption)));
    } else {
      l.textContent = textOrKey(caption);
      let e;
      if (o.select) {
        e = document.createElement("select");
        for (const [v, x] of o.select) { const op = document.createElement("option"); op.value = v; op.textContent = textOrKey(x); e.appendChild(op); }
        e.value = m[key] ?? "";
      } else {
        e = document.createElement("input"); e.type = "text"; e.value = m[key] ?? ""; e.spellcheck = false;
        if (o.placeholder) e.placeholder = textOrKey(o.placeholder);
        if (o.list) e.setAttribute("list", o.list);
      }
      e.dataset.key = key;
      l.appendChild(e);
    }
    zone.appendChild(l);
  }
}

/* Copies the displayed fields into the configuration; saves if something changed. */
async function saveTranslatorSettings() {
  const m = engine("translators", $("#opt-translator").value);
  if (!m || $("#translator-engine").hidden) return;
  let changed = false;
  for (const e of $("#translator-engine").querySelectorAll("[data-key]")) {
    const v = e.type === "checkbox" ? e.checked : e.value.trim();
    if ((m[e.dataset.key] ?? (e.type === "checkbox" ? false : "")) !== v) { m[e.dataset.key] = v; changed = true; }
  }
  if (changed) config = await api("PUT", "/api/config", config);
}

// --- Starting -----------------------------------------------------------------------------------------------------

function pageDestination() { return { next_to_video: $("#opt-dest-next-to").checked, output_dir: $("#opt-dest-output").checked }; }

/* The output folder field sticks to its checkbox and is hidden when it is unticked; the folder is still used for
   YouTube videos and the working files, hence the discreet reminder. */
function updateOutputZone() {
  const ticked = $("#opt-dest-output").checked;
  $("#output-zone").hidden = !ticked;
  $("#output-reminder").textContent = ticked ? "" : T("ui.dest.reminder", { path: $("#opt-output-dir").value.trim() || T("ui.dest.the_output_folder") });
}
$("#opt-dest-output").addEventListener("change", updateOutputZone);
$("#opt-output-dir").addEventListener("input", updateOutputZone);

function taskOptions(source) {
  return { source, transcription: $("#opt-transcription").value, translator: $("#opt-translator").value,
           language: $("#opt-language").value, languages: readLanguages(), output_dir: $("#opt-output-dir").value.trim(),
           destination: pageDestination(), redo: $("#opt-redo").checked, pause: $("#opt-pause").checked, until: pageGoal(), replace: pageReplace() };
}

/* What we want to get: all the rest of the page adapts to it. */
const pageGoal = () => document.querySelector("input[name=goal]:checked")?.value || "translation";
const pageReplace = () => pageGoal() === "mux" && $("#opt-replace").checked;

function updateGoal() {
  const available = dubbingEnabled(), zone = $("#opt-goal-dubbing");
  zone.disabled = !available;
  if (!available && zone.checked) document.querySelector("input[name=goal][value=translation]").checked = true;
  $("#goal-dubbing-zone").title = available ? "" : T("ui.goal.dub_unavailable");
  $("#replace-zone").hidden = pageGoal() !== "mux";
  if ($("#replace-zone").hidden) $("#opt-replace").checked = false;     // never kept from one time to the next: the original goes to the recycle bin
  const note = $("#goal-note"), languages = readLanguages(), noTranslator = engine("translators", $("#opt-translator").value)?.type === "none";
  if (pageGoal() !== "mux") note.textContent = T("ui.goal.result_subtitles");
  else if (noTranslator || !languages.length) note.textContent = T("ui.goal.dub_needs_translation");
  else note.textContent = T("ui.goal.result_dubbing", { lang: languageName(languages[0]) });
}
for (const r of document.querySelectorAll("input[name=goal]")) r.addEventListener("change", updateGoal);
$("#opt-translator").addEventListener("change", updateGoal);
$("#opt-languages").addEventListener("input", updateGoal);

/* Target languages and destination chosen on the page: remembered in the configuration if they changed. */
async function savePageOptions() {
  const languages = readLanguages(), dest = pageDestination();
  const changed = pageGoal() !== (config.goal || "translation") || JSON.stringify(languages) !== JSON.stringify(config.target_languages || ["fr"])
    || JSON.stringify(dest) !== JSON.stringify({ next_to_video: config.destination?.next_to_video ?? true, output_dir: config.destination?.output_dir ?? false });
  if (!changed) return;
  config.target_languages = languages; config.destination = dest; config.goal = pageGoal();
  config = await api("PUT", "/api/config", config);
}

async function start(source) {
  if (!source) return;
  if (!$("#opt-dest-next-to").checked && !$("#opt-dest-output").checked
      && !confirm(T("ui.confirm_no_destination"))) return;
  $("#btn-go").disabled = true;
  try {
    await saveTranslatorSettings();
    await savePageOptions();
    const t = await api("POST", "/api/tasks", taskOptions(source));
    $("#preview").hidden = true;
    follow(t.id);
  } catch (err) {
    if (err.data?.folder) openFolder(source);
    else alert(T("ui.err.start", { error: err.message }));
  }
  finally { $("#btn-go").disabled = false; }
}

$("#form-source").addEventListener("submit", (ev) => { ev.preventDefault(); start($("#source").value.trim()); });

let previewDelay = null;
$("#source").addEventListener("input", () => {
  clearTimeout(previewDelay);
  const v = $("#source").value.trim();
  if (!/^https?:\/\//.test(v)) { $("#preview").hidden = true; return; }
  previewDelay = setTimeout(async () => {
    $("#preview").hidden = false; $("#preview").textContent = T("ui.preview.reading");
    try {
      const i = await api("POST", "/api/youtube", { url: v, output_dir: $("#opt-output-dir").value.trim() });
      $("#preview").textContent = i.ok ? `${i.title} (${formatDuration(i.duration)})${i.exists ? T("ui.preview.downloaded") : ""} → ${i.video}` : "yt-dlp : " + i.detail;
    } catch (err) { $("#preview").textContent = err.message; }
  }, 600);
});

/* System dialog box; if none is available, the message invites to type the path in the field. */
async function choosePath(type, startDir = "") {
  try { return (await api("POST", "/api/dialog", { type, start: startDir })).path || ""; }
  catch (err) { alert(err.message); return ""; }
}

$("#btn-browse").addEventListener("click", async () => {
  const path = await choosePath("file");
  if (path) { $("#source").value = path; $("#source").focus(); }
});
$("#btn-output-folder").addEventListener("click", async () => {
  const path = await choosePath("folder", $("#opt-output-dir").value.trim());
  if (path) $("#opt-output-dir").value = path;
});

// Drag and drop: the browser does not give the path, the file is sent and copied into the output folder.
const drop = $("#drop");
for (const ev of ["dragenter", "dragover"]) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("hover"); });
for (const ev of ["dragleave", "drop"]) drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("hover"); });
drop.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) sendFile(f); });

function sendFile(file) {
  const prog = $("#drop-progress"), bar = prog.querySelector(".bar"), text = prog.querySelector(".text");
  prog.hidden = false; bar.style.width = "0"; text.textContent = T("ui.upload.copying", { file: file.name });
  const xhr = new XMLHttpRequest();
  const outputDir = encodeURIComponent($("#opt-output-dir").value.trim());
  xhr.open("PUT", `/api/files?name=${encodeURIComponent(file.name)}&output_dir=${outputDir}`);
  xhr.setRequestHeader("X-Language", I18N.language);
  xhr.upload.onprogress = (e) => { if (e.lengthComputable) { bar.style.width = (100 * e.loaded / e.total).toFixed(0) + "%"; text.textContent = T("ui.upload.progress", { file: file.name, pct: (100 * e.loaded / e.total).toFixed(0) }); } };
  xhr.onload = () => {
    try {
      const r = JSON.parse(xhr.responseText);
      if (xhr.status >= 400) throw new Error(r.error);
      text.textContent = (r.existed ? T("ui.upload.existed") : T("ui.upload.copied")) + " " + T("ui.upload.click_start");
      setTimeout(() => { prog.hidden = true; }, 2500);
      $("#source").value = r.path;
      $("#btn-go").focus();
    } catch (err) { text.textContent = T("ui.failed", { error: err.message }); }
  };
  xhr.onerror = () => { text.textContent = T("ui.upload.failed"); };
  xhr.send(file);
}

// --- Source folder: inventory, selection, language per video ------------------------------------------------------

let inventory = null;

$("#btn-source-folder").addEventListener("click", async () => {
  const path = await choosePath("source_folder");
  if (path) { $("#source").value = path; openFolder(path); }
});
function closeFolder() {
  if (scanId) api("POST", `/api/folder/${scanId}/cancel`, {}).catch(() => {});
  scanId = null;
  $("#folder").hidden = true; $("#folder-actions").hidden = true; $("#btn-go").hidden = false;
  $("#opt-pause-text").textContent = T("ui.pause_option");
}
$("#btn-close-folder").addEventListener("click", closeFolder);

let scanId = null;      // inventory of the folder in progress

async function openFolder(path) {
  $("#folder").hidden = false; $("#folder-actions").hidden = false; $("#btn-go").hidden = true;   // Start the batch replaces Start
  inventory = null;
  $("#folder-root").textContent = path;
  $("#folder-tree").innerHTML = ""; $("#folder-summary").textContent = ""; $("#folder-existing-batch").hidden = true;
  $("#btn-start-batch").disabled = true;
  $("#opt-pause-text").textContent = T("ui.pause_option_batch");
  const sel = $("#folder-global-language");
  sel.innerHTML = $("#opt-language").innerHTML;       // at once: the list does not stay empty during the scan
  sel.value = $("#opt-language").value;
  let a;
  try { a = await api("POST", "/api/folder", { path }); }
  catch (err) { $("#folder-root").textContent = path + " : " + err.message; return; }
  scanId = a.id;
  updateLoading(a);
  while (scanId === a.id) {
    let e;
    try { e = await api("GET", `/api/folder/${a.id}${inventory ? "" : "?videos=1"}`); }
    catch (err) { $("#folder-root").textContent = path + " : " + err.message; scanId = null; $("#folder-loading").hidden = true; return; }
    if (scanId !== a.id) return;
    updateLoading(e);
    if (e.videos && !inventory) buildTree(e);
    if (inventory) applyTrackLanguages(e.languages);
    if (e.state === "cancelled") { closeFolder(); return; }
    if (e.state === "error") $("#folder-root").textContent = path + " : " + e.error;
    if (["done", "error"].includes(e.state)) { scanId = null; return; }
    await new Promise((end) => setTimeout(end, 300));
  }
}

function updateLoading(e) {
  const zone = $("#folder-loading"), bar = $("#folder-progress");
  zone.hidden = ["done", "cancelled", "error"].includes(e.state);
  bar.classList.toggle("indeterminate", e.state === "scanning");
  if (e.state === "scanning") {
    bar.querySelector(".bar").style.width = "";
    bar.querySelector(".text").textContent = T("ui.folder.searching", { n: e.seen });
  } else if (e.state === "languages") {
    bar.querySelector(".bar").style.width = (100 * e.done / Math.max(1, e.count)).toFixed(0) + "%";
    bar.querySelector(".text").textContent = T("ui.folder.reading_tracks", { done: e.done, n: e.count });
  }
  $("#btn-folder-cancel").title = e.state === "scanning" ? T("ui.folder.cancel_scan") : T("ui.folder.cancel_languages");
}

$("#btn-folder-cancel").addEventListener("click", () => {
  if (!inventory) closeFolder();
  else if (scanId) api("POST", `/api/folder/${scanId}/cancel`, {}).catch(() => {});
});

function buildTree(e) {
  inventory = e;
  const folderCount = new Set(e.videos.map((v) => v.folder)).size;
  $("#folder-root").textContent = T("ui.folder.summary", { path: e.root, n: e.count }) + (folderCount > 1 ? " " + T("ui.folder.in_folders", { n: folderCount }) : "");
  const ex = $("#folder-existing-batch");
  ex.hidden = !e.existing_batch;
  if (e.existing_batch) {
    ex.innerHTML = "";
    ex.append(document.createTextNode(T("ui.folder.batch_exists", { file: e.existing_batch }) + " "));
    ex.appendChild(button(T("ui.folder.resume_this_batch"), () => loadBatch(e.existing_batch)));
    ex.append(document.createTextNode(" " + T("ui.folder.or_new_batch")));
  }
  const tree = $("#folder-tree");
  tree.innerHTML = "";
  if (!e.count) tree.textContent = T("ui.folder.no_video");
  const targets = readLanguages(), folded = folderCount > 3, languages = $("#opt-language").innerHTML, byDefault = $("#opt-language").value || "";
  $("#btn-folder-fold").textContent = folded ? T("ui.folder.expand_all") : T("ui.folder.collapse_all");
  $("#btn-folder-fold").hidden = folderCount < 2;
  let container = null, current = null;
  for (const v of e.videos) {
    if (!container || v.folder !== current) {
      current = v.folder;
      container = addGroup(tree, v.folder, folded);
    }
    const row = document.createElement("div"); row.className = "video"; row.dataset.path = v.path; row.dataset.size = v.size;
    const c = document.createElement("input"); c.type = "checkbox";
    c.checked = targets.some((l) => !v.subtitles.includes(l));
    c.addEventListener("change", updateFolderSummary);
    const name = document.createElement("span"); name.className = "name"; name.textContent = v.name; name.title = v.path;
    const size = document.createElement("span"); size.className = "size"; size.textContent = formatBytes(v.size);
    const badges = document.createElement("span");
    for (const code of v.subtitles) { const b = document.createElement("span"); b.className = "badge" + (targets.includes(code) ? " ok" : ""); b.textContent = code; b.title = T("ui.folder.existing_subtitles"); badges.appendChild(b); }
    const language = document.createElement("select"); language.innerHTML = languages; language.dataset.role = "language"; language.value = byDefault;
    language.addEventListener("change", () => { language.dataset.touched = "1"; });
    row.append(c, name, size, badges, language);
    container.appendChild(row);
  }
  updateFolderSummary();
}

function addGroup(tree, folderName, folded) {
  const head = document.createElement("div"); head.className = "folder-group";
  const rows = document.createElement("div"); rows.className = "folder-rows"; rows.hidden = folded;
  const arrow = document.createElement("button"); arrow.type = "button"; arrow.className = "fold"; arrow.textContent = folded ? "▸" : "▾";
  const toggle = () => { rows.hidden = !rows.hidden; arrow.textContent = rows.hidden ? "▸" : "▾"; };
  arrow.addEventListener("click", toggle);
  const c = document.createElement("input"); c.type = "checkbox";
  c.addEventListener("change", () => { for (const x of rows.querySelectorAll("input[type=checkbox]")) x.checked = c.checked; updateFolderSummary(); });
  const name = document.createElement("span"); name.className = "folder-name"; name.textContent = folderName || T("ui.folder.root"); name.addEventListener("click", toggle);
  const count = document.createElement("span"); count.className = "help"; count.dataset.role = "count";
  head.append(arrow, c, name, count);
  tree.append(head, rows);
  return rows;
}

$("#btn-folder-fold").addEventListener("click", () => {
  const groups = [...$("#folder-tree").querySelectorAll(".folder-rows")];
  const unfold = groups.some((g) => g.hidden);
  for (const g of groups) { g.hidden = !unfold; g.previousElementSibling.querySelector(".fold").textContent = unfold ? "▾" : "▸"; }
  $("#btn-folder-fold").textContent = unfold ? T("ui.folder.collapse_all") : T("ui.folder.expand_all");
});

/* The language read on the audio track arrives in the background; a choice made by hand is never overwritten. */
function applyTrackLanguages(languages) {
  for (const l of folderRows()) {
    const code = languages[l.dataset.path], s = l.querySelector("select[data-role=language]");
    if (!code || s.dataset.touched || s.dataset.track === code) continue;
    s.dataset.track = code;
    s.title = T("ui.folder.track_tag", { code });
    if ([...s.options].some((o) => o.value === code)) s.value = code;
  }
}

function folderRows() { return [...$("#folder-tree").querySelectorAll(".video")]; }
function updateFolderSummary() {
  const checked = folderRows().filter((l) => l.querySelector("input[type=checkbox]").checked);
  const bytes = checked.reduce((a, l) => a + Number(l.dataset.size), 0);
  $("#folder-summary").textContent = T("ui.folder.checked", { n: checked.length, size: formatBytes(bytes) });
  $("#btn-start-batch").disabled = !checked.length;
  for (const g of $("#folder-tree").querySelectorAll(".folder-rows")) {
    const boxes = [...g.querySelectorAll("input[type=checkbox]")], n = boxes.filter((c) => c.checked).length;
    const head = g.previousElementSibling, c = head.querySelector("input[type=checkbox]");
    c.checked = n === boxes.length; c.indeterminate = n > 0 && n < boxes.length;
    head.querySelector("[data-role=count]").textContent = T("ui.folder.group_checked", { n, total: boxes.length });
  }
}
$("#btn-folder-all").addEventListener("click", () => { for (const c of $("#folder-tree").querySelectorAll("input[type=checkbox]")) c.checked = true; updateFolderSummary(); });
$("#btn-folder-none").addEventListener("click", () => { for (const c of $("#folder-tree").querySelectorAll("input[type=checkbox]")) c.checked = false; updateFolderSummary(); });
$("#btn-folder-missing").addEventListener("click", () => {
  const targets = readLanguages();
  for (const l of folderRows()) {
    const v = inventory.videos.find((x) => x.path === l.dataset.path);
    l.querySelector("input[type=checkbox]").checked = !!v && targets.some((c) => !v.subtitles.includes(c));
  }
  updateFolderSummary();
});
$("#btn-folder-apply-language").addEventListener("click", () => {
  for (const s of $("#folder-tree").querySelectorAll("select[data-role=language]")) { s.value = $("#folder-global-language").value; s.dataset.touched = "1"; }
});

$("#btn-start-batch").addEventListener("click", async () => {
  const videos = folderRows().filter((l) => l.querySelector("input[type=checkbox]").checked)
    .map((l) => ({ path: l.dataset.path, language: l.querySelector("select[data-role=language]").value }));
  if (!videos.length) return;
  $("#btn-start-batch").disabled = true;
  try {
    await saveTranslatorSettings();
    await savePageOptions();
    const o = taskOptions("");
    const batch = await api("POST", "/api/batches", { root: inventory.root, videos, transcription: o.transcription, translator: o.translator,
                                                      languages: o.languages, destination: o.destination, output_dir: o.output_dir, pause: o.pause, redo: o.redo,
                                                      stop: pageGoal(), replace: pageReplace(), review: claudeAvailable() && $("#opt-review").checked });
    closeFolder();
    followBatch(batch.id);
  } catch (err) { alert(T("ui.err.start_batch", { error: err.message })); }
  finally { $("#btn-start-batch").disabled = false; }
});

$("#btn-resume-batch").addEventListener("click", async () => {
  const path = await choosePath("batch", $("#opt-output-dir").value.trim());
  if (path) loadBatch(path);
});

// Batch left unfinished at the previous session: offered in a box at the top of the page.
async function checkLastBatch() {
  const d = (await api("GET", "/api/batches/last").catch(() => ({}))).last;
  if (!d) return;
  $("#last-batch-title").textContent = { suspended: T("ui.last_batch.was_paused"), awaiting_translation: T("ui.last_batch.was_waiting") }[d.state]
    || T("ui.last_batch.was_interrupted");
  const trans = engine("translators", d.translator) ? engineName(engine("translators", d.translator)) : d.translator;
  $("#last-batch-detail").textContent = `${d.root} · ` + T("ui.last_batch.done", { done: d.done, n: d.total })
    + (trans ? " · " + T("ui.last_batch.translator", { name: trans }) : "") + (d.updated ? " · " + T("ui.last_batch.last_activity", { date: d.updated }) : "");
  $("#btn-last-resume").onclick = () => loadBatch(d.file, true);
  $("#btn-last-settings").onclick = () => loadBatch(d.file);
  $("#btn-last-ignore").onclick = async () => { await api("POST", "/api/batches/last/forget", {}).catch(() => {}); $("#last-batch").hidden = true; };
  $("#last-batch").hidden = false;
}

async function loadBatch(file, startNow = false) {
  try {
    const batch = await api("POST", "/api/batches/load", { file });
    if (startNow) await api("POST", `/api/batches/${batch.id}/resume`, {});
    closeFolder();
    followBatch(batch.id);
  } catch (err) { alert(T("ui.err.resume", { error: err.message })); }
}

// --- Steps of a video ---------------------------------------------------------------------------------------------

const dubbingEnabled = () => !!config.dubbing?.enabled;
const claudeAvailable = () => (config.translators || []).some((m) => m.type === "claude");

const wantsDubbing = (b) => dubbingEnabled() && ["dubbing", "mux"].includes(b.options?.stop);

/* One dot per step of the chain; those that do not apply to this batch are not shown. */
function videoSteps(v, b) {
  const review = b.options?.review || v.reviewed;
  return [[T("ui.transcription"), v.transcribed], ...(review ? [[T("ui.dots.review"), v.reviewed], [T("ui.dots.validation"), v.reviewed && v.validated]] : []), [T("ui.translation"), v.translated],
          ...(wantsDubbing(b) || v.dubbed || v.muxed ? [[T("ui.step.dubbing"), v.dubbed], [T("ui.dots.mkv"), v.muxed]] : [])];
}

function updateDots(z, steps) {
  if (z.childElementCount !== steps.length) z.replaceChildren(...steps.map(() => document.createElement("i")));
  steps.forEach(([name, done], i) => { z.children[i].className = done ? "done" : ""; z.children[i].title = T(done ? "ui.dots.done" : "ui.dots.todo", { step: name }); });
}

const toValidate = (v) => v.reviewed && !v.validated;
const displayedBatchId = () => currentBatch || finishedBatch;

async function continueEpisode(path) {
  const id = displayedBatchId();
  if (!id) return;
  try {
    await api("POST", `/api/batches/${id}/continue`, { path });
    if (currentBatch) refreshBatch(); else followBatch(id);
  } catch (err) { alert(err.message); }
}

let validationVideo = "";                         // video whose names are shown; empty for the whole batch

function validationRow(p) {
  const tr = document.createElement("tr");
  tr.dataset.transcribed = p.transcribed;
  const field = (value, name) => { const i = document.createElement("input"); i.type = "text"; i.value = value || ""; i.dataset.field = name; i.spellcheck = false; return i; };
  const tick = document.createElement("input"); tick.type = "checkbox"; tick.dataset.field = "accept";
  tick.checked = !!p.spelling && p.status !== "uncertain" && p.confidence !== "low";
  const why = document.createElement("div"); why.className = "why";
  why.textContent = `${p.status === "uncertain" ? T("ui.names.uncertain") + " " : ""}${p.reason || ""} (${T("ui.names.stats", { n: p.occurrences || 0, confidence: p.confidence ? label("ui.names.confidence.", p.confidence) : "?" })})`;
  why.title = (p.examples || []).join("\n");
  for (const content of [tick, p.transcribed, field(p.spelling, "spelling"), field(p.french, "french"), why]) {
    const td = document.createElement("td"); td.append(content); tr.appendChild(td);
  }
  return tr;
}

async function openValidation(path) {
  const id = displayedBatchId();
  if (!id) return;
  try {
    const r = await api("GET", `/api/batches/${id}/review${path ? "?path=" + encodeURIComponent(path) : ""}`);
    if (!r.proposals.length && path) return continueEpisode(path);
    validationVideo = path || "";
    $("#validation-title").textContent = path ? T("ui.names.title_video", { n: r.proposals.length, file: fileName(path) }) : T("ui.names.title_batch", { n: r.proposals.length });
    $("#validation-rows").replaceChildren(...r.proposals.map(validationRow));
    $("#btn-validation-validate").textContent = path ? T("ui.names.validate_continue") : T("ui.names.validate");
    $("#validation-message").textContent = "";
    $("#validation").hidden = false;
    $("#validation").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (err) { alert(err.message); }
}

for (const [selector, value] of [["#btn-validation-all", true], ["#btn-validation-none", false]])
  $(selector).addEventListener("click", () => { for (const c of $("#validation-rows").querySelectorAll("[data-field=accept]")) c.checked = value; });
$("#btn-validation-close").addEventListener("click", () => { $("#validation").hidden = true; });
$("#btn-validation-validate").addEventListener("click", async () => {
  const id = displayedBatchId(), btn = $("#btn-validation-validate");
  const field = (tr, name) => tr.querySelector(`[data-field=${name}]`);
  const decisions = [...$("#validation-rows").children].map((tr) => ({ transcribed: tr.dataset.transcribed, spelling: field(tr, "spelling").value, french: field(tr, "french").value, accept: field(tr, "accept").checked }));
  btn.disabled = true;
  try {
    await api("POST", `/api/batches/${id}/validate`, { decisions });
    $("#validation").hidden = true;
    if (validationVideo) await continueEpisode(validationVideo);
    else if (currentBatch) refreshBatch();
  } catch (err) { $("#validation-message").textContent = T("ui.failed", { error: err.message }); }
  finally { btn.disabled = false; }
});
$("#btn-batch-check").addEventListener("click", () => openValidation(""));
async function changeBatchGoal() {
  const id = displayedBatchId();
  try {
    await api("POST", `/api/batches/${id}/goal`, { stop: $("#batch-goal").value, replace: $("#batch-replace").checked });
    $("#batch-goal").blur();
    if (currentBatch) refreshBatch(); else followBatch(id);
  } catch (err) { alert(err.message); }
}
$("#batch-goal").addEventListener("change", changeBatchGoal);
$("#batch-replace").addEventListener("change", changeBatchGoal);

// --- Following a batch --------------------------------------------------------------------------------------------

let currentBatch = null, batchTimer = null, batchPausedFor = null, lastBatchEstimate = null;
let batchSuspendedFor = null, suspendedBatch = null;    // suspended batch whose settings panel is built
let batchVideoSeen = null;                               // running video already centred in the batch list

function followBatch(id) {
  currentBatch = id; batchPausedFor = null; batchSuspendedFor = null; batchVideoSeen = null;
  batchRows = new Map(); $("#batch-videos").innerHTML = "";
  $("#last-batch").hidden = true;
  $("#batch").hidden = false; $("#batch-pause").hidden = true; $("#batch-suspended").hidden = true;
  clearInterval(batchTimer);
  batchTimer = setInterval(refreshBatch, 2000);
  return refreshBatch();
}

/* Time left computed by the service (remaining_s): it survives a reload of the page. */
function remainingText(s) {
  if (s === null || s === undefined) return "";
  return s > 0 ? T("ui.time.left_about", { time: formatDuration(s) }) : T("ui.time.ending");
}

let batchRows = new Map();                        // rank of the video → its row (its path changes if the MKV replaces it)

function updateBatchList(ul, b) {
  for (const [rank, v] of b.videos.entries()) {
    let r = batchRows.get(rank);
    if (!r) {
      r = { li: document.createElement("li"), e: document.createElement("span"), n: document.createElement("span"), d: document.createElement("span"), task: null, state: null, accordion: null, duration: "" };
      r.n.className = "src"; r.n.textContent = v.name; r.d.className = "date";
      r.retry = document.createElement("button");
      Object.assign(r.retry, { type: "button", className: "secondary small", textContent: T("ui.batch.retry"), title: T("ui.batch.retry_one_title"), hidden: true });
      r.retry.addEventListener("click", () => retryBatch(v.path));
      r.dots = document.createElement("span"); r.dots.className = "dots";
      r.continue = document.createElement("button");
      Object.assign(r.continue, { type: "button", className: "secondary small", hidden: true });
      r.continue.addEventListener("click", () => { if (toValidate(r.v)) openValidation(r.v.path); else continueEpisode(r.v.path); });
      r.li.append(r.e, r.n, r.dots, r.d, r.continue, r.retry);
      ul.appendChild(r.li);
      batchRows.set(rank, r);
    }
    r.v = v;
    const blocked = v.state === "prepared" && toValidate(v);
    r.e.className = "state " + (blocked ? "to_validate" : v.state);
    r.e.textContent = label("ui.video_state.", blocked ? "to_validate" : v.state);
    updateDots(r.dots, videoSteps(v, b));
    const complete = v.translated && (!wantsDubbing(b) || (b.options.stop === "dubbing" ? v.dubbed : v.muxed));
    r.continue.hidden = complete || ["running", "error", "cancelled"].includes(v.state) || b.state === "cancelled";
    r.continue.textContent = toValidate(v) ? T("ui.check_names") : T("ui.batch.continue");
    r.continue.title = toValidate(v) ? T("ui.batch.names_waiting") : T(wantsDubbing(b) ? "ui.batch.continue_dub_title" : "ui.batch.continue_title");
    r.n.textContent = v.name; r.n.title = v.error || v.path;
    r.retry.hidden = v.state !== "error" || b.state === "cancelled";
    if (v.task !== r.task) {
      r.accordion?.remove();
      r.task = v.task; r.accordion = null;
      if (v.task) { r.accordion = logAccordion({ id: v.task }, r.n); r.li.appendChild(r.accordion.button); r.accordion.install(r.li); }
    }
    if (v.state !== r.state) {
      r.state = v.state; r.duration = "";
      r.accordion?.reload();
      if (v.task && v.state !== "running") passDuration(v.task, v.state).then((x) => { r.duration = x; });
    }
    r.d.textContent = (v.detected_language ? languageName(v.detected_language) : v.language ? languageName(v.language) : T("ui.batch.detection"))
      + (v.count ? " · " + T("ui.batch.nb_subtitles", { n: v.count }) : "") + (r.duration ? ` · ${r.duration}` : "") + (v.error ? " · " + v.error : "");
  }
}

/* Several videos at once: one row per video (translated lines, elapsed time, time left) and a common log. */
const MAX_COMMON_LOG_LINES = 500;
let parallelMode = false, commonLines = [];
const parallelFollow = new Map();              // task → { name, read, done, total, resumed }

function videoCode(name) { return /S\d+E\d+/i.exec(name)?.[0] || name.replace(/\.[^.]+$/, "").slice(0, 24); }

function enterParallel() {
  parallelMode = true; commonLines = []; parallelFollow.clear();
  clearInterval(timer); timer = null; currentTask = null;
  showRetry(null); $("#task").hidden = false; $("#steps").hidden = true; $("#btn-cancel").hidden = true; $("#pause").hidden = true; $("#result").hidden = true;
  $("#log-text").textContent = ""; $("#log-timer").textContent = "";
  $("#task-title").title = "";
  $("#task-state").textContent = label("ui.task_state.", "running"); $("#task-state").className = "state running";
}

function leaveParallel() {
  parallelMode = false; parallelFollow.clear(); taskProcess = null;
  showProcess();
  $("#batch-active").hidden = true;            // the common log stays readable until the next followed task
  $("#task-title").textContent = T("ui.parallel.last");
  $("#task-state").textContent = T("ui.parallel.finished"); $("#task-state").className = "state done";
}

async function readParallelFollow(id, s) {
  let t;
  try { t = await api("GET", `/api/tasks/${id}?since=${s.read}`); } catch (err) { return null; }
  let lines = t.log;
  if (!s.resumed) {                            // at the first read, only the translation, not the preparation
    lines = lines.slice(t.log_resumed_at || 0);
    s.resumed = true;
  }
  s.read = t.log_total;
  if (t.progress) { s.done = t.progress.done; s.total = t.progress.total; }
  const code = videoCode(s.name);
  return { t, lines: lines.map((x) => x.replace(/^(\[\d\d:\d\d:\d\d\])/, `$1 ${code}`)) };
}

function activeRow(a) {
  const d = document.createElement("div"); d.className = "row";
  const name = document.createElement("span"); name.className = "name"; name.textContent = a.name; name.title = a.name;
  const mini = document.createElement("div"); mini.className = "mini";
  const full = document.createElement("span"); full.style.width = (a.total ? 100 * a.done / a.total : 0).toFixed(0) + "%"; mini.appendChild(full);
  const count = document.createElement("span"); count.className = "time count"; count.textContent = a.total ? `${a.done}/${a.total}` : "…";
  const elapsed = document.createElement("span"); elapsed.className = "time"; elapsed.textContent = `⏱ ${formatDuration(a.elapsed)}`;
  const left = document.createElement("span"); left.className = "time"; left.textContent = a.left === null ? "" : T("ui.time.left", { time: formatDuration(a.left) });
  d.append(name, mini, count, elapsed, left);
  return d;
}

async function updateParallel(b) {
  if (!parallelMode) enterParallel();
  const actives = b.videos.filter((v) => v.state === "running" && v.task);
  for (const v of actives) if (!parallelFollow.has(v.task)) parallelFollow.set(v.task, { name: v.name, read: 0, done: 0, total: 0, resumed: false });
  const ids = new Set(actives.map((v) => v.task));
  const reads = await Promise.all([...parallelFollow].map(async ([id, s]) => ({ id, s, r: await readParallelFollow(id, s) })));
  if (!parallelMode) return;
  for (const { id } of reads) if (!ids.has(id)) parallelFollow.delete(id);   // finished: its last lines were just read

  const running = reads.filter(({ id, r }) => ids.has(id) && r?.t.state === "running").map(({ s, r }) => ({
    name: s.name, done: s.done, total: s.total, left: r.t.remaining_s ?? null,
    elapsed: r.t.pass_started ? Math.max(0, Math.round(Date.now() / 1000 - r.t.pass_started)) : 0 }));
  $("#batch-active").hidden = !running.length;
  $("#batch-active").replaceChildren(...running.map(activeRow));
  $("#task-title").textContent = T("ui.parallel.translating", { n: running.length });
  if (running.length) batchProcess += " · " + running.map((a) => `${videoCode(a.name)} ⏱ ${formatDuration(a.elapsed)}${a.left === null ? "" : ", " + T("ui.time.left", { time: formatDuration(a.left) })}`).join(" | ");
  showProcess();

  const fresh = reads.flatMap(({ r }) => r?.lines || []).sort((a, b) => a.slice(0, 10).localeCompare(b.slice(0, 10)));
  if (fresh.length) {
    const j = $("#log"), atBottom = j.scrollTop + j.clientHeight >= j.scrollHeight - 20;
    commonLines = commonLines.concat(fresh).slice(-MAX_COMMON_LOG_LINES);
    $("#log-text").textContent = commonLines.join("\n");
    if (atBottom) j.scrollTop = j.scrollHeight;
  }
}


async function refreshBatch() {
  if (!currentBatch) return;
  let b;
  try { b = await api("GET", `/api/batches/${currentBatch}`); } catch (err) { return; }
  $("#batch-root").textContent = b.root;
  const state = $("#batch-state");
  // The real step of the running video: without a pause, phase 1 goes up to the translation.
  state.textContent = b.state === "running" ? T("ui.batch_step." + (b.step || { 1: "transcription", 2: "translation", 3: "dubbing" }[b.phase])) : label("ui.batch_state.", b.state);
  state.className = "state " + b.state;
  $("#btn-batch-suspend").hidden = b.state !== "running";
  $("#btn-batch-resume").hidden = b.state !== "suspended";
  $("#btn-batch-cancel").hidden = ["done", "cancelled"].includes(b.state);
  $("#btn-batch-retry").hidden = !b.counts.error || b.state === "cancelled";
  $("#btn-batch-retry").textContent = T("ui.batch.retry_failed", { n: b.counts.error });
  const done = b.counts.done + (b.phase === 1 ? b.counts.prepared : 0) + (b.phase < 3 ? b.counts.translated : 0) + b.counts.error + b.counts.cancelled;
  const bar = $("#batch-progress");
  bar.querySelector(".bar").style.width = (100 * done / Math.max(1, b.total)).toFixed(0) + "%";
  bar.querySelector(".text").textContent = T("ui.batch.progress", { done, n: b.total }) + " (" + [T("ui.batch.count_done", { n: b.counts.done }), T("ui.batch.count_transcribed", { n: b.counts.prepared }), T("ui.batch.count_failed", { n: b.counts.error })].join(", ") + ")";
  const left = remainingText(b.remaining_s);
  const perVideo = b.state === "running" && b.avg_duration_s ? T("ui.batch.per_video", { time: formatDuration(b.avg_duration_s) }) : "";
  bar.querySelector(".text").textContent += (left ? ` · ${left}` : "") + (perVideo ? ` · ${perVideo}` : "");
  // Suspended, the videos already started go to their end: their follow-up stays shown as long as one remains.
  parallelBatch = b.concurrency > 1 && (b.state === "running" || (b.state === "suspended" && b.counts.running > 0));
  const activeCount = b.counts.running;
  const batchDetail = !parallelBatch ? "" : b.state === "suspended" ? " · " + T("ui.batch.finishing", { n: activeCount }) : " · " + T("ui.parallel.translating", { n: activeCount }) + (perVideo ? " · " + perVideo : "");
  batchProcess = { running: T("ui.process.batch", { done, n: b.total }) + (left ? " (" + left + ")" : "") + batchDetail, suspended: T("ui.process.batch_paused", { done, n: b.total }) + batchDetail,
                   awaiting_translation: T("ui.process.batch_ready", { done, n: b.total }) }[b.state] || null;
  if (!parallelBatch) showProcess();          // otherwise updateParallel completes the text first, without flickering
  // The setting of this batch, not the checkbox of the form (it only applies to a new piece of work).
  const pauseInfo = $("#batch-pause-info"), batchTranslator = engine("translators", b.options?.translator || config.active_translator);
  pauseInfo.hidden = b.phase !== 1 || !["running", "suspended"].includes(b.state) || batchTranslator?.type === "none";
  pauseInfo.textContent = b.options?.pause === false ? T("ui.batch.no_pause") : T(b.options?.review ? "ui.batch.will_pause_names" : "ui.batch.will_pause");
  $("#batch-file").textContent = T("ui.batch.file", { path: b.file });
  const ul = $("#batch-videos");
  updateBatchList(ul, b);
  const current = b.videos.find((v) => v.state === "running");
  if ((current?.path ?? null) !== batchVideoSeen) {  // new running video: centre it in the list
    batchVideoSeen = current?.path ?? null;
    if (current) { const li = ul.children[b.videos.indexOf(current)]; ul.scrollTop = li.offsetTop - (ul.clientHeight - li.offsetHeight) / 2; }
  }
  if (parallelBatch) await updateParallel(b);
  else {
    if (parallelMode) leaveParallel();
    if (current?.task && currentTask !== current.task) follow(current.task);
  }
  const dub = wantsDubbing(b);
  $("#batch-goal-zone").hidden = !dubbingEnabled() || b.state === "cancelled";
  if (document.activeElement !== $("#batch-goal")) $("#batch-goal").value = dub ? "mux" : "translation";
  $("#batch-replace-zone").hidden = !dub;
  $("#batch-replace").checked = !!b.options?.replace;
  $("#batch-goal-note").textContent = dub && b.dubbing_s ? T("ui.batch.gpu_time", { time: formatDuration(b.dubbing_s) }) : "";
  $("#btn-batch-translate").textContent = dub ? T("ui.batch.translate_dub_all") : T("ui.batch.translate_all");
  const toValidateCount = b.videos.filter(toValidate).length;
  $("#batch-to-check").hidden = !toValidateCount;
  $("#batch-to-check-text").textContent = T("ui.batch.names_to_check", { n: toValidateCount });
  if (b.state === "awaiting_translation") { if (batchPausedFor !== b.id) showBatchPause(b); }
  else { $("#batch-pause").hidden = true; batchPausedFor = null; }
  if (b.state === "suspended") { if (batchSuspendedFor !== b.id) showSuspended(b); }
  else { $("#batch-suspended").hidden = true; batchSuspendedFor = null; }
  if (["done", "cancelled"].includes(b.state)) { clearInterval(batchTimer); batchTimer = null; finishedBatch = currentBatch; currentBatch = null; updateHistory(); }
}

async function showBatchPause(b) {
  batchPausedFor = b.id;
  $("#batch-pause").hidden = false; $("#batch-message").textContent = "";
  $("#batch-pause-summary").textContent = T("ui.batch.ready_to_translate", { n: b.counts.prepared });
  fillSelect($("#batch-translator"), config.translators, b.options?.translator || config.active_translator);
  fillLanguages($("#batch-languages"), b.options?.languages || config.target_languages || ["fr"]);
  const update = async () => {
    try { lastBatchEstimate = await api("GET", `/api/batches/${b.id}/estimate?languages=${encodeURIComponent(readLanguages($("#batch-languages")).join(","))}`); showCostsIn($("#batch-costs"), lastBatchEstimate, $("#batch-translator").value); }
    catch (err) { $("#batch-message").textContent = err.message; }
  };
  $("#batch-languages").oninput = update;
  $("#batch-translator").onchange = () => { showBatchClaudeSettings(); showCostsIn($("#batch-costs"), lastBatchEstimate, $("#batch-translator").value); };
  const changeClaude = () => { batchClaudeSave = saveBatchClaudeSettings(update); };
  $("#batch-model").onchange = changeClaude;
  $("#batch-effort").onchange = changeClaude;
  showBatchClaudeSettings();
  await update();
}

/* Model and effort of the chosen Claude translator: editable here, because the translation of the batch reads the configuration again. */
let batchClaudeSave = Promise.resolve();

function showBatchClaudeSettings() {
  const m = engine("translators", $("#batch-translator").value), claude = m?.type === "claude";
  $("#batch-model-zone").hidden = !claude; $("#batch-effort-zone").hidden = !claude;
  $("#batch-effort-note").hidden = !claude || (m.effort || "low") === "low";
  if (!claude) return;
  const offered = [...$("#claude-models").options].map((o) => o.value), current = m.model || "claude-sonnet-5-5";
  const sel = $("#batch-model");
  sel.innerHTML = "";
  for (const v of offered.includes(current) ? offered : [current, ...offered]) {
    const o = document.createElement("option"); o.value = v; o.textContent = v; sel.appendChild(o);
  }
  sel.value = current;
  $("#batch-effort").value = m.effort || "low";
}

async function saveBatchClaudeSettings(after) {
  const m = engine("translators", $("#batch-translator").value);
  if (m?.type !== "claude") return;
  m.model = $("#batch-model").value; m.effort = $("#batch-effort").value;
  try {
    config = await api("PUT", "/api/config", config);
    showTranslatorSettings();           // otherwise the form of the page would write the old value back at the next start
    showBatchClaudeSettings();
    await after();
  } catch (err) { $("#batch-message").textContent = err.message; }
}

async function batchAction(action, body) {
  if (!currentBatch) return;
  try { await api("POST", `/api/batches/${currentBatch}/${action}`, body || {}); refreshBatch(); }
  catch (err) { alert(err.message); }
}
/* Puts a video in error (or all of them) back in the list to process; also after the end of the batch, which starts again for them. */
let finishedBatch = null;
async function retryBatch(path) {
  const id = currentBatch || finishedBatch;
  if (!id) return;
  try {
    await api("POST", `/api/batches/${id}/retry`, path ? { path } : {});
    if (currentBatch) refreshBatch(); else followBatch(id);
  } catch (err) { alert(err.message); }
}
$("#btn-batch-retry").addEventListener("click", () => retryBatch());
$("#btn-batch-suspend").addEventListener("click", () => batchAction("suspend"));
/* Suspended batch: its settings for what follows, editable here or with the lists at the top of the page. */
function showSuspended(b) {
  batchSuspendedFor = b.id; suspendedBatch = b;
  fillSelect($("#batch-susp-transcription"), config.transcriptions, b.options?.transcription || config.active_transcription);
  fillSelect($("#batch-susp-translator"), config.translators, b.options?.translator || config.active_translator);
  fillLanguages($("#batch-susp-languages"), b.options?.languages || config.target_languages || ["fr"]);
  $("#batch-suspended").hidden = false;
}
for (const [top, panel] of [["#opt-transcription", "#batch-susp-transcription"], ["#opt-translator", "#batch-susp-translator"]])
  $(top).addEventListener("change", () => { if (!$("#batch-suspended").hidden) $(panel).value = $(top).value; });

$("#btn-batch-resume").addEventListener("click", async () => {
  const translator = $("#batch-susp-translator").value, m = engine("translators", translator);
  const body = { translator, transcription: $("#batch-susp-transcription").value, languages: readLanguages($("#batch-susp-languages")) };
  const changed = translator !== (suspendedBatch?.options?.translator || config.active_translator);
  if (changed) body.pause = defaultPause(m);       // switching to a paid API gives the estimate again before spending
  if (changed && suspendedBatch?.phase === 2 && m?.type === "claude"
      && !confirm(T("ui.batch.confirm_paid", { name: engineName(m), model: m.model || T("ui.batch.default_model") }))) return;
  await saveTranslatorSettings().catch(() => {});
  batchAction("resume", body);
});
$("#btn-batch-cancel").addEventListener("click", () => { if (confirm(T("ui.batch.confirm_cancel"))) batchAction("cancel"); });
$("#btn-batch-translate").addEventListener("click", async () => {
  await batchClaudeSave;
  const translator = $("#batch-translator").value;
  batchAction("translate", { translator, languages: readLanguages($("#batch-languages")) }).then(() => { $("#batch-pause").hidden = true; });
});
$("#btn-batch-no-translation").addEventListener("click", () => batchAction("translate", { translator: "none" }).then(() => { $("#batch-pause").hidden = true; }));

// --- Following a task ---------------------------------------------------------------------------------------------

let pausedFor = null;          // id of the task whose pause panel is built

// State of the running work, always shown in the header.
const PROCESS_STEPS = { source: "ui.step.source", transcription: "ui.transcription", segmentation: "ui.step.split", review: "ui.step.review", translation: "ui.translation", files: "ui.step.files", dubbing: "ui.step.dubbing" };
let batchProcess = null, taskProcess = null, parallelBatch = false;
function showProcess() {
  // Several videos at once: the detail of one would teach nothing, the batch sums up.
  const z = $("#process"), text = [batchProcess, parallelBatch ? null : taskProcess].filter(Boolean).join(" · ");
  z.hidden = !text;
  z.classList.toggle("multiline", parallelBatch);
  z.textContent = text;
  z.title = text;
}

function showRetry(t) {
  const possible = t && t.state === "error" && !t.options?.batch;
  for (const b of [$("#btn-retry"), $("#btn-retry-log")]) { b.hidden = !possible; b.dataset.task = possible ? t.id : ""; }
}

async function retry(id) {
  try { await follow((await api("POST", `/api/tasks/${id}/retry`)).id); updateHistory(); }
  catch (err) { alert(T("ui.err.retry", { error: err.message })); }
}

function follow(id) {
  currentTask = id; logRead = 0; pausedFor = null; showRetry(null);
  $("#steps").hidden = false;                 // hidden during a parallel translation
  $("#log-text").textContent = ""; $("#log-timer").textContent = ""; $("#result").hidden = true; $("#pause").hidden = true; $("#task").hidden = false;
  for (const li of document.querySelectorAll("#steps li")) li.className = "";
  clearInterval(timer);
  timer = setInterval(refresh, 1000);
  return refresh();
}

async function refresh() {
  if (!currentTask) return;
  let t;
  try { t = await api("GET", `/api/tasks/${currentTask}?since=${logRead}`); } catch (err) { return; }
  if (parallelMode) return;
  $("#task-title").textContent = fileName(t.source); $("#task-title").title = t.source;
  const state = $("#task-state");
  state.textContent = label("ui.task_state.", t.state);
  state.className = "state " + t.state;
  $("#btn-cancel").hidden = !["queued", "running", "paused"].includes(t.state);
  showRetry(t);
  const order = ["source", "transcription", "segmentation", "review", "translation", "files", "dubbing", "mux"];
  const i = order.indexOf(t.step), until = t.options?.until;
  const show = { review: !!t.options?.review, dubbing: ["dubbing", "mux"].includes(until), mux: until === "mux" };
  for (const [name, visible] of Object.entries(show)) document.querySelector(`#steps li[data-step=${name}]`).hidden = !visible;
  document.querySelectorAll("#steps li").forEach((li, k) => {
    li.className = t.state === "done" || k < i ? "done" : (k === i && t.state === "running" ? "current" : "");
  });
  if (t.state === "paused" && !t.options?.batch) { if (pausedFor !== t.id) showPause(t); }   // a batch is translated through its own panel
  else $("#pause").hidden = true;
  if (t.log.length) {
    const j = $("#log");
    const atBottom = j.scrollTop + j.clientHeight >= j.scrollHeight - 20;
    const text = $("#log-text");
    text.textContent += (text.textContent ? "\n" : "") + t.log.join("\n");
    if (atBottom) j.scrollTop = j.scrollHeight;
  }
  logRead = t.log_total;
  // Time elapsed on this video since the start of the pass (transcription, or full translation).
  const chrono = t.state === "running" && t.pass_started ? `⏱ ${formatDuration(Math.max(0, Math.round(Date.now() / 1000 - t.pass_started)))}` : "";
  $("#log-timer").textContent = chrono ? "  " + chrono : "";
  const sourceName = fileName(t.source);
  const taskLeft = remainingText(t.remaining_s);
  const stepName = t.step === "mux" ? "MKV" : T(PROCESS_STEPS[t.step] || "ui.process.preparing");
  taskProcess = t.state === "running" ? `${T("ui.process.step", { step: stepName, file: sourceName })} ${chrono}${taskLeft ? " · " + taskLeft : ""}`.trim()
    : t.state === "queued" ? T("ui.process.waiting", { file: sourceName }) : t.state === "paused" && !t.options?.batch ? T("ui.process.paused", { file: sourceName }) : null;
  showProcess();
  if (t.state === "done" && t.result) showResult(t.result);
  if (!["queued", "running", "paused"].includes(t.state)) {
    clearInterval(timer); timer = null;
    const finished = currentTask; currentTask = null;
    updateHistory(); updateStatus();
    if (finished && t.state === "done") document.title = T("ui.title_done");
  }
}

// --- Pause before the translation: estimate and choice of the translator --------------------------------------------

function showPause(t) {
  pausedFor = t.id;
  const z = $("#pause");
  z.hidden = false;
  $("#btn-translate").textContent = t.options?.until === "mux" ? T("ui.translate_dub") : T("ui.translate");
  $("#pause-summary").textContent = t.prepared ? T("ui.task.prepared", { n: t.prepared.count, lang: languageName(t.prepared.language), file: t.prepared.source_srt }) : "";
  $("#pause-message").textContent = "";
  fillSelect($("#pause-translator"), config.translators, t.options?.translator || config.active_translator);
  fillLanguages($("#pause-languages"), t.options?.languages || config.target_languages || ["fr"]);
  showCosts(t.estimate);
  const refreshEstimate = async () => {
    try { showCosts(await api("GET", `/api/tasks/${t.id}/estimate?languages=${encodeURIComponent(readLanguages($("#pause-languages")).join(","))}`)); }
    catch (err) { $("#pause-message").textContent = err.message; }
  };
  $("#pause-languages").oninput = refreshEstimate;
  $("#pause-translator").onchange = () => showCosts(lastEstimate);
}

let lastEstimate = null;
function showCosts(e) { lastEstimate = e; showCostsIn($("#pause-costs"), e, $("#pause-translator").value); }

const dollars = (x) => formatNumber(x, { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });

function showCostsIn(z, e, chosen) {
  z.innerHTML = "";
  if (!e || !e.languages) return;
  if (!e.languages.length) { z.textContent = T("ui.cost.nothing"); return; }
  const head = document.createElement("span");
  head.textContent = (e.videos ? T("ui.cost.videos", { n: e.videos }) + ", " : "") + T("ui.cost.summary", { n: e.count, langs: e.languages.map(languageName).join(", "), requests: e.requests, tokens_in: formatNumber(e.input_tokens), tokens_out: formatNumber(e.output_tokens) });
  z.appendChild(head);
  const kept = e.translators.find((tr) => tr.id === chosen);
  if (kept && kept.type !== "none") {
    const big = document.createElement("div"); big.className = "main-cost";
    big.textContent = kept.cost_usd === null || kept.cost_usd === undefined ? T("ui.cost.unknown_cap")
      : kept.cost_usd === 0 ? T("ui.cost.free_cap") + (kept.duration_s ? ", " + T("ui.cost.about", { time: formatDuration(kept.duration_s) }) : "")
      : `≈ ${dollars(kept.cost_usd)}`;
    z.prepend(big);
  }
  for (const tr of e.translators) {
    if (tr.type === "none") continue;
    const s = document.createElement("span");
    s.className = tr.id === chosen ? "chosen" : "";
    const cost = tr.cost_usd === null || tr.cost_usd === undefined ? T("est.unknown_price") : tr.cost_usd === 0 ? T("est.free") : `≈ ${dollars(tr.cost_usd)}`;
    s.textContent = `${tr.name}${tr.model ? " (" + tr.model + ")" : ""} : ${cost}${tr.duration_s ? ", ≈ " + formatDuration(tr.duration_s) : ""}`;
    z.appendChild(s);
  }
}

async function resume(translator) {
  if (!currentTask) return;
  const msg = $("#pause-message");
  $("#btn-translate").disabled = $("#btn-no-translation").disabled = true;
  try {
    await api("POST", `/api/tasks/${currentTask}/translate`, { translator, languages: readLanguages($("#pause-languages")) });
    $("#pause").hidden = true; pausedFor = null;
  } catch (err) { msg.textContent = err.message; }
  finally { $("#btn-translate").disabled = $("#btn-no-translation").disabled = false; }
}
$("#btn-translate").addEventListener("click", () => resume($("#pause-translator").value));
$("#btn-no-translation").addEventListener("click", () => resume("none"));

function showResult(r) {
  const z = $("#result");
  z.hidden = false;
  z.innerHTML = "";
  const files = r.files || [{ language: r.language, path: r.srt, count: r.count, translated: r.translated }];
  const title = document.createElement("strong");
  const languages = files.map((f) => languageName(f.language)).join(", ");
  // Below 5 s, everything was taken from work already done (same threshold as timing.py). The duration stops at the subtitles: meaningless after a dubbing.
  title.textContent = r.mkv ? T("ui.result.dubbed", { langs: languages }) : r.duration_s < 5 ? T("ui.result.already", { langs: languages })
    : T("ui.result.created", { langs: languages, time: formatDuration(r.duration_s) });
  z.appendChild(title);
  z.appendChild(button(T("ui.result.open_folder"), () => api("POST", "/api/open", { path: r.srt })));
  z.appendChild(button(r.mkv ? T("ui.result.open_mkv") : T("ui.result.open_video"), () => api("POST", "/api/open", { path: r.mkv || r.video })));
  if (r.mkv) {
    const p = document.createElement("div");
    p.className = "path"; p.textContent = `${r.replaced ? T("ui.result.replaced") : T("ui.dots.mkv")} → ${r.mkv}`;
    z.appendChild(p);
  }
  for (const f of files) {
    const p = document.createElement("div");
    p.className = "path"; p.textContent = T("ui.result.file", { lang: languageName(f.language) + (f.translated ? "" : " (" + T("ui.result.spoken") + ")"), n: f.count, path: f.path });
    z.appendChild(p);
  }
}

function button(text, action) {
  const b = document.createElement("button");
  b.type = "button"; b.className = "secondary small"; b.textContent = text;
  b.addEventListener("click", () => action().catch((e) => alert(e.message)));
  return b;
}

for (const b of [$("#btn-retry"), $("#btn-retry-log")]) b.addEventListener("click", () => retry(b.dataset.task));
$("#btn-cancel").addEventListener("click", () => { if (currentTask) api("POST", `/api/tasks/${currentTask}/cancel`); });

let fullHistory = false;
const openLogs = new Set();      // open logs: the history is rebuilt at the end of each task

/* The log of a past task opens under its row; the card of the running task is not touched. */
function logAccordion(t, title) {
  const text = document.createElement("pre"); text.className = "log"; text.hidden = true;
  let zone = null;                      // created at the first opening (a batch can count hundreds of rows)
  const fetchLog = async () => {
    try { text.textContent = (await api("GET", `/api/tasks/${t.id}`)).log.join("\n") || T("ui.log.empty"); }
    catch (err) { text.textContent = T("ui.log.unreadable", { error: err.message }); }
  };
  const toggle = async () => {
    const open = !zone || zone.hidden;
    if (open && !zone) { text.hidden = false; watchScrolling(text); zone = text.parentElement; }
    else zone.hidden = !open;
    b.textContent = T("ui.log.button") + (open ? " ▾" : " ▸");
    b.setAttribute("aria-expanded", String(open));
    if (!open) { openLogs.delete(t.id); return; }
    openLogs.add(t.id);
    text.textContent = T("ui.log.loading");
    await fetchLog();
  };
  const b = button(T("ui.log.button") + " ▸", async () => toggle());
  b.setAttribute("aria-expanded", "false");
  title.classList.add("clickable");
  title.onclick = toggle;
  return {
    button: b,
    install(li) { li.appendChild(text); return openLogs.has(t.id) ? toggle() : null; },
    reload() { if (zone && !zone.hidden) fetchLog(); },
    remove() { (zone || text).remove(); b.remove(); },
  };
}

/* Duration of a pass, without the pause: computed by the server from the log (duration_s). */
const durationCache = new Map();
async function passDuration(id, state) {
  const key = `${id}:${state}`;
  if (!durationCache.has(key)) {
    try { const d = (await api("GET", `/api/tasks/${id}?since=1000000000`)).duration_s; durationCache.set(key, d === null || d === undefined ? "" : formatDuration(d)); }
    catch (err) { return ""; }
  }
  return durationCache.get(key);
}
$("#btn-history").addEventListener("click", () => { fullHistory = !fullHistory; updateHistory(); });

async function updateHistory() {
  const list = await api("GET", "/api/tasks").catch(() => []);
  const ul = $("#history"), scroll = ul.scrollTop, loads = [];
  ul.innerHTML = "";
  const historyButton = $("#btn-history");
  historyButton.hidden = list.length <= 5;
  historyButton.textContent = fullHistory ? T("ui.history.less") : T("ui.history.more", { n: list.length });
  for (const t of fullHistory ? list : list.slice(0, 5)) {
    const li = document.createElement("li");
    const state = document.createElement("span"); state.className = "state " + t.state;
    state.textContent = label("ui.history_state.", t.state);
    const src = document.createElement("span"); src.className = "src"; src.textContent = fileName(t.source); src.title = t.error || t.source;
    const date = document.createElement("span"); date.className = "date"; date.textContent = formatDate(t.finished || t.started || t.created);
    if (!["running", "queued"].includes(t.state) && t.duration_s !== null && t.duration_s !== undefined) { date.textContent += " · " + formatDuration(t.duration_s); date.title = T("ui.history.duration_title"); }
    li.append(state, src, date);
    const accordion = logAccordion(t, src);
    li.appendChild(accordion.button);
    if (t.result?.srt) li.appendChild(button(T("ui.folder.title"), () => api("POST", "/api/open", { path: t.result.srt })));
    if (t.state === "error" && !t.options?.batch) li.appendChild(button(T("ui.retry"), () => retry(t.id)));
    ul.appendChild(li);
    loads.push(accordion.install(li));
  }
  await Promise.all(loads);      // without this, the content is still too short to restore the position
  ul.scrollTop = scroll;
}

// --- Settings -----------------------------------------------------------------------------------------------------

const dlg = $("#dlg-settings");
$("#btn-settings").addEventListener("click", () => { fillSettings(); dlg.showModal(); });
$("#btn-close-settings").addEventListener("click", () => dlg.close());
$("#btn-cancel-settings").addEventListener("click", () => dlg.close());

function fillSettings() {
  const c = config, s = c.subtitles || {};
  $("#cfg-output-dir").value = c.output_dir || ""; $("#cfg-language").value = c.language || "";
  $("#cfg-youtube").value = JSON.stringify((c.youtube || {}).command || ["yt-dlp"]);
  $("#cfg-chunk").value = (c.translation || {}).chunk_size || 25;
  $("#cfg-idle").value = (c.web || {}).idle_min ?? 60;
  $("#cfg-cpl").value = s.chars_per_line ?? 42; $("#cfg-lines").value = s.lines ?? 2; $("#cfg-dmax").value = s.max_duration ?? 6;
  $("#cfg-dmin").value = s.min_duration ?? 1; $("#cfg-pause").value = s.split_pause ?? 0.6; $("#cfg-cps").value = s.reading_cps ?? 17;
  const n = { audio: true, subtitles: true, transcription: false, logs: false, logs_age_h: 48, ...(c.cleanup || {}) };
  $("#cfg-clean-audio").checked = !!n.audio; $("#cfg-clean-subtitles").checked = !!n.subtitles;
  $("#cfg-clean-transcription").checked = !!n.transcription; $("#cfg-clean-logs").checked = !!n.logs; $("#cfg-clean-age").value = n.logs_age_h;
  showCleanupSummary();
  const lt = $("#transcription-list"); lt.innerHTML = "";
  for (const m of c.transcriptions || []) lt.appendChild(engineRow("transcription", m, m.id === c.active_transcription));
  const ld = $("#translator-list"); ld.innerHTML = "";
  for (const m of c.translators || []) ld.appendChild(engineRow("translator", m, m.id === c.active_translator));
  $("#settings-message").textContent = "";
}

function engineRow(kind, m, active) {
  const el = document.getElementById(kind === "transcription" ? "tpl-transcription" : "tpl-translator").content.firstElementChild.cloneNode(true);
  translatePage(el);
  for (const field of el.querySelectorAll("[data-field]")) {
    const k = field.dataset.field;
    if (k === "active") field.checked = active;
    else if (field.type === "checkbox") field.checked = !!m[k];
    else if (k === "command") field.value = m.command ? JSON.stringify(m.command) : "";
    else field.value = m[k] ?? "";
  }
  el.querySelector("[data-action=delete]").addEventListener("click", () => el.remove());
  el.querySelector("[data-action=test]").addEventListener("click", async (ev) => {
    const feedback = el.querySelector("[data-role=feedback]");
    feedback.textContent = T("ui.eng.testing"); ev.target.disabled = true;
    try {
      const r = await api("POST", "/api/test", { kind, engine: readEngine(el) });
      feedback.textContent = (r.ok ? "✓ " : "✗ ") + r.detail;
    } catch (err) { feedback.textContent = "✗ " + err.message; }
    finally { ev.target.disabled = false; }
  });
  return el;
}

function readEngine(el) {
  const m = {};
  for (const field of el.querySelectorAll("[data-field]")) {
    const k = field.dataset.field;
    if (k === "active") continue;
    if (field.type === "checkbox") m[k] = field.checked;
    else if (k === "command") { const v = field.value.trim(); if (v) m.command = JSON.parse(v); }
    else { const v = field.value.trim(); if (v || k === "id" || k === "name" || k === "key") m[k] = v; }
  }
  return m;
}

$("#btn-add-transcription").addEventListener("click", () => $("#transcription-list").appendChild(engineRow("transcription", { id: "", name: "", url: "", key: "" }, false)));
$("#btn-add-translator").addEventListener("click", () => $("#translator-list").appendChild(engineRow("translator", { id: "", name: "", type: "openai", model: "", url: "", key: "" }, false)));

$("#btn-save-settings").addEventListener("click", async () => {
  const msg = $("#settings-message");
  try {
    const c = JSON.parse(JSON.stringify(config));
    c.output_dir = $("#cfg-output-dir").value.trim(); c.language = $("#cfg-language").value.trim();
    c.youtube = { ...(c.youtube || {}), command: JSON.parse($("#cfg-youtube").value) };
    c.translation = { ...(c.translation || {}), chunk_size: Number($("#cfg-chunk").value) || 25 };
    c.web = { ...(c.web || {}), idle_min: Number($("#cfg-idle").value) || 0 };
    c.subtitles = { chars_per_line: Number($("#cfg-cpl").value), lines: Number($("#cfg-lines").value), max_duration: Number($("#cfg-dmax").value),
                    min_duration: Number($("#cfg-dmin").value), split_pause: Number($("#cfg-pause").value), min_gap: (c.subtitles || {}).min_gap ?? 0.08,
                    reading_cps: Number($("#cfg-cps").value) };
    c.cleanup = readCleanup();
    const read = (sel) => {
      const list = [], blocks = [...document.querySelectorAll(sel + " .engine")];
      let active = null;
      for (const el of blocks) {
        const m = readEngine(el);
        if (!m.id) throw new Error(T("ui.cfg.id_required"));
        if (list.some((x) => x.id === m.id)) throw new Error(T("ui.cfg.duplicate_id", { id: m.id }));
        if (el.querySelector("[data-field=active]").checked) active = m.id;
        list.push(m);
      }
      if (!active && list.length) active = list[0].id;
      return [list, active];
    };
    [c.transcriptions, c.active_transcription] = read("#transcription-list");
    [c.translators, c.active_translator] = read("#translator-list");
    config = await api("PUT", "/api/config", c);
    msg.textContent = T("ui.cfg.saved");
    fillSelect($("#opt-transcription"), config.transcriptions, config.active_transcription);
    fillSelect($("#opt-translator"), config.translators, config.active_translator);
    $("#opt-output-dir").value = config.output_dir; $("#opt-language").value = config.language || "";
    updateOutputZone();
    showTranslatorSettings();
    updateStatus();
    setTimeout(() => dlg.close(), 500);
  } catch (err) { msg.textContent = T("log.error", { error: err.message }); }
});

// --- Cleanup of the working folder ---------------------------------------------------------------------------------

function readCleanup() {
  return { audio: $("#cfg-clean-audio").checked, subtitles: $("#cfg-clean-subtitles").checked, transcription: $("#cfg-clean-transcription").checked,
           logs: $("#cfg-clean-logs").checked, logs_age_h: Number($("#cfg-clean-age").value) || 0 };
}

function formatBytes(o) {
  return o >= 1e9 ? T("ui.size.gb", { n: formatNumber(o / 1e9, { maximumFractionDigits: 2 }) }) : o >= 1e6 ? T("ui.size.mb", { n: formatNumber(Math.round(o / 1e6)) }) : T("ui.size.kb", { n: formatNumber(Math.round(o / 1e3)) });
}

function showSummary(categories) {
  for (const el of document.querySelectorAll("[data-summary]")) {
    const b = categories[el.dataset.summary];
    el.textContent = b ? `(${T("ui.cfg.files", { n: b.files })}, ${formatBytes(b.bytes)})` : "";
  }
}

async function showCleanupSummary() {
  try { showSummary((await api("GET", "/api/cleanup?output_dir=" + encodeURIComponent($("#cfg-output-dir").value.trim()))).categories); }
  catch (err) { $("#cleanup-feedback").textContent = err.message; }
}

$("#btn-clean").addEventListener("click", async (ev) => {
  const feedback = $("#cleanup-feedback");
  ev.target.disabled = true; feedback.textContent = T("ui.cfg.cleaning");
  try {
    const r = await api("POST", "/api/cleanup", { options: readCleanup(), output_dir: $("#cfg-output-dir").value.trim() });
    const total = Object.values(r.deleted).reduce((a, b) => ({ files: a.files + b.files, bytes: a.bytes + b.bytes }), { files: 0, bytes: 0 });
    feedback.textContent = total.files ? T("ui.cfg.cleaned", { n: total.files, size: formatBytes(total.bytes) }) : T("ui.cfg.nothing_to_clean");
    showSummary(r.categories);
  } catch (err) { feedback.textContent = T("ui.failed", { error: err.message }); }
  finally { ev.target.disabled = false; }
});

// --- Miscellaneous ------------------------------------------------------------------------------------------------

function fileName(source) {
  const s = String(source || "");
  return /^https?:/.test(s) ? s : s.split(/[\\/]/).pop();
}

function formatDuration(s) {
  s = Math.round(Number(s) || 0);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h} h ${String(m).padStart(2, "0")} min` : m ? `${m} min ${String(sec).padStart(2, "0")} s` : `${sec} s`;
}
function formatDate(t) {
  if (!t) return "";
  const d = new Date(t * 1000);
  return d.toLocaleDateString(I18N.language, { day: "2-digit", month: "2-digit" }) + " " + d.toLocaleTimeString(I18N.language, { hour: "2-digit", minute: "2-digit" });
}
for (const id of ["opt-transcription", "opt-translator"]) $("#" + id).addEventListener("change", updateStatus);
$("#opt-translator").addEventListener("change", () => { showTranslatorSettings(); updateDefaultPause(); });
setInterval(updateStatus, 15000);
/* The commands are enabled only once what already runs is known: the content no longer changes under the cursor. */
loadTranslations().then(() => {
  updateThemeButton();
  for (const s of ["#folder-tree", "#batch-videos", "#log", "#history"]) watchScrolling($(s));
  fillSpokenLanguages();
  fillLanguageChoice();
  return load();
}).then(() => {
  document.body.classList.remove("loading");
  const page = document.querySelector("main");
  page.inert = false; page.removeAttribute("aria-busy");
  $("#btn-settings").disabled = false;
}).catch((err) => {
  document.querySelector("#loading .spinner").hidden = true;
  $("#loading-text").textContent = T("ui.service_unreachable", { error: err.message });
});
