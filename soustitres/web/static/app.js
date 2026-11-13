/* Interface du générateur de sous-titres : une tâche à la fois, suivie par interrogation régulière de l'API. */
"use strict";

const $ = (s) => document.querySelector(s);
let config = null;
let tacheCourante = null;      // id de la tâche affichée
let journalLu = 0;
let minuteur = null;

async function api(methode, chemin, corps) {
  const r = await fetch(chemin, { method: methode, headers: corps !== undefined ? { "Content-Type": "application/json" } : {},
                                  body: corps !== undefined ? JSON.stringify(corps) : undefined });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) { const e = new Error(d.erreur || `${r.status} ${r.statusText}`); e.donnees = d; throw e; }
  return d;
}

/* « Mon serveur (détails…) » devient « Mon serveur » dans l'en-tête ; le nom complet reste au survol. */
function nomCourt(m) { return (m?.nom || m?.id || "?").replace(/\s*\(.*$/, "") || m?.nom; }

function moteur(liste, id) { return (config?.[liste] || []).find((m) => m.id === id); }

// --- Thème clair ou sombre (choix mémorisé dans ce navigateur, sinon celui du système) -------------------------

function themeActuel() {
  return document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
}
function majBoutonTheme() {
  const sombre = themeActuel() === "dark", b = $("#btn-theme");
  b.textContent = sombre ? "☀" : "☾";
  b.title = sombre ? "Passer au thème clair" : "Passer au thème sombre";
  b.setAttribute("aria-label", b.title);
}
$("#btn-theme").addEventListener("click", () => {
  const t = themeActuel() === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("theme", t); } catch (e) { /* navigation privée : valable pour cette page seulement */ }
  majBoutonTheme();
});
majBoutonTheme();

// --- Zones à défilement : ombre et flèche tant qu'il reste du contenu -------------------------------------------

function surveillerDefilement(el) {
  const zone = document.createElement("div"); zone.className = "zone-defil";
  el.parentNode.insertBefore(zone, el);
  zone.appendChild(el);
  const creerFleche = (classe, texte, titre, sens) => {
    const b = document.createElement("button");
    b.type = "button"; b.className = classe; b.textContent = texte; b.hidden = true; b.title = titre;
    b.addEventListener("click", () => el.scrollBy({ top: sens * el.clientHeight * 0.8, behavior: "smooth" }));
    zone.appendChild(b);
    return b;
  };
  const monter = creerFleche("fleche-defil monter", "▴", "Remonter", -1), descendre = creerFleche("fleche-defil", "▾", "Descendre", 1);
  let prevu = false;
  const maj = () => {
    prevu = false;
    const haut = el.scrollTop > 2, bas = el.scrollTop + el.clientHeight < el.scrollHeight - 2;
    zone.classList.toggle("haut", haut);
    zone.classList.toggle("bas", bas);
    monter.hidden = !haut;
    descendre.hidden = !bas;
  };
  const planifier = () => { if (!prevu) { prevu = true; requestAnimationFrame(maj); } };
  el.addEventListener("scroll", planifier, { passive: true });
  new ResizeObserver(planifier).observe(el);
  new MutationObserver(planifier).observe(el, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ["hidden"] });
  maj();
}
for (const s of ["#dossier-arbre", "#lot-videos", "#journal", "#historique"]) surveillerDefilement($(s));

// --- Chargement -----------------------------------------------------------------------------------------------

async function charger() {
  config = await api("GET", "/api/config");
  remplirSelect($("#opt-transcription"), config.transcriptions, config.transcription_active);
  remplirSelect($("#opt-traducteur"), config.traducteurs, config.traducteur_actif);
  $("#opt-langue").value = config.langue || "";
  $("#opt-sortie").value = config.sortie || "";
  remplirLangues($("#opt-langues"), config.langues_cibles || ["fr"]);
  $("#opt-dest-a-cote").checked = config.destination?.a_cote ?? true;
  $("#opt-dest-sortie").checked = config.destination?.sortie ?? false;
  majZoneSortie();
  majPauseParDefaut();
  afficherReglagesTraducteur();
  const voulu = document.querySelector(`input[name=objectif][value=${doublageActif() && config.objectif === "assemblage" ? "assemblage" : "traduction"}]`);
  voulu.checked = true;
  majObjectif();
  $("#opt-relecture").checked = claudeDisponible(); $("#zone-relecture").hidden = !claudeDisponible();
  const github = (config.web || {}).github || "";          // lien grisé tant que le dépôt public n'existe pas
  const lien = $("#lien-github");
  lien.href = github || "#";
  lien.setAttribute("aria-disabled", github ? "false" : "true");
  lien.title = github ? "Dépôt GitHub du projet" : "Dépôt GitHub : pas encore publié";
  await majEtat();
  await majHistorique();
  await verifierDernierLot();
}

// --- Langues cibles (cases + champ libre pour les autres codes) -------------------------------------------------

const LANGUES = [["fr", "français"], ["en", "anglais"], ["de", "allemand"], ["es", "espagnol"], ["it", "italien"], ["pt", "portugais"], ["nl", "néerlandais"]];
const NOMS_LANGUES = Object.fromEntries(LANGUES.concat([["pl", "polonais"], ["ru", "russe"], ["uk", "ukrainien"], ["tr", "turc"], ["ar", "arabe"], ["ja", "japonais"], ["zh", "chinois"], ["ko", "coréen"]]));

function remplirLangues(zone, codes) {
  zone.innerHTML = "";
  for (const [code, nom] of LANGUES) {
    const l = document.createElement("label"); l.className = "case";
    const c = document.createElement("input"); c.type = "checkbox"; c.value = code; c.checked = codes.includes(code);
    l.append(c, document.createTextNode(" " + nom));
    zone.appendChild(l);
  }
  const l = document.createElement("label"); l.className = "case";
  const autres = document.createElement("input"); autres.type = "text"; autres.dataset.role = "autres"; autres.spellcheck = false;
  autres.placeholder = "autres : pl, ru…"; autres.title = "Codes de langue séparés par des virgules";
  autres.value = codes.filter((c) => !LANGUES.some(([k]) => k === c)).join(", ");
  l.append(document.createTextNode("autres "), autres);
  zone.appendChild(l);
}

function lireLangues(zone = $("#opt-langues")) {
  const codes = [...zone.querySelectorAll("input[type=checkbox]:checked")].map((c) => c.value);
  for (const c of zone.querySelector("[data-role=autres]").value.toLowerCase().split(/[\s,;]+/)) if (c && !codes.includes(c)) codes.push(c);
  return codes;
}

function nomLangue(code) { return NOMS_LANGUES[code] || code; }

/* Pause avant la traduction : cochée par défaut sauf pour une IA locale (gratuite) ou « aucun ». */
function pauseParDefaut(m) {
  if (!m || m.type === "aucun") return false;
  return !(m.type === "openai" && /127\.0\.0\.1|localhost|\[::1\]/.test(m.url || ""));
}
function majPauseParDefaut() { $("#opt-pause").checked = pauseParDefaut(moteur("traducteurs", $("#opt-traducteur").value)); }

function remplirSelect(sel, liste, actif) {
  sel.innerHTML = "";
  for (const m of liste || []) {
    const o = document.createElement("option");
    o.value = m.id; o.textContent = m.nom || m.id;
    if (m.id === actif) o.selected = true;
    sel.appendChild(o);
  }
}

async function majEtat() {
  try {
    const e = await api("GET", "/api/etat").catch((err) => { $("#coupure").hidden = false; throw err; });
    $("#coupure").hidden = true;
    // Un lot ouvert a ses propres réglages : l'en-tête montre ce qui tourne vraiment, pas la liste de la page.
    const tr = moteur("transcriptions", e.lot?.options?.transcription || $("#opt-transcription").value || e.transcription_active);
    const trad = moteur("traducteurs", e.lot?.options?.traducteur || $("#opt-traducteur").value || e.traducteur_actif);
    const pTr = $("#puce-transcription");
    pTr.textContent = "Transcription : " + nomCourt(tr); pTr.title = tr?.nom || "";
    pTr.className = "puce " + (tr?.commande ? ({ pret: "ok", demarrage: "attention", arrete: "" }[e.transcription_locale?.[tr.id]] || "") : "ok");
    if (tr?.commande) pTr.title += " · serveur local : " + (e.transcription_locale?.[tr.id] || "?");
    const pTrad = $("#puce-traducteur");
    pTrad.textContent = "Traduction : " + nomCourt(trad); pTrad.title = trad?.nom || "";
    pTrad.className = "puce ok";
    const pIa = $("#puce-ia");
    if (e.ia_locale && trad?.supervision) {
      pIa.hidden = false;
      pIa.textContent = "IA locale : " + ({ pret: "prête", demarrage: "démarrage", arrete: "arrêtée", arret_securite: "arrêt de sécurité", erreur: "erreur" }[e.ia_locale.etat] || e.ia_locale.etat);
      pIa.className = "puce discret " + ({ pret: "ok", demarrage: "attention", erreur: "erreur", arret_securite: "erreur" }[e.ia_locale.etat] || "");
    } else pIa.hidden = true;
    if (e.tache && !tacheCourante && !modeParallele) await suivre(e.tache.id);
    if (e.lot && !lotCourant) await suivreLot(e.lot.id);
  } catch (err) { console.warn(err); }
}

// --- Réglages du traducteur choisi, affichés sur la page -------------------------------------------------------

const CHAMPS_TRADUCTEUR = {
  claude: [["modele", "Modèle", { list: "modeles-claude", placeholder: "claude-sonnet-5-5" }],
           ["effort", "Effort de réflexion", { select: [["", "défaut (low)"], ["low", "low"], ["medium", "medium"], ["high", "high"]] }]],
  openai: [["url", "URL du serveur", { placeholder: "http://127.0.0.1:8080" }], ["modele", "Modèle", {}],
           ["supervision", "Supervision (démarre l'IA locale)", { placeholder: "vide si aucune" }],
           ["arreter_apres", "Arrêter l'IA locale après la tâche", { case: true }]],
  aucun: [],
};

function afficherReglagesTraducteur() {
  const zone = $("#moteur-trad"), m = moteur("traducteurs", $("#opt-traducteur").value);
  const champs = m ? CHAMPS_TRADUCTEUR[m.type || "openai"] || [] : [];
  zone.innerHTML = "";
  zone.hidden = !champs.length;
  if (!champs.length) return;
  const titre = document.createElement("div");
  titre.className = "titre";
  titre.innerHTML = `<span>Réglages de <strong></strong> (enregistrés au démarrage d'une tâche)</span><span class="aide">clé d'API : dans Réglages</span>`;
  titre.querySelector("strong").textContent = m.nom || m.id;
  zone.appendChild(titre);
  for (const [cle, libelle, o] of champs) {
    const l = document.createElement("label");
    if (o.case) {
      l.className = "case";
      const c = document.createElement("input"); c.type = "checkbox"; c.dataset.cle = cle; c.checked = !!m[cle];
      l.append(c, document.createTextNode(" " + libelle));
    } else {
      l.textContent = libelle;
      let e;
      if (o.select) {
        e = document.createElement("select");
        for (const [v, t] of o.select) { const op = document.createElement("option"); op.value = v; op.textContent = t; e.appendChild(op); }
        e.value = m[cle] ?? "";
      } else {
        e = document.createElement("input"); e.type = "text"; e.value = m[cle] ?? ""; e.spellcheck = false;
        if (o.placeholder) e.placeholder = o.placeholder;
        if (o.list) e.setAttribute("list", o.list);
      }
      e.dataset.cle = cle;
      l.appendChild(e);
    }
    zone.appendChild(l);
  }
}

/* Reporte les champs affichés dans la configuration ; enregistre si quelque chose a changé. */
async function enregistrerReglagesTraducteur() {
  const m = moteur("traducteurs", $("#opt-traducteur").value);
  if (!m || $("#moteur-trad").hidden) return;
  let change = false;
  for (const e of $("#moteur-trad").querySelectorAll("[data-cle]")) {
    const v = e.type === "checkbox" ? e.checked : e.value.trim();
    if ((m[e.dataset.cle] ?? (e.type === "checkbox" ? false : "")) !== v) { m[e.dataset.cle] = v; change = true; }
  }
  if (change) config = await api("PUT", "/api/config", config);
}

// --- Lancement ------------------------------------------------------------------------------------------------

function destinationPage() { return { a_cote: $("#opt-dest-a-cote").checked, sortie: $("#opt-dest-sortie").checked }; }

/* Le champ du dossier de sortie est collé à sa case et masqué quand elle est décochée ; le dossier sert quand même
   aux vidéos YouTube et aux fichiers de travail, d'où le rappel discret. */
function majZoneSortie() {
  const coche = $("#opt-dest-sortie").checked;
  $("#zone-sortie").hidden = !coche;
  $("#sortie-rappel").textContent = coche ? "" : `(vidéos YouTube et travail dans ${$("#opt-sortie").value.trim() || "le dossier de sortie"})`;
}
$("#opt-dest-sortie").addEventListener("change", majZoneSortie);
$("#opt-sortie").addEventListener("input", majZoneSortie);

function optionsTache(source) {
  return { source, transcription: $("#opt-transcription").value, traducteur: $("#opt-traducteur").value,
           langue: $("#opt-langue").value, langues: lireLangues(), sortie: $("#opt-sortie").value.trim(),
           destination: destinationPage(), refaire: $("#opt-refaire").checked, pause: $("#opt-pause").checked, jusqua: objectifPage(), remplacer: remplacerPage() };
}

/* Ce que l'on veut obtenir : tout le reste de la page s'y adapte. */
const objectifPage = () => document.querySelector("input[name=objectif]:checked")?.value || "traduction";
const remplacerPage = () => objectifPage() === "assemblage" && $("#opt-remplacer").checked;

function majObjectif() {
  const dispo = doublageActif(), zone = $("#opt-objectif-doublage");
  zone.disabled = !dispo;
  if (!dispo && zone.checked) document.querySelector("input[name=objectif][value=traduction]").checked = true;
  $("#zone-objectif-doublage").title = dispo ? "" : "Le doublage n'est pas configuré : voir la table [doublage] de config.toml";
  $("#zone-remplacer").hidden = objectifPage() !== "assemblage";
  if ($("#zone-remplacer").hidden) $("#opt-remplacer").checked = false;     // jamais conservé d'une fois sur l'autre : l'original part à la corbeille
  const note = $("#note-objectif"), langues = lireLangues(), sansTraducteur = moteur("traducteurs", $("#opt-traducteur").value)?.type === "aucun";
  if (objectifPage() !== "assemblage") note.textContent = "Résultat : un fichier de sous-titres par langue.";
  else if (sansTraducteur || !langues.length) note.textContent = "Le doublage a besoin d'une traduction : choisissez un traducteur et au moins une langue.";
  else note.textContent = `Résultat : les sous-titres et un MKV avec les deux voix. Doublage en ${nomLangue(langues[0])}, environ 8 min 30 de carte graphique pour 30 min de vidéo.`;
}
for (const r of document.querySelectorAll("input[name=objectif]")) r.addEventListener("change", majObjectif);
$("#opt-traducteur").addEventListener("change", majObjectif);
$("#opt-langues").addEventListener("input", majObjectif);

/* Langues cibles et destination choisies sur la page : mémorisées dans la configuration si elles ont changé. */
async function enregistrerOptionsPage() {
  const langues = lireLangues(), dest = destinationPage();
  const change = objectifPage() !== (config.objectif || "traduction") || JSON.stringify(langues) !== JSON.stringify(config.langues_cibles || ["fr"])
    || JSON.stringify(dest) !== JSON.stringify({ a_cote: config.destination?.a_cote ?? true, sortie: config.destination?.sortie ?? false });
  if (!change) return;
  config.langues_cibles = langues; config.destination = dest; config.objectif = objectifPage();
  config = await api("PUT", "/api/config", config);
}

async function lancer(source) {
  if (!source) return;
  if (!$("#opt-dest-a-cote").checked && !$("#opt-dest-sortie").checked
      && !confirm("Aucune destination cochée : les sous-titres resteront dans le dossier de travail. Continuer ?")) return;
  $("#btn-go").disabled = true;
  try {
    await enregistrerReglagesTraducteur();
    await enregistrerOptionsPage();
    const t = await api("POST", "/api/taches", optionsTache(source));
    $("#apercu").hidden = true;
    suivre(t.id);
  } catch (err) {
    if (err.donnees?.dossier) ouvrirDossier(source);
    else alert("Impossible de lancer : " + err.message);
  }
  finally { $("#btn-go").disabled = false; }
}

$("#form-source").addEventListener("submit", (ev) => { ev.preventDefault(); lancer($("#source").value.trim()); });

let delaiApercu = null;
$("#source").addEventListener("input", () => {
  clearTimeout(delaiApercu);
  const v = $("#source").value.trim();
  if (!/^https?:\/\//.test(v)) { $("#apercu").hidden = true; return; }
  delaiApercu = setTimeout(async () => {
    $("#apercu").hidden = false; $("#apercu").textContent = "Lecture des informations de la vidéo…";
    try {
      const i = await api("POST", "/api/youtube", { url: v, sortie: $("#opt-sortie").value.trim() });
      $("#apercu").textContent = i.ok ? `${i.titre} (${formaterDuree(i.duree)})${i.existe ? " : déjà téléchargée" : ""} → ${i.video}` : "yt-dlp : " + i.detail;
    } catch (err) { $("#apercu").textContent = err.message; }
  }, 600);
});

/* Boîte de dialogue du système ; si aucune n'est disponible, le message invite à saisir le chemin dans le champ. */
async function choisirChemin(type, depart = "") {
  try { return (await api("POST", "/api/dialogue", { type, depart })).chemin || ""; }
  catch (err) { alert(err.message); return ""; }
}

$("#btn-parcourir").addEventListener("click", async () => {
  const chemin = await choisirChemin("fichier");
  if (chemin) { $("#source").value = chemin; $("#source").focus(); }
});
$("#btn-dossier").addEventListener("click", async () => {
  const chemin = await choisirChemin("dossier", $("#opt-sortie").value.trim());
  if (chemin) $("#opt-sortie").value = chemin;
});

// Glisser-déposer : le navigateur ne donne pas le chemin, le fichier est envoyé et copié dans le dossier de sortie.
const depot = $("#depot");
for (const ev of ["dragenter", "dragover"]) depot.addEventListener(ev, (e) => { e.preventDefault(); depot.classList.add("survol"); });
for (const ev of ["dragleave", "drop"]) depot.addEventListener(ev, (e) => { e.preventDefault(); depot.classList.remove("survol"); });
depot.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) envoyerFichier(f); });

function envoyerFichier(fichier) {
  const prog = $("#depot-progression"), barre = prog.querySelector(".barre"), texte = prog.querySelector(".texte");
  prog.hidden = false; barre.style.width = "0"; texte.textContent = `Copie de ${fichier.name}…`;
  const xhr = new XMLHttpRequest();
  const sortie = encodeURIComponent($("#opt-sortie").value.trim());
  xhr.open("PUT", `/api/fichiers?nom=${encodeURIComponent(fichier.name)}&sortie=${sortie}`);
  xhr.upload.onprogress = (e) => { if (e.lengthComputable) { barre.style.width = (100 * e.loaded / e.total).toFixed(0) + "%"; texte.textContent = `Copie de ${fichier.name} : ${(100 * e.loaded / e.total).toFixed(0)} %`; } };
  xhr.onload = () => {
    try {
      const r = JSON.parse(xhr.responseText);
      if (xhr.status >= 400) throw new Error(r.erreur);
      texte.textContent = (r.existait ? "Fichier déjà présent dans le dossier de sortie." : "Copié.") + " Cliquer Démarrer.";
      setTimeout(() => { prog.hidden = true; }, 2500);
      $("#source").value = r.chemin;
      $("#btn-go").focus();
    } catch (err) { texte.textContent = "Échec : " + err.message; }
  };
  xhr.onerror = () => { texte.textContent = "Échec de l'envoi."; };
  xhr.send(fichier);
}

// --- Dossier source : inventaire, sélection, langue par vidéo -------------------------------------------------

let inventaire = null;

$("#btn-dossier-source").addEventListener("click", async () => {
  const chemin = await choisirChemin("dossier_source");
  if (chemin) { $("#source").value = chemin; ouvrirDossier(chemin); }
});
function fermerDossier() {
  if (analyseId) api("POST", `/api/dossier/${analyseId}/annuler`, {}).catch(() => {});
  analyseId = null;
  $("#dossier").hidden = true; $("#dossier-actions").hidden = true; $("#btn-go").hidden = false;
  $("#opt-pause-texte").textContent = "Pause avant la traduction : voir l'estimation du coût, puis choisir le traducteur";
}
$("#btn-fermer-dossier").addEventListener("click", fermerDossier);

let analyseId = null;      // inventaire du dossier en cours

async function ouvrirDossier(chemin) {
  $("#dossier").hidden = false; $("#dossier-actions").hidden = false; $("#btn-go").hidden = true;   // Lancer le lot remplace Démarrer
  inventaire = null;
  $("#dossier-racine").textContent = chemin;
  $("#dossier-arbre").innerHTML = ""; $("#dossier-resume").textContent = ""; $("#dossier-lot-existant").hidden = true;
  $("#btn-lancer-lot").disabled = true;
  $("#opt-pause-texte").textContent = "Tout transcrire d'abord, puis afficher le coût total de toutes les traductions avant de les lancer";
  const sel = $("#dossier-langue-globale");
  sel.innerHTML = $("#opt-langue").innerHTML;       // tout de suite : la liste ne reste pas vide pendant l'analyse
  sel.value = $("#opt-langue").value;
  let a;
  try { a = await api("POST", "/api/dossier", { chemin }); }
  catch (err) { $("#dossier-racine").textContent = chemin + " : " + err.message; return; }
  analyseId = a.id;
  majChargement(a);
  while (analyseId === a.id) {
    let e;
    try { e = await api("GET", `/api/dossier/${a.id}${inventaire ? "" : "?videos=1"}`); }
    catch (err) { $("#dossier-racine").textContent = chemin + " : " + err.message; analyseId = null; $("#dossier-chargement").hidden = true; return; }
    if (analyseId !== a.id) return;
    majChargement(e);
    if (e.videos && !inventaire) construireArbre(e);
    if (inventaire) appliquerLanguesPiste(e.langues);
    if (e.etat === "annulee") { fermerDossier(); return; }
    if (e.etat === "erreur") $("#dossier-racine").textContent = chemin + " : " + e.erreur;
    if (["fini", "erreur"].includes(e.etat)) { analyseId = null; return; }
    await new Promise((fin) => setTimeout(fin, 300));
  }
}

function majChargement(e) {
  const zone = $("#dossier-chargement"), barre = $("#dossier-progression");
  zone.hidden = ["fini", "annulee", "erreur"].includes(e.etat);
  barre.classList.toggle("indefini", e.etat === "parcours");
  if (e.etat === "parcours") {
    barre.querySelector(".barre").style.width = "";
    barre.querySelector(".texte").textContent = `Recherche des vidéos : ${e.vus} trouvée${e.vus > 1 ? "s" : ""}…`;
  } else if (e.etat === "langues") {
    barre.querySelector(".barre").style.width = (100 * e.faites / Math.max(1, e.nb)).toFixed(0) + "%";
    barre.querySelector(".texte").textContent = `Lecture de la langue des pistes audio : ${e.faites}/${e.nb}`;
  }
  $("#btn-dossier-annuler").title = e.etat === "parcours" ? "Abandonner l'analyse de ce dossier"
    : "Arrêter la lecture des langues : la liste reste, les vidéos non lues seront en détection automatique";
}

$("#btn-dossier-annuler").addEventListener("click", () => {
  if (!inventaire) fermerDossier();
  else if (analyseId) api("POST", `/api/dossier/${analyseId}/annuler`, {}).catch(() => {});
});

function construireArbre(e) {
  inventaire = e;
  const nbDossiers = new Set(e.videos.map((v) => v.dossier)).size;
  $("#dossier-racine").textContent = `${e.racine} : ${e.nb} vidéo${e.nb > 1 ? "s" : ""}` + (nbDossiers > 1 ? ` dans ${nbDossiers} dossiers` : "");
  const ex = $("#dossier-lot-existant");
  ex.hidden = !e.lot_existant;
  if (e.lot_existant) {
    ex.innerHTML = "";
    ex.append(document.createTextNode(`Un fichier de lot existe déjà ici (${e.lot_existant}) : `));
    ex.appendChild(bouton("Reprendre ce lot", () => chargerLot(e.lot_existant)));
    ex.append(document.createTextNode(" ou lancer un nouveau lot ci-dessous (le fichier sera remplacé)."));
  }
  const arbre = $("#dossier-arbre");
  arbre.innerHTML = "";
  if (!e.nb) arbre.textContent = "Aucune vidéo trouvée dans ce dossier.";
  const cibles = lireLangues(), plie = nbDossiers > 3, langues = $("#opt-langue").innerHTML, defaut = $("#opt-langue").value || "";
  $("#btn-dossier-plier").textContent = plie ? "Tout déplier" : "Tout replier";
  $("#btn-dossier-plier").hidden = nbDossiers < 2;
  let conteneur = null, courant = null;
  for (const v of e.videos) {
    if (!conteneur || v.dossier !== courant) {
      courant = v.dossier;
      conteneur = ajouterGroupe(arbre, v.dossier, plie);
    }
    const ligne = document.createElement("div"); ligne.className = "video"; ligne.dataset.chemin = v.chemin; ligne.dataset.taille = v.taille;
    const c = document.createElement("input"); c.type = "checkbox";
    c.checked = cibles.some((l) => !v.sous_titres.includes(l));
    c.addEventListener("change", majResumeDossier);
    const nom = document.createElement("span"); nom.className = "nom"; nom.textContent = v.nom; nom.title = v.chemin;
    const taille = document.createElement("span"); taille.className = "taille"; taille.textContent = formaterOctets(v.taille);
    const badges = document.createElement("span");
    for (const code of v.sous_titres) { const b = document.createElement("span"); b.className = "badge" + (cibles.includes(code) ? " ok" : ""); b.textContent = code; b.title = "sous-titres déjà présents"; badges.appendChild(b); }
    const langue = document.createElement("select"); langue.innerHTML = langues; langue.dataset.role = "langue"; langue.value = defaut;
    langue.addEventListener("change", () => { langue.dataset.touche = "1"; });
    ligne.append(c, nom, taille, badges, langue);
    conteneur.appendChild(ligne);
  }
  majResumeDossier();
}

function ajouterGroupe(arbre, dossierNom, plie) {
  const entete = document.createElement("div"); entete.className = "groupe-dossier";
  const lignes = document.createElement("div"); lignes.className = "lignes-dossier"; lignes.hidden = plie;
  const fleche = document.createElement("button"); fleche.type = "button"; fleche.className = "plier"; fleche.textContent = plie ? "▸" : "▾";
  const basculer = () => { lignes.hidden = !lignes.hidden; fleche.textContent = lignes.hidden ? "▸" : "▾"; };
  fleche.addEventListener("click", basculer);
  const c = document.createElement("input"); c.type = "checkbox";
  c.addEventListener("change", () => { for (const x of lignes.querySelectorAll("input[type=checkbox]")) x.checked = c.checked; majResumeDossier(); });
  const nom = document.createElement("span"); nom.className = "nom-dossier"; nom.textContent = dossierNom || "(racine)"; nom.addEventListener("click", basculer);
  const compte = document.createElement("span"); compte.className = "aide"; compte.dataset.role = "compte";
  entete.append(fleche, c, nom, compte);
  arbre.append(entete, lignes);
  return lignes;
}

$("#btn-dossier-plier").addEventListener("click", () => {
  const groupes = [...$("#dossier-arbre").querySelectorAll(".lignes-dossier")];
  const deplier = groupes.some((g) => g.hidden);
  for (const g of groupes) { g.hidden = !deplier; g.previousElementSibling.querySelector(".plier").textContent = deplier ? "▾" : "▸"; }
  $("#btn-dossier-plier").textContent = deplier ? "Tout replier" : "Tout déplier";
});

/* La langue lue sur la piste audio arrive en arrière-plan ; un choix fait à la main n'est jamais écrasé. */
function appliquerLanguesPiste(langues) {
  for (const l of lignesDossier()) {
    const code = langues[l.dataset.chemin], s = l.querySelector("select[data-role=langue]");
    if (!code || s.dataset.touche || s.dataset.piste === code) continue;
    s.dataset.piste = code;
    s.title = "étiquette de la piste audio : " + code;
    if ([...s.options].some((o) => o.value === code)) s.value = code;
  }
}

function lignesDossier() { return [...$("#dossier-arbre").querySelectorAll(".video")]; }
function majResumeDossier() {
  const cochees = lignesDossier().filter((l) => l.querySelector("input[type=checkbox]").checked);
  const octets = cochees.reduce((a, l) => a + Number(l.dataset.taille), 0);
  $("#dossier-resume").textContent = `${cochees.length} vidéo${cochees.length > 1 ? "s" : ""} cochée${cochees.length > 1 ? "s" : ""} (${formaterOctets(octets)})`;
  $("#btn-lancer-lot").disabled = !cochees.length;
  for (const g of $("#dossier-arbre").querySelectorAll(".lignes-dossier")) {
    const cases = [...g.querySelectorAll("input[type=checkbox]")], n = cases.filter((c) => c.checked).length;
    const entete = g.previousElementSibling, c = entete.querySelector("input[type=checkbox]");
    c.checked = n === cases.length; c.indeterminate = n > 0 && n < cases.length;
    entete.querySelector("[data-role=compte]").textContent = `${n}/${cases.length} cochée${n > 1 ? "s" : ""}`;
  }
}
$("#btn-dossier-tout").addEventListener("click", () => { for (const c of $("#dossier-arbre").querySelectorAll("input[type=checkbox]")) c.checked = true; majResumeDossier(); });
$("#btn-dossier-rien").addEventListener("click", () => { for (const c of $("#dossier-arbre").querySelectorAll("input[type=checkbox]")) c.checked = false; majResumeDossier(); });
$("#btn-dossier-manquants").addEventListener("click", () => {
  const cibles = lireLangues();
  for (const l of lignesDossier()) {
    const v = inventaire.videos.find((x) => x.chemin === l.dataset.chemin);
    l.querySelector("input[type=checkbox]").checked = !!v && cibles.some((c) => !v.sous_titres.includes(c));
  }
  majResumeDossier();
});
$("#btn-dossier-appliquer-langue").addEventListener("click", () => {
  for (const s of $("#dossier-arbre").querySelectorAll("select[data-role=langue]")) { s.value = $("#dossier-langue-globale").value; s.dataset.touche = "1"; }
});

$("#btn-lancer-lot").addEventListener("click", async () => {
  const videos = lignesDossier().filter((l) => l.querySelector("input[type=checkbox]").checked)
    .map((l) => ({ chemin: l.dataset.chemin, langue: l.querySelector("select[data-role=langue]").value }));
  if (!videos.length) return;
  $("#btn-lancer-lot").disabled = true;
  try {
    await enregistrerReglagesTraducteur();
    await enregistrerOptionsPage();
    const o = optionsTache("");
    const lot = await api("POST", "/api/lots", { racine: inventaire.racine, videos, transcription: o.transcription, traducteur: o.traducteur,
                                                 langues: o.langues, destination: o.destination, sortie: o.sortie, pause: o.pause, refaire: o.refaire,
                                                 arret: objectifPage(), remplacer: remplacerPage(), relecture: claudeDisponible() && $("#opt-relecture").checked });
    fermerDossier();
    suivreLot(lot.id);
  } catch (err) { alert("Impossible de lancer le lot : " + err.message); }
  finally { $("#btn-lancer-lot").disabled = false; }
});

$("#btn-reprendre-lot").addEventListener("click", async () => {
  const chemin = await choisirChemin("lot", $("#opt-sortie").value.trim());
  if (chemin) chargerLot(chemin);
});

// Lot resté inachevé à la session précédente : proposé dans un encadré en haut de la page.
async function verifierDernierLot() {
  const d = (await api("GET", "/api/lots/dernier").catch(() => ({}))).dernier;
  if (!d) return;
  $("#dernier-lot-titre").textContent = { suspendu: "Un lot était en pause lors de la dernière session.",
                                          attente_traduction: "Un lot attendait sa traduction lors de la dernière session." }[d.etat]
    || "Un lot a été interrompu lors de la dernière session.";
  const trad = moteur("traducteurs", d.traducteur)?.nom || d.traducteur;
  $("#dernier-lot-detail").textContent = `${d.racine} · ${d.faites}/${d.total} vidéo${d.total > 1 ? "s" : ""} terminée${d.faites > 1 ? "s" : ""}`
    + (trad ? ` · traducteur : ${trad}` : "") + (d.mis_a_jour ? ` · dernière activité : ${d.mis_a_jour}` : "");
  $("#btn-dernier-reprendre").onclick = () => chargerLot(d.fichier, true);
  $("#btn-dernier-regler").onclick = () => chargerLot(d.fichier);
  $("#btn-dernier-ignorer").onclick = async () => { await api("POST", "/api/lots/dernier/oublier", {}).catch(() => {}); $("#dernier-lot").hidden = true; };
  $("#dernier-lot").hidden = false;
}

async function chargerLot(fichier, demarrer = false) {
  try {
    const lot = await api("POST", "/api/lots/charger", { fichier });
    if (demarrer) await api("POST", `/api/lots/${lot.id}/reprendre`, {});
    fermerDossier();
    suivreLot(lot.id);
  } catch (err) { alert("Reprise impossible : " + err.message); }
}

// --- Étapes d'une vidéo ---------------------------------------------------------------------------------------

const doublageActif = () => !!config.doublage?.actif;
const claudeDisponible = () => (config.traducteurs || []).some((m) => m.type === "claude");

const veutDoublage = (l) => doublageActif() && ["doublage", "assemblage"].includes(l.options?.arret);

/* Une pastille par étape de la chaîne ; celles qui ne s'appliquent pas à ce lot ne sont pas montrées. */
function etapesDeLaVideo(v, l) {
  const relecture = l.options?.relecture || v.relue;
  return [["Transcription", v.transcrite], ...(relecture ? [["Relecture IA", v.relue], ["Validation des noms", v.relue && v.validee]] : []), ["Traduction", v.traduite],
          ...(veutDoublage(l) || v.doublee || v.assemblee ? [["Doublage", v.doublee], ["MKV multipiste", v.assemblee]] : [])];
}

function majPastilles(z, etapes) {
  if (z.childElementCount !== etapes.length) z.replaceChildren(...etapes.map(() => document.createElement("i")));
  etapes.forEach(([nom, faite], i) => { z.children[i].className = faite ? "faite" : ""; z.children[i].title = `${nom} : ${faite ? "faite" : "à faire"}`; });
}

const aValider = (v) => v.relue && !v.validee;
const idLotAffiche = () => lotCourant || lotTermine;

async function continuerEpisode(chemin) {
  const id = idLotAffiche();
  if (!id) return;
  try {
    await api("POST", `/api/lots/${id}/continuer`, { chemin });
    if (lotCourant) rafraichirLot(); else suivreLot(id);
  } catch (err) { alert(err.message); }
}

let validationVideo = "";                         // vidéo dont les noms sont affichés ; vide pour tout le lot

function ligneValidation(p) {
  const tr = document.createElement("tr");
  tr.dataset.transcrit = p.transcrit;
  const champ = (valeur, nom) => { const i = document.createElement("input"); i.type = "text"; i.value = valeur || ""; i.dataset.champ = nom; i.spellcheck = false; return i; };
  const coche = document.createElement("input"); coche.type = "checkbox"; coche.dataset.champ = "accepter";
  coche.checked = !!p.graphie && p.statut !== "incertain" && p.confiance !== "faible";
  const pourquoi = document.createElement("div"); pourquoi.className = "pourquoi";
  pourquoi.textContent = `${p.statut === "incertain" ? "Incertain. " : ""}${p.raison || ""} (${p.occurrences || 0} fois, confiance ${p.confiance || "?"})`;
  pourquoi.title = (p.exemples || []).join("\n");
  for (const contenu of [coche, p.transcrit, champ(p.graphie, "graphie"), champ(p.francais, "francais"), pourquoi]) {
    const td = document.createElement("td"); td.append(contenu); tr.appendChild(td);
  }
  return tr;
}

async function ouvrirValidation(chemin) {
  const id = idLotAffiche();
  if (!id) return;
  try {
    const r = await api("GET", `/api/lots/${id}/relecture${chemin ? "?chemin=" + encodeURIComponent(chemin) : ""}`);
    if (!r.propositions.length && chemin) return continuerEpisode(chemin);
    validationVideo = chemin || "";
    $("#validation-titre").textContent = `${r.propositions.length} nom${r.propositions.length > 1 ? "s" : ""} à vérifier${chemin ? " : " + nomFichier(chemin) : " dans le lot"}`;
    $("#validation-lignes").replaceChildren(...r.propositions.map(ligneValidation));
    $("#btn-validation-valider").textContent = chemin ? "Valider et passer à la suite" : "Valider les noms";
    $("#validation-message").textContent = "";
    $("#validation").hidden = false;
    $("#validation").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (err) { alert(err.message); }
}

for (const [bouton_, valeur] of [["#btn-validation-tout", true], ["#btn-validation-rien", false]])
  $(bouton_).addEventListener("click", () => { for (const c of $("#validation-lignes").querySelectorAll("[data-champ=accepter]")) c.checked = valeur; });
$("#btn-validation-fermer").addEventListener("click", () => { $("#validation").hidden = true; });
$("#btn-validation-valider").addEventListener("click", async () => {
  const id = idLotAffiche(), bouton_ = $("#btn-validation-valider");
  const champ = (tr, nom) => tr.querySelector(`[data-champ=${nom}]`);
  const decisions = [...$("#validation-lignes").children].map((tr) => ({ transcrit: tr.dataset.transcrit, graphie: champ(tr, "graphie").value, francais: champ(tr, "francais").value, accepter: champ(tr, "accepter").checked }));
  bouton_.disabled = true;
  try {
    await api("POST", `/api/lots/${id}/valider`, { decisions });
    $("#validation").hidden = true;
    if (validationVideo) await continuerEpisode(validationVideo);
    else if (lotCourant) rafraichirLot();
  } catch (err) { $("#validation-message").textContent = "Échec : " + err.message; }
  finally { bouton_.disabled = false; }
});
$("#btn-lot-verifier").addEventListener("click", () => ouvrirValidation(""));
async function changerObjectifLot() {
  const id = idLotAffiche();
  try {
    await api("POST", `/api/lots/${id}/objectif`, { arret: $("#lot-objectif").value, remplacer: $("#lot-remplacer").checked });
    $("#lot-objectif").blur();
    if (lotCourant) rafraichirLot(); else suivreLot(id);
  } catch (err) { alert(err.message); }
}
$("#lot-objectif").addEventListener("change", changerObjectifLot);
$("#lot-remplacer").addEventListener("change", changerObjectifLot);

// --- Suivi d'un lot -------------------------------------------------------------------------------------------

let lotCourant = null, minuteurLot = null, lotPausePour = null, derniereEstimationLot = null;
let lotSuspenduPour = null, lotSuspendu = null;    // lot en pause dont le panneau de réglages est construit
let lotVideoVue = null;                            // vidéo en cours déjà centrée dans la liste du lot

function suivreLot(id) {
  lotCourant = id; lotPausePour = null; lotSuspenduPour = null; lotVideoVue = null;
  lotLignes = new Map(); $("#lot-videos").innerHTML = "";
  $("#dernier-lot").hidden = true;
  $("#lot").hidden = false; $("#lot-pause").hidden = true; $("#lot-suspendu").hidden = true;
  clearInterval(minuteurLot);
  minuteurLot = setInterval(rafraichirLot, 2000);
  return rafraichirLot();
}

/* Temps restant calculé par le service (reste_s) : il survit au rechargement de la page. */
function texteReste(s) {
  if (s === null || s === undefined) return "";
  return s > 0 ? "reste environ " + formaterDuree(s) : "fin imminente";
}

let lotLignes = new Map();                        // rang de la vidéo → sa ligne (son chemin change si le MKV la remplace)

function majListeLot(ul, l) {
  for (const [rang, v] of l.videos.entries()) {
    let r = lotLignes.get(rang);
    if (!r) {
      r = { li: document.createElement("li"), e: document.createElement("span"), n: document.createElement("span"), d: document.createElement("span"), tache: null, etat: null, accordeon: null, duree: "" };
      r.n.className = "src"; r.n.textContent = v.nom; r.d.className = "date";
      r.reessayer = document.createElement("button");
      Object.assign(r.reessayer, { type: "button", className: "secondaire petit", textContent: "Réessayer", title: "Remettre cette vidéo dans la liste à traiter", hidden: true });
      r.reessayer.addEventListener("click", () => reessayerLot(v.chemin));
      r.pastilles = document.createElement("span"); r.pastilles.className = "pastilles";
      r.continuer = document.createElement("button");
      Object.assign(r.continuer, { type: "button", className: "secondaire petit", hidden: true });
      r.continuer.addEventListener("click", () => { if (aValider(r.v)) ouvrirValidation(r.v.chemin); else continuerEpisode(r.v.chemin); });
      r.li.append(r.e, r.n, r.pastilles, r.d, r.continuer, r.reessayer);
      ul.appendChild(r.li);
      lotLignes.set(rang, r);
    }
    r.v = v;
    const bloquee = v.etat === "prepare" && aValider(v);
    r.e.className = "etat " + (bloquee ? "a_valider" : v.etat);
    r.e.textContent = bloquee ? "à valider" : { a_faire: "à faire", en_cours: "en cours", prepare: "transcrite", traduit: "traduite", fait: "ok", erreur: "erreur", annule: "annulée" }[v.etat] || v.etat;
    majPastilles(r.pastilles, etapesDeLaVideo(v, l));
    const complete = v.traduite && (!veutDoublage(l) || (l.options.arret === "doublage" ? v.doublee : v.assemblee));
    r.continuer.hidden = complete || ["en_cours", "erreur", "annule"].includes(v.etat) || l.etat === "annule";
    r.continuer.textContent = aValider(v) ? "Vérifier les noms" : "Continuer";
    r.continuer.title = aValider(v) ? "Des noms attendent votre décision avant la suite de cet épisode"
      : `Continuer cet épisode jusqu'${veutDoublage(l) ? "au doublage" : "aux sous-titres traduits"}, avant les autres`;
    r.n.textContent = v.nom; r.n.title = v.erreur || v.chemin;
    r.reessayer.hidden = v.etat !== "erreur" || l.etat === "annule";
    if (v.tache !== r.tache) {
      r.accordeon?.retirer();
      r.tache = v.tache; r.accordeon = null;
      if (v.tache) { r.accordeon = journalAccordeon({ id: v.tache }, r.n); r.li.appendChild(r.accordeon.bouton); r.accordeon.installer(r.li); }
    }
    if (v.etat !== r.etat) {
      r.etat = v.etat; r.duree = "";
      r.accordeon?.recharger();
      if (v.tache && v.etat !== "en_cours") dureePasse(v.tache, v.etat).then((x) => { r.duree = x; });
    }
    r.d.textContent = (v.langue_detectee ? nomLangue(v.langue_detectee) : v.langue ? nomLangue(v.langue) : "détection")
      + (v.nb ? ` · ${v.nb} st` : "") + (r.duree ? ` · ${r.duree}` : "") + (v.erreur ? " · " + v.erreur : "");
  }
}

/* Plusieurs vidéos à la fois : une ligne par vidéo (lignes traduites, temps écoulé, temps restant) et un journal commun. */
const MAX_LIGNES_JOURNAL_COMMUN = 500;
let modeParallele = false, lignesCommunes = [];
const suiviParallele = new Map();              // tâche → { nom, lu, faits, total, repris }

function codeVideo(nom) { return /S\d+E\d+/i.exec(nom)?.[0] || nom.replace(/\.[^.]+$/, "").slice(0, 24); }

function entrerParallele() {
  modeParallele = true; lignesCommunes = []; suiviParallele.clear();
  clearInterval(minuteur); minuteur = null; tacheCourante = null;
  montrerRelance(null); $("#tache").hidden = false; $("#etapes").hidden = true; $("#btn-annuler").hidden = true; $("#pause").hidden = true; $("#resultat").hidden = true;
  $("#journal-texte").textContent = ""; $("#journal-minuteur").textContent = "";
  $("#tache-titre").title = "";
  $("#tache-etat").textContent = "en cours"; $("#tache-etat").className = "etat en_cours";
}

function quitterParallele() {
  modeParallele = false; suiviParallele.clear(); processTache = null;
  afficherProcess();
  $("#lot-actives").hidden = true;            // le journal commun reste lisible jusqu'à la prochaine tâche suivie
  $("#tache-titre").textContent = "Dernières traductions";
  $("#tache-etat").textContent = "terminées"; $("#tache-etat").className = "etat terminee";
}

async function lireSuiviParallele(id, s) {
  let t;
  try { t = await api("GET", `/api/taches/${id}?depuis=${s.lu}`); } catch (err) { return null; }
  let lignes = t.journal;
  if (!s.repris) {                             // à la première lecture, seulement la traduction, pas la préparation
    const i = lignes.findLastIndex((x) => x.includes("Reprise :"));
    if (i >= 0) lignes = lignes.slice(i);
    s.repris = true;
  }
  s.lu = t.journal_total;
  for (const x of lignes) {
    const m = /(\d+)\/(\d+) sous-titres traduits/.exec(x);
    if (m) { s.faits = +m[1]; s.total = +m[2]; }
  }
  const code = codeVideo(s.nom);
  return { t, lignes: lignes.map((x) => x.replace(/^(\[\d\d:\d\d:\d\d\])/, `$1 ${code}`)) };
}

function ligneActive(a) {
  const d = document.createElement("div"); d.className = "ligne";
  const nom = document.createElement("span"); nom.className = "nom"; nom.textContent = a.nom; nom.title = a.nom;
  const mini = document.createElement("div"); mini.className = "mini";
  const plein = document.createElement("span"); plein.style.width = (a.total ? 100 * a.faits / a.total : 0).toFixed(0) + "%"; mini.appendChild(plein);
  const compte = document.createElement("span"); compte.className = "temps compte"; compte.textContent = a.total ? `${a.faits}/${a.total}` : "…";
  const ecoule = document.createElement("span"); ecoule.className = "temps"; ecoule.textContent = `⏱ ${formaterDuree(a.ecoule)}`;
  const reste = document.createElement("span"); reste.className = "temps"; reste.textContent = a.reste === null ? "" : `reste ${formaterDuree(a.reste)}`;
  d.append(nom, mini, compte, ecoule, reste);
  return d;
}

async function majParallele(l) {
  if (!modeParallele) entrerParallele();
  const actives = l.videos.filter((v) => v.etat === "en_cours" && v.tache);
  for (const v of actives) if (!suiviParallele.has(v.tache)) suiviParallele.set(v.tache, { nom: v.nom, lu: 0, faits: 0, total: 0, repris: false });
  const ids = new Set(actives.map((v) => v.tache));
  const lus = await Promise.all([...suiviParallele].map(async ([id, s]) => ({ id, s, r: await lireSuiviParallele(id, s) })));
  if (!modeParallele) return;
  for (const { id } of lus) if (!ids.has(id)) suiviParallele.delete(id);   // terminée : ses dernières lignes viennent d'être lues

  const enCours = lus.filter(({ id, r }) => ids.has(id) && r?.t.etat === "en_cours").map(({ s, r }) => ({
    nom: s.nom, faits: s.faits, total: s.total, reste: r.t.reste_s ?? null,
    ecoule: r.t.passe_debut ? Math.max(0, Math.round(Date.now() / 1000 - r.t.passe_debut)) : 0 }));
  $("#lot-actives").hidden = !enCours.length;
  $("#lot-actives").replaceChildren(...enCours.map(ligneActive));
  $("#tache-titre").textContent = `${enCours.length} vidéo${enCours.length > 1 ? "s" : ""} en traduction`;
  if (enCours.length) processLot += " · " + enCours.map((a) => `${codeVideo(a.nom)} ⏱ ${formaterDuree(a.ecoule)}${a.reste === null ? "" : ", reste " + formaterDuree(a.reste)}`).join(" | ");
  afficherProcess();

  const nouvelles = lus.flatMap(({ r }) => r?.lignes || []).sort((a, b) => a.slice(0, 10).localeCompare(b.slice(0, 10)));
  if (nouvelles.length) {
    const j = $("#journal"), enBas = j.scrollTop + j.clientHeight >= j.scrollHeight - 20;
    lignesCommunes = lignesCommunes.concat(nouvelles).slice(-MAX_LIGNES_JOURNAL_COMMUN);
    $("#journal-texte").textContent = lignesCommunes.join("\n");
    if (enBas) j.scrollTop = j.scrollHeight;
  }
}

const ETAPES_LOT = { source: "préparation", transcription: "transcription", decoupe: "découpe", relecture: "relecture IA", traduction: "traduction",
  fin: "écriture des fichiers", doublage: "doublage", assemblage: "assemblage" };

async function rafraichirLot() {
  if (!lotCourant) return;
  let l;
  try { l = await api("GET", `/api/lots/${lotCourant}`); } catch (err) { return; }
  $("#lot-racine").textContent = l.racine;
  const etat = $("#lot-etat");
  // L'étape réelle de la vidéo en cours : sans pause, la phase 1 va jusqu'à la traduction.
  etat.textContent = { en_cours: (ETAPES_LOT[l.etape] || { 1: "transcription", 2: "traduction", 3: "doublage" }[l.phase]) + " en cours", suspendu: "en pause", attente_traduction: "en attente de traduction", termine: "terminé", annule: "annulé" }[l.etat] || l.etat;
  etat.className = "etat " + l.etat;
  $("#btn-lot-suspendre").hidden = l.etat !== "en_cours";
  $("#btn-lot-reprendre").hidden = l.etat !== "suspendu";
  $("#btn-lot-annuler").hidden = ["termine", "annule"].includes(l.etat);
  $("#btn-lot-reessayer").hidden = !l.comptes.erreur || l.etat === "annule";
  $("#btn-lot-reessayer").textContent = l.comptes.erreur > 1 ? `Réessayer les ${l.comptes.erreur} en erreur` : "Réessayer celle en erreur";
  const faits = l.comptes.fait + (l.phase === 1 ? l.comptes.prepare : 0) + (l.phase < 3 ? l.comptes.traduit : 0) + l.comptes.erreur + l.comptes.annule;
  const barre = $("#lot-progression");
  barre.querySelector(".barre").style.width = (100 * faits / Math.max(1, l.total)).toFixed(0) + "%";
  barre.querySelector(".texte").textContent = `${faits}/${l.total} vidéo${l.total > 1 ? "s" : ""} (${l.comptes.fait} terminée${l.comptes.fait > 1 ? "s" : ""}, ${l.comptes.prepare} transcrite${l.comptes.prepare > 1 ? "s" : ""}, ${l.comptes.erreur} en erreur)`;
  const reste = texteReste(l.reste_s);
  const parVideo = l.etat === "en_cours" && l.duree_moyenne_s ? `≈ ${formaterDuree(l.duree_moyenne_s)} par vidéo` : "";
  barre.querySelector(".texte").textContent += (reste ? ` · ${reste}` : "") + (parVideo ? ` · ${parVideo}` : "");
  // En pause, les vidéos déjà lancées vont jusqu'au bout : leur suivi reste affiché tant qu'il en reste une.
  lotParallele = l.simultanees > 1 && (l.etat === "en_cours" || (l.etat === "suspendu" && l.comptes.en_cours > 0));
  const nbActives = `${l.comptes.en_cours} vidéo${l.comptes.en_cours > 1 ? "s" : ""}`;
  const detailLot = !lotParallele ? "" : l.etat === "suspendu" ? ` · ${nbActives} en train de finir` : ` · ${nbActives} en traduction${parVideo ? " · " + parVideo : ""}`;
  processLot = { en_cours: `Lot ${faits}/${l.total}${reste ? " (" + reste + ")" : ""}${detailLot}`, suspendu: `Lot en pause ${faits}/${l.total}${detailLot}`, attente_traduction: `Lot prêt à traduire ${faits}/${l.total}` }[l.etat] || null;
  if (!lotParallele) afficherProcess();          // sinon majParallele complète d'abord le texte, sans clignotement
  // Le réglage de ce lot, pas la case du formulaire (elle ne vaut que pour un nouveau travail).
  const infoPause = $("#lot-info-pause"), traducteurLot = moteur("traducteurs", l.options?.traducteur || config.traducteur_actif);
  infoPause.hidden = l.phase !== 1 || !["en_cours", "suspendu"].includes(l.etat) || traducteurLot?.type === "aucun";
  infoPause.textContent = l.options?.pause === false ? "Ce lot traduit chaque vidéo à la suite, sans s'arrêter."
    : `Ce lot s'arrêtera avant de traduire pour ${l.options?.relecture ? "vérifier les noms et " : ""}afficher le coût.`;
  $("#lot-fichier").textContent = "Fichier de lot (pour reprendre après une coupure) : " + l.fichier;
  const ul = $("#lot-videos");
  majListeLot(ul, l);
  const courante = l.videos.find((v) => v.etat === "en_cours");
  if ((courante?.chemin ?? null) !== lotVideoVue) {  // nouvelle vidéo en cours : la centrer dans la liste
    lotVideoVue = courante?.chemin ?? null;
    if (courante) { const li = ul.children[l.videos.indexOf(courante)]; ul.scrollTop = li.offsetTop - (ul.clientHeight - li.offsetHeight) / 2; }
  }
  if (lotParallele) await majParallele(l);
  else {
    if (modeParallele) quitterParallele();
    if (courante?.tache && tacheCourante !== courante.tache) suivre(courante.tache);
  }
  const double = veutDoublage(l);
  $("#lot-zone-objectif").hidden = !doublageActif() || l.etat === "annule";
  if (document.activeElement !== $("#lot-objectif")) $("#lot-objectif").value = double ? "assemblage" : "traduction";
  $("#lot-zone-remplacer").hidden = !double;
  $("#lot-remplacer").checked = !!l.options?.remplacer;
  $("#lot-note-objectif").textContent = double && l.doublage_s ? `≈ ${formaterDuree(l.doublage_s)} de carte graphique pour les vidéos encore à doubler` : "";
  $("#btn-lot-traduire").textContent = double ? "Traduire et doubler tout" : "Traduire tout";
  const nbAValider = l.videos.filter(aValider).length;
  $("#lot-a-verifier").hidden = !nbAValider;
  $("#lot-a-verifier-texte").textContent = `${nbAValider} vidéo${nbAValider > 1 ? "s ont" : " a"} des noms à vérifier.`;
  if (l.etat === "attente_traduction") { if (lotPausePour !== l.id) afficherPauseLot(l); }
  else { $("#lot-pause").hidden = true; lotPausePour = null; }
  if (l.etat === "suspendu") { if (lotSuspenduPour !== l.id) afficherSuspendu(l); }
  else { $("#lot-suspendu").hidden = true; lotSuspenduPour = null; }
  if (["termine", "annule"].includes(l.etat)) { clearInterval(minuteurLot); minuteurLot = null; lotTermine = lotCourant; lotCourant = null; majHistorique(); }
}

async function afficherPauseLot(l) {
  lotPausePour = l.id;
  $("#lot-pause").hidden = false; $("#lot-message").textContent = "";
  $("#lot-pause-resume").textContent = `${l.comptes.prepare} vidéo${l.comptes.prepare > 1 ? "s" : ""} prête${l.comptes.prepare > 1 ? "s" : ""} à traduire.`;
  remplirSelect($("#lot-traducteur"), config.traducteurs, l.options?.traducteur || config.traducteur_actif);
  remplirLangues($("#lot-langues"), l.options?.langues || config.langues_cibles || ["fr"]);
  const maj = async () => {
    try { derniereEstimationLot = await api("GET", `/api/lots/${l.id}/estimation?langues=${encodeURIComponent(lireLangues($("#lot-langues")).join(","))}`); afficherCoutsDans($("#lot-couts"), derniereEstimationLot, $("#lot-traducteur").value); }
    catch (err) { $("#lot-message").textContent = err.message; }
  };
  $("#lot-langues").oninput = maj;
  $("#lot-traducteur").onchange = () => { afficherReglagesClaudeLot(); afficherCoutsDans($("#lot-couts"), derniereEstimationLot, $("#lot-traducteur").value); };
  const changerClaude = () => { sauvegardeClaudeLot = enregistrerReglagesClaudeLot(maj); };
  $("#lot-modele").onchange = changerClaude;
  $("#lot-effort").onchange = changerClaude;
  afficherReglagesClaudeLot();
  await maj();
}

/* Modèle et effort du traducteur Claude choisi : modifiables ici, car la traduction du lot relit la configuration. */
let sauvegardeClaudeLot = Promise.resolve();

function afficherReglagesClaudeLot() {
  const m = moteur("traducteurs", $("#lot-traducteur").value), claude = m?.type === "claude";
  $("#lot-zone-modele").hidden = !claude; $("#lot-zone-effort").hidden = !claude;
  $("#lot-note-effort").hidden = !claude || (m.effort || "low") === "low";
  if (!claude) return;
  const proposes = [...$("#modeles-claude").options].map((o) => o.value), actuel = m.modele || "claude-sonnet-5-5";
  const sel = $("#lot-modele");
  sel.innerHTML = "";
  for (const v of proposes.includes(actuel) ? proposes : [actuel, ...proposes]) {
    const o = document.createElement("option"); o.value = v; o.textContent = v; sel.appendChild(o);
  }
  sel.value = actuel;
  $("#lot-effort").value = m.effort || "low";
}

async function enregistrerReglagesClaudeLot(apres) {
  const m = moteur("traducteurs", $("#lot-traducteur").value);
  if (m?.type !== "claude") return;
  m.modele = $("#lot-modele").value; m.effort = $("#lot-effort").value;
  try {
    config = await api("PUT", "/api/config", config);
    afficherReglagesTraducteur();           // sinon le formulaire de la page réécrirait l'ancienne valeur au prochain lancement
    afficherReglagesClaudeLot();
    await apres();
  } catch (err) { $("#lot-message").textContent = err.message; }
}

async function actionLot(action, corps) {
  if (!lotCourant) return;
  try { await api("POST", `/api/lots/${lotCourant}/${action}`, corps || {}); rafraichirLot(); }
  catch (err) { alert(err.message); }
}
/* Remet une vidéo en erreur (ou toutes) dans la liste à traiter ; aussi après la fin du lot, qui repart pour elles. */
let lotTermine = null;
async function reessayerLot(chemin) {
  const id = lotCourant || lotTermine;
  if (!id) return;
  try {
    await api("POST", `/api/lots/${id}/reessayer`, chemin ? { chemin } : {});
    if (lotCourant) rafraichirLot(); else suivreLot(id);
  } catch (err) { alert(err.message); }
}
$("#btn-lot-reessayer").addEventListener("click", () => reessayerLot());
$("#btn-lot-suspendre").addEventListener("click", () => actionLot("suspendre"));
/* Lot en pause : ses réglages pour la suite, modifiables ici ou par les listes du haut de la page. */
function afficherSuspendu(l) {
  lotSuspenduPour = l.id; lotSuspendu = l;
  remplirSelect($("#lot-susp-transcription"), config.transcriptions, l.options?.transcription || config.transcription_active);
  remplirSelect($("#lot-susp-traducteur"), config.traducteurs, l.options?.traducteur || config.traducteur_actif);
  remplirLangues($("#lot-susp-langues"), l.options?.langues || config.langues_cibles || ["fr"]);
  $("#lot-suspendu").hidden = false;
}
for (const [haut, panneau] of [["#opt-transcription", "#lot-susp-transcription"], ["#opt-traducteur", "#lot-susp-traducteur"]])
  $(haut).addEventListener("change", () => { if (!$("#lot-suspendu").hidden) $(panneau).value = $(haut).value; });

$("#btn-lot-reprendre").addEventListener("click", async () => {
  const traducteur = $("#lot-susp-traducteur").value, m = moteur("traducteurs", traducteur);
  const corps = { traducteur, transcription: $("#lot-susp-transcription").value, langues: lireLangues($("#lot-susp-langues")) };
  const change = traducteur !== (lotSuspendu?.options?.traducteur || config.traducteur_actif);
  if (change) corps.pause = pauseParDefaut(m);       // passer à une API payante redonne l'estimation avant de dépenser
  if (change && lotSuspendu?.phase === 2 && m?.type === "claude"
      && !confirm(`Traduire la suite du lot avec ${m.nom} (${m.modele || "modèle par défaut"}) ? Le coût est facturé sur la clé API.`)) return;
  await enregistrerReglagesTraducteur().catch(() => {});
  actionLot("reprendre", corps);
});
$("#btn-lot-annuler").addEventListener("click", () => { if (confirm("Annuler le lot ? Les vidéos déjà faites gardent leurs sous-titres.")) actionLot("annuler"); });
$("#btn-lot-traduire").addEventListener("click", async () => {
  await sauvegardeClaudeLot;
  const traducteur = $("#lot-traducteur").value;
  actionLot("traduire", { traducteur, langues: lireLangues($("#lot-langues")) }).then(() => { $("#lot-pause").hidden = true; });
});
$("#btn-lot-sans-traduire").addEventListener("click", () => actionLot("traduire", { traducteur: "aucun" }).then(() => { $("#lot-pause").hidden = true; }));

// --- Suivi d'une tâche ----------------------------------------------------------------------------------------

let pausePour = null;          // id de la tâche dont le panneau de pause est construit

// État du traitement en cours, affiché en permanence dans l'en-tête.
const ETAPES_PROCESS = { source: "Source", transcription: "Transcription", decoupe: "Découpe", relecture: "Relecture", traduction: "Traduction", fin: "Fichiers", doublage: "Doublage", assemblage: "MKV" };
let processLot = null, processTache = null, lotParallele = false;
function afficherProcess() {
  // Plusieurs vidéos à la fois : le détail d'une seule n'apprendrait rien, le lot résume.
  const z = $("#process"), texte = [processLot, lotParallele ? null : processTache].filter(Boolean).join(" · ");
  z.hidden = !texte;
  z.classList.toggle("multiligne", lotParallele);
  z.textContent = texte;
  z.title = texte;
}

function montrerRelance(t) {
  const possible = t && t.etat === "erreur" && !t.options?.lot;
  for (const b of [$("#btn-relancer"), $("#btn-relancer-journal")]) { b.hidden = !possible; b.dataset.tache = possible ? t.id : ""; }
}

async function relancer(id) {
  try { await suivre((await api("POST", `/api/taches/${id}/reessayer`)).id); majHistorique(); }
  catch (err) { alert("Relance impossible : " + err.message); }
}

function suivre(id) {
  tacheCourante = id; journalLu = 0; pausePour = null; montrerRelance(null);
  $("#etapes").hidden = false;                 // masquées pendant une traduction parallèle
  $("#journal-texte").textContent = ""; $("#journal-minuteur").textContent = ""; $("#resultat").hidden = true; $("#pause").hidden = true; $("#tache").hidden = false;
  for (const li of document.querySelectorAll("#etapes li")) li.className = "";
  clearInterval(minuteur);
  minuteur = setInterval(rafraichir, 1000);
  return rafraichir();
}

async function rafraichir() {
  if (!tacheCourante) return;
  let t;
  try { t = await api("GET", `/api/taches/${tacheCourante}?depuis=${journalLu}`); } catch (err) { return; }
  if (modeParallele) return;
  $("#tache-titre").textContent = nomFichier(t.source); $("#tache-titre").title = t.source;
  const etat = $("#tache-etat");
  etat.textContent = { en_attente: "en attente", en_cours: "en cours", en_pause: "en pause", terminee: "terminée", erreur: "erreur", annulee: "annulée" }[t.etat] || t.etat;
  etat.className = "etat " + t.etat;
  $("#btn-annuler").hidden = !["en_attente", "en_cours", "en_pause"].includes(t.etat);
  montrerRelance(t);
  const ordre = ["source", "transcription", "decoupe", "relecture", "traduction", "fin", "doublage", "assemblage"];
  const i = ordre.indexOf(t.etape), jusqua = t.options?.jusqua;
  const montrer = { relecture: !!t.options?.relecture, doublage: ["doublage", "assemblage"].includes(jusqua), assemblage: jusqua === "assemblage" };
  for (const [nom, visible] of Object.entries(montrer)) document.querySelector(`#etapes li[data-etape=${nom}]`).hidden = !visible;
  document.querySelectorAll("#etapes li").forEach((li, k) => {
    li.className = t.etat === "terminee" || k < i ? "faite" : (k === i && t.etat === "en_cours" ? "en-cours" : "");
  });
  if (t.etat === "en_pause" && !t.options?.lot) { if (pausePour !== t.id) afficherPause(t); }   // un lot se traduit par son propre panneau
  else $("#pause").hidden = true;
  if (t.journal.length) {
    const j = $("#journal");
    const enBas = j.scrollTop + j.clientHeight >= j.scrollHeight - 20;
    const texte = $("#journal-texte");
    texte.textContent += (texte.textContent ? "\n" : "") + t.journal.join("\n");
    if (enBas) j.scrollTop = j.scrollHeight;
  }
  journalLu = t.journal_total;
  // Temps écoulé sur cette vidéo depuis le début de la passe (transcription, ou traduction complète).
  const chrono = t.etat === "en_cours" && t.passe_debut ? `⏱ ${formaterDuree(Math.max(0, Math.round(Date.now() / 1000 - t.passe_debut)))}` : "";
  $("#journal-minuteur").textContent = chrono ? "  " + chrono : "";
  const nomSource = nomFichier(t.source);
  const resteTache = texteReste(t.reste_s);
  processTache = t.etat === "en_cours" ? `${ETAPES_PROCESS[t.etape] || "Préparation"} : ${nomSource} ${chrono}${resteTache ? " · " + resteTache : ""}`.trim()
    : t.etat === "en_attente" ? `En attente : ${nomSource}` : t.etat === "en_pause" && !t.options?.lot ? `En pause avant traduction : ${nomSource}` : null;
  afficherProcess();
  if (t.etat === "terminee" && t.resultat) afficherResultat(t.resultat);
  if (!["en_attente", "en_cours", "en_pause"].includes(t.etat)) {
    clearInterval(minuteur); minuteur = null;
    const fini = tacheCourante; tacheCourante = null;
    majHistorique(); majEtat();
    if (fini && t.etat === "terminee") document.title = "Génération de sous-titres : terminé";
  }
}

// --- Pause avant la traduction : estimation et choix du traducteur ----------------------------------------------

function afficherPause(t) {
  pausePour = t.id;
  const z = $("#pause");
  z.hidden = false;
  $("#btn-traduire").textContent = t.options?.jusqua === "assemblage" ? "Traduire et doubler" : "Traduire";
  $("#pause-resume").textContent = t.prepare ? `${t.prepare.nb} sous-titres en ${nomLangue(t.prepare.langue)} prêts (${t.prepare.srt_origine}).` : "";
  $("#pause-message").textContent = "";
  remplirSelect($("#pause-traducteur"), config.traducteurs, t.options?.traducteur || config.traducteur_actif);
  remplirLangues($("#pause-langues"), t.options?.langues || config.langues_cibles || ["fr"]);
  afficherCouts(t.estimation);
  const rafraichirEstimation = async () => {
    try { afficherCouts(await api("GET", `/api/taches/${t.id}/estimation?langues=${encodeURIComponent(lireLangues($("#pause-langues")).join(","))}`)); }
    catch (err) { $("#pause-message").textContent = err.message; }
  };
  $("#pause-langues").oninput = rafraichirEstimation;
  $("#pause-traducteur").onchange = () => afficherCouts(derniereEstimation);
}

let derniereEstimation = null;
function afficherCouts(e) { derniereEstimation = e; afficherCoutsDans($("#pause-couts"), e, $("#pause-traducteur").value); }

function afficherCoutsDans(z, e, choisi) {
  z.innerHTML = "";
  if (!e || !e.langues) return;
  if (!e.langues.length) { z.textContent = "Rien à traduire : la vidéo est déjà dans la ou les langues choisies."; return; }
  const tete = document.createElement("span");
  tete.textContent = `${e.videos ? e.videos + " vidéos, " : ""}${e.nb} sous-titres → ${e.langues.map(nomLangue).join(", ")} : ${e.requetes} requêtes, ≈ ${e.jetons_entree.toLocaleString("fr-FR")} jetons d'entrée et ${e.jetons_sortie.toLocaleString("fr-FR")} de sortie.`;
  z.appendChild(tete);
  const retenu = e.traducteurs.find((tr) => tr.id === choisi);
  if (retenu && retenu.type !== "aucun") {
    const grand = document.createElement("div"); grand.className = "cout-principal";
    grand.textContent = retenu.cout_usd === null || retenu.cout_usd === undefined ? "Prix inconnu"
      : retenu.cout_usd === 0 ? `Gratuit${retenu.duree_s ? ", environ " + formaterDuree(retenu.duree_s) : ""}`
      : `≈ ${retenu.cout_usd.toFixed(2).replace(".", ",")} $`;
    z.prepend(grand);
  }
  for (const tr of e.traducteurs) {
    if (tr.type === "aucun") continue;
    const s = document.createElement("span");
    s.className = tr.id === choisi ? "choisi" : "";
    const cout = tr.cout_usd === null || tr.cout_usd === undefined ? "prix inconnu" : tr.cout_usd === 0 ? "gratuit" : `≈ ${tr.cout_usd.toFixed(2)} $`;
    s.textContent = `${tr.nom}${tr.modele ? " (" + tr.modele + ")" : ""} : ${cout}${tr.duree_s ? ", ≈ " + formaterDuree(tr.duree_s) : ""}`;
    z.appendChild(s);
  }
}

async function reprendre(traducteur) {
  if (!tacheCourante) return;
  const msg = $("#pause-message");
  $("#btn-traduire").disabled = $("#btn-sans-traduire").disabled = true;
  try {
    await api("POST", `/api/taches/${tacheCourante}/traduire`, { traducteur, langues: lireLangues($("#pause-langues")) });
    $("#pause").hidden = true; pausePour = null;
  } catch (err) { msg.textContent = err.message; }
  finally { $("#btn-traduire").disabled = $("#btn-sans-traduire").disabled = false; }
}
$("#btn-traduire").addEventListener("click", () => reprendre($("#pause-traducteur").value));
$("#btn-sans-traduire").addEventListener("click", () => reprendre("aucun"));

function afficherResultat(r) {
  const z = $("#resultat");
  z.hidden = false;
  z.innerHTML = "";
  const fichiers = r.fichiers || [{ langue: r.langue, chemin: r.srt, nb: r.nb, traduit: r.traduit }];
  const titre = document.createElement("strong");
  const langues = fichiers.map((f) => nomLangue(f.langue)).join(", ");
  // En dessous de 5 s, tout a été repris du travail déjà fait (même seuil que temps.py). La durée s'arrête aux sous-titres : pas de sens après un doublage.
  titre.textContent = r.mkv ? `Sous-titres et doublage créés : ${langues}` : r.duree_s < 5 ? `Sous-titres déjà prêts, rien à refaire : ${langues}`
    : `Sous-titres créés : ${langues} (${formaterDuree(r.duree_s)} depuis le début du traitement)`;
  z.appendChild(titre);
  z.appendChild(bouton("Ouvrir le dossier", () => api("POST", "/api/ouvrir", { chemin: r.srt })));
  z.appendChild(bouton(r.mkv ? "Ouvrir le MKV" : "Ouvrir la vidéo", () => api("POST", "/api/ouvrir", { chemin: r.mkv || r.video })));
  if (r.mkv) {
    const ch = document.createElement("div");
    ch.className = "chemin"; ch.textContent = `${r.remplace ? "Vidéo remplacée (original à la corbeille)" : "MKV multipiste"} → ${r.mkv}`;
    z.appendChild(ch);
  }
  for (const f of fichiers) {
    const ch = document.createElement("div");
    ch.className = "chemin"; ch.textContent = `${nomLangue(f.langue)}${f.traduit ? "" : " (langue parlée)"} : ${f.nb} sous-titres → ${f.chemin}`;
    z.appendChild(ch);
  }
}

function bouton(texte, action) {
  const b = document.createElement("button");
  b.type = "button"; b.className = "secondaire petit"; b.textContent = texte;
  b.addEventListener("click", () => action().catch((e) => alert(e.message)));
  return b;
}

for (const b of [$("#btn-relancer"), $("#btn-relancer-journal")]) b.addEventListener("click", () => relancer(b.dataset.tache));
$("#btn-annuler").addEventListener("click", () => { if (tacheCourante) api("POST", `/api/taches/${tacheCourante}/annuler`); });

let historiqueComplet = false;
const histOuverts = new Set();      // journaux ouverts : l'historique est reconstruit à la fin de chaque tâche

/* Le journal d'une tâche passée s'ouvre sous sa ligne ; la carte de la tâche en cours n'est pas touchée. */
function journalAccordeon(t, titre) {
  const texte = document.createElement("pre"); texte.className = "journal"; texte.hidden = true;
  let zone = null;                      // créée à la première ouverture (un lot peut compter des centaines de lignes)
  const charger = async () => {
    try { texte.textContent = (await api("GET", `/api/taches/${t.id}`)).journal.join("\n") || "(journal vide)"; }
    catch (err) { texte.textContent = "Journal illisible : " + err.message; }
  };
  const basculer = async () => {
    const ouvrir = !zone || zone.hidden;
    if (ouvrir && !zone) { texte.hidden = false; surveillerDefilement(texte); zone = texte.parentElement; }
    else zone.hidden = !ouvrir;
    b.textContent = ouvrir ? "Journal ▾" : "Journal ▸";
    b.setAttribute("aria-expanded", String(ouvrir));
    if (!ouvrir) { histOuverts.delete(t.id); return; }
    histOuverts.add(t.id);
    texte.textContent = "Chargement…";
    await charger();
  };
  const b = bouton("Journal ▸", async () => basculer());
  b.setAttribute("aria-expanded", "false");
  titre.classList.add("cliquable");
  titre.onclick = basculer;
  return {
    bouton: b,
    installer(li) { li.appendChild(texte); return histOuverts.has(t.id) ? basculer() : null; },
    recharger() { if (zone && !zone.hidden) charger(); },
    retirer() { (zone || texte).remove(); b.remove(); },
  };
}

/* Durée d'une passe, lue dans son journal : de la première à la dernière ligne, sans le temps passé en pause. */
function dureeJournal(lignes) {
  let total = 0, precedente = null, enPause = false;
  for (const ligne of lignes) {
    const m = /^\[(\d\d):(\d\d):(\d\d)\]/.exec(ligne);
    if (!m) continue;
    const s = 3600 * +m[1] + 60 * +m[2] + +m[3];
    if (precedente !== null && !enPause) total += (s - precedente + 86400) % 86400;
    precedente = s;
    if (/\] En pause :/.test(ligne)) enPause = true;
    else if (/\] Reprise :/.test(ligne)) enPause = false;
  }
  return total;
}

const cacheDurees = new Map();
async function dureePasse(id, etat) {
  const cle = `${id}:${etat}`;
  if (!cacheDurees.has(cle)) {
    try { cacheDurees.set(cle, formaterDuree(dureeJournal((await api("GET", `/api/taches/${id}`)).journal))); }
    catch (err) { return ""; }
  }
  return cacheDurees.get(cle);
}
$("#btn-historique").addEventListener("click", () => { historiqueComplet = !historiqueComplet; majHistorique(); });

async function majHistorique() {
  const liste = await api("GET", "/api/taches").catch(() => []);
  const ul = $("#historique"), defilement = ul.scrollTop, chargements = [];
  ul.innerHTML = "";
  const bouton_h = $("#btn-historique");
  bouton_h.hidden = liste.length <= 5;
  bouton_h.textContent = historiqueComplet ? "Voir moins" : `Voir plus (${liste.length})`;
  for (const t of historiqueComplet ? liste : liste.slice(0, 5)) {
    const li = document.createElement("li");
    const etat = document.createElement("span"); etat.className = "etat " + t.etat;
    etat.textContent = { en_attente: "attente", en_cours: "en cours", en_pause: "pause", terminee: "ok", erreur: "erreur", annulee: "annulée" }[t.etat] || t.etat;
    const src = document.createElement("span"); src.className = "src"; src.textContent = nomFichier(t.source); src.title = t.erreur || t.source;
    const date = document.createElement("span"); date.className = "date"; date.textContent = formaterDate(t.fin || t.debut || t.creee);
    if (!["en_cours", "en_attente"].includes(t.etat)) dureePasse(t.id, t.etat).then((x) => { if (x) { date.textContent += " · " + x; date.title = "durée de cette passe, hors pause"; } });
    li.append(etat, src, date);
    const accordeon = journalAccordeon(t, src);
    li.appendChild(accordeon.bouton);
    if (t.resultat?.srt) li.appendChild(bouton("Dossier", () => api("POST", "/api/ouvrir", { chemin: t.resultat.srt })));
    if (t.etat === "erreur" && !t.options?.lot) li.appendChild(bouton("Relancer", () => relancer(t.id)));
    ul.appendChild(li);
    chargements.push(accordeon.installer(li));
  }
  await Promise.all(chargements);      // sans ça, le contenu est encore trop court pour reprendre la position
  ul.scrollTop = defilement;
}

// --- Réglages -------------------------------------------------------------------------------------------------

const dlg = $("#dlg-reglages");
$("#btn-reglages").addEventListener("click", () => { remplirReglages(); dlg.showModal(); });
$("#btn-fermer-reglages").addEventListener("click", () => dlg.close());
$("#btn-annuler-reglages").addEventListener("click", () => dlg.close());

function remplirReglages() {
  const c = config, s = c.sous_titres || {};
  $("#cfg-sortie").value = c.sortie || ""; $("#cfg-langue").value = c.langue || "";
  $("#cfg-youtube").value = JSON.stringify((c.youtube || {}).commande || ["yt-dlp"]);
  $("#cfg-lot").value = (c.traduction || {}).lot || 25;
  $("#cfg-inactivite").value = (c.web || {}).inactivite_min ?? 60;
  $("#cfg-cpl").value = s.caracteres_par_ligne ?? 42; $("#cfg-lignes").value = s.lignes ?? 2; $("#cfg-dmax").value = s.duree_max ?? 6;
  $("#cfg-dmin").value = s.duree_min ?? 1; $("#cfg-pause").value = s.pause_coupe ?? 0.6; $("#cfg-cps").value = s.lecture_cps ?? 17;
  const n = { audio: true, sous_titres: true, transcription: false, journaux: false, journaux_age_h: 48, ...(c.nettoyage || {}) };
  $("#cfg-net-audio").checked = !!n.audio; $("#cfg-net-sous-titres").checked = !!n.sous_titres;
  $("#cfg-net-transcription").checked = !!n.transcription; $("#cfg-net-journaux").checked = !!n.journaux; $("#cfg-net-age").value = n.journaux_age_h;
  afficherBilanNettoyage();
  const lt = $("#liste-transcriptions"); lt.innerHTML = "";
  for (const m of c.transcriptions || []) lt.appendChild(ligneMoteur("transcription", m, m.id === c.transcription_active));
  const ld = $("#liste-traducteurs"); ld.innerHTML = "";
  for (const m of c.traducteurs || []) ld.appendChild(ligneMoteur("traducteur", m, m.id === c.traducteur_actif));
  $("#reglages-message").textContent = "";
}

function ligneMoteur(genre, m, actif) {
  const el = document.getElementById(genre === "transcription" ? "tpl-transcription" : "tpl-traducteur").content.firstElementChild.cloneNode(true);
  for (const champ of el.querySelectorAll("[data-champ]")) {
    const k = champ.dataset.champ;
    if (k === "actif") champ.checked = actif;
    else if (champ.type === "checkbox") champ.checked = !!m[k];
    else if (k === "commande") champ.value = m.commande ? JSON.stringify(m.commande) : "";
    else champ.value = m[k] ?? "";
  }
  el.querySelector("[data-action=supprimer]").addEventListener("click", () => el.remove());
  el.querySelector("[data-action=tester]").addEventListener("click", async (ev) => {
    const retour = el.querySelector("[data-role=retour]");
    retour.textContent = "Test en cours…"; ev.target.disabled = true;
    try {
      const r = await api("POST", "/api/tester", { genre, moteur: lireMoteur(el) });
      retour.textContent = (r.ok ? "✓ " : "✗ ") + r.detail;
    } catch (err) { retour.textContent = "✗ " + err.message; }
    finally { ev.target.disabled = false; }
  });
  return el;
}

function lireMoteur(el) {
  const m = {};
  for (const champ of el.querySelectorAll("[data-champ]")) {
    const k = champ.dataset.champ;
    if (k === "actif") continue;
    if (champ.type === "checkbox") m[k] = champ.checked;
    else if (k === "commande") { const v = champ.value.trim(); if (v) m.commande = JSON.parse(v); }
    else { const v = champ.value.trim(); if (v || k === "id" || k === "nom" || k === "cle") m[k] = v; }
  }
  return m;
}

$("#btn-ajout-transcription").addEventListener("click", () => $("#liste-transcriptions").appendChild(ligneMoteur("transcription", { id: "", nom: "", url: "", cle: "" }, false)));
$("#btn-ajout-traducteur").addEventListener("click", () => $("#liste-traducteurs").appendChild(ligneMoteur("traducteur", { id: "", nom: "", type: "openai", modele: "", url: "", cle: "" }, false)));

$("#btn-enregistrer-reglages").addEventListener("click", async () => {
  const msg = $("#reglages-message");
  try {
    const c = JSON.parse(JSON.stringify(config));
    c.sortie = $("#cfg-sortie").value.trim(); c.langue = $("#cfg-langue").value.trim();
    c.youtube = { ...(c.youtube || {}), commande: JSON.parse($("#cfg-youtube").value) };
    c.traduction = { ...(c.traduction || {}), lot: Number($("#cfg-lot").value) || 25 };
    c.web = { ...(c.web || {}), inactivite_min: Number($("#cfg-inactivite").value) || 0 };
    c.sous_titres = { caracteres_par_ligne: Number($("#cfg-cpl").value), lignes: Number($("#cfg-lignes").value), duree_max: Number($("#cfg-dmax").value),
                      duree_min: Number($("#cfg-dmin").value), pause_coupe: Number($("#cfg-pause").value), ecart_min: (c.sous_titres || {}).ecart_min ?? 0.08,
                      lecture_cps: Number($("#cfg-cps").value) };
    c.nettoyage = lireNettoyage();
    const lire = (sel, nomRadio) => {
      const liste = [], blocs = [...document.querySelectorAll(sel + " .moteur")];
      let actif = null;
      for (const el of blocs) {
        const m = lireMoteur(el);
        if (!m.id) throw new Error("chaque moteur doit avoir un id");
        if (liste.some((x) => x.id === m.id)) throw new Error(`id en double : ${m.id}`);
        if (el.querySelector("[data-champ=actif]").checked) actif = m.id;
        liste.push(m);
      }
      if (!actif && liste.length) actif = liste[0].id;
      return [liste, actif];
    };
    [c.transcriptions, c.transcription_active] = lire("#liste-transcriptions");
    [c.traducteurs, c.traducteur_actif] = lire("#liste-traducteurs");
    config = await api("PUT", "/api/config", c);
    msg.textContent = "Enregistré.";
    remplirSelect($("#opt-transcription"), config.transcriptions, config.transcription_active);
    remplirSelect($("#opt-traducteur"), config.traducteurs, config.traducteur_actif);
    $("#opt-sortie").value = config.sortie; $("#opt-langue").value = config.langue || "";
    majZoneSortie();
    afficherReglagesTraducteur();
    majEtat();
    setTimeout(() => dlg.close(), 500);
  } catch (err) { msg.textContent = "Erreur : " + err.message; }
});

// --- Nettoyage du dossier de travail -------------------------------------------------------------------------

function lireNettoyage() {
  return { audio: $("#cfg-net-audio").checked, sous_titres: $("#cfg-net-sous-titres").checked, transcription: $("#cfg-net-transcription").checked,
           journaux: $("#cfg-net-journaux").checked, journaux_age_h: Number($("#cfg-net-age").value) || 0 };
}

function formaterOctets(o) { return o >= 1e9 ? (o / 1e9).toFixed(2) + " Go" : o >= 1e6 ? (o / 1e6).toFixed(0) + " Mo" : (o / 1e3).toFixed(0) + " Ko"; }

function montrerBilan(categories) {
  for (const el of document.querySelectorAll("[data-bilan]")) {
    const b = categories[el.dataset.bilan];
    el.textContent = b ? `(${b.fichiers} fichier${b.fichiers > 1 ? "s" : ""}, ${formaterOctets(b.octets)})` : "";
  }
}

async function afficherBilanNettoyage() {
  try { montrerBilan((await api("GET", "/api/nettoyage?sortie=" + encodeURIComponent($("#cfg-sortie").value.trim()))).categories); }
  catch (err) { $("#nettoyage-retour").textContent = err.message; }
}

$("#btn-nettoyer").addEventListener("click", async (ev) => {
  const retour = $("#nettoyage-retour");
  ev.target.disabled = true; retour.textContent = "Nettoyage…";
  try {
    const r = await api("POST", "/api/nettoyage", { options: lireNettoyage(), sortie: $("#cfg-sortie").value.trim() });
    const total = Object.values(r.supprime).reduce((a, b) => ({ fichiers: a.fichiers + b.fichiers, octets: a.octets + b.octets }), { fichiers: 0, octets: 0 });
    retour.textContent = total.fichiers ? `${total.fichiers} fichier(s) supprimé(s), ${formaterOctets(total.octets)} libérés.` : "Rien à supprimer.";
    montrerBilan(r.categories);
  } catch (err) { retour.textContent = "Échec : " + err.message; }
  finally { ev.target.disabled = false; }
});

// --- Divers ---------------------------------------------------------------------------------------------------

function nomFichier(source) {
  const s = String(source || "");
  return /^https?:/.test(s) ? s : s.split(/[\\/]/).pop();
}

function formaterDuree(s) {
  s = Math.round(Number(s) || 0);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h} h ${String(m).padStart(2, "0")} min` : m ? `${m} min ${String(sec).padStart(2, "0")} s` : `${sec} s`;
}
function formaterDate(t) {
  if (!t) return "";
  const d = new Date(t * 1000);
  return d.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" }) + " " + d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
}
for (const id of ["opt-transcription", "opt-traducteur"]) $("#" + id).addEventListener("change", majEtat);
$("#opt-traducteur").addEventListener("change", () => { afficherReglagesTraducteur(); majPauseParDefaut(); });
setInterval(majEtat, 15000);
/* Les commandes ne s'activent qu'une fois connu ce qui tourne déjà : le contenu ne change plus sous le curseur. */
charger().then(() => {
  document.body.classList.remove("chargement");
  const page = document.querySelector("main");
  page.inert = false; page.removeAttribute("aria-busy");
  $("#btn-reglages").disabled = false;
}).catch((err) => {
  document.querySelector("#chargement .rond").hidden = true;
  $("#chargement-texte").textContent = `Le service ne répond pas (${err.message}). Le relancer depuis le module de la page Nouvel onglet, puis recharger cette page.`;
});
