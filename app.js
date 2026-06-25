/* =============================================================================
 * Carte de fraîcheur — Paris 8e
 * -----------------------------------------------------------------------------
 * Estimation de la chaleur ressentie par rue, en fonction de l'heure et du
 * scénario météo.
 *
 *   T_ressentie = T_base + Modificateur_Ombre(heure, hauteur_bati)
 *                        + Modificateur_Bitume - Modificateur_Fraicheur
 *
 * Les relations spatiales statiques (hauteur du bâti voisin `h_bati`, proximité
 * d'un espace frais `cool`) sont précalculées par filter_data.py et stockées
 * dans les propriétés de chaque tronçon. Le navigateur n'effectue donc que
 * l'arithmétique dépendante de l'heure et du scénario, ce qui garde l'app fluide.
 * ========================================================================== */

"use strict";

/* ----------------------------- Paramètres ------------------------------- */
const SCENARIOS = {
  normal:   { label: "Normal",   tBase: 28 },
  canicule: { label: "Canicule", tBase: 38 },
};

const ALGO = {
  bitume: 2.5,        // malus asphalte (°C), constant sur les rues
  fraicheur: 4.5,     // bonus évapotranspiration (°C) si proche d'un espace frais
  shadeMin: 2,        // modificateur d'ombre minimal (rue ombragée)
  shadeMax: 12,       // modificateur d'ombre maximal (plein soleil, midi)
  streetWidthM: 12,   // largeur indicative de rue pour le test d'ombre portée
  sunPeakHour: 14,    // midi solaire approx. (été, heure légale)
  sunMaxElevDeg: 62,  // élévation solaire maxi (été à Paris)
};

// Échelle de couleurs : du frais (bleu/vert) au très chaud (rouge/violet)
const TEMP_MIN = 26, TEMP_MAX = 53;
const COLOR_STOPS = [
  [0.00, [ 43, 131, 186]], // bleu
  [0.20, [ 90, 196, 196]], // cyan
  [0.40, [120, 198, 121]], // vert
  [0.55, [255, 221, 100]], // jaune
  [0.70, [253, 162,  62]], // orange
  [0.85, [220,  58,  44]], // rouge
  [1.00, [123,  31, 122]], // violet
];

/* ----------------------------- État global ------------------------------ */
const state = {
  hour: 14,
  scenario: "normal",
  troncons: null,
  layers: {},
};

/* =============================== Modèle solaire ========================== */
// Élévation du soleil (degrés), parabole centrée sur le midi solaire.
function sunElevation(hour) {
  const t = (hour - ALGO.sunPeakHour) / 6.5;
  return Math.max(2, ALGO.sunMaxElevDeg * (1 - t * t));
}
// Azimut indicatif : Est le matin -> Sud à midi -> Ouest le soir.
function sunAzimuth(hour) {
  return 95 + (hour - 8) * (265 - 95) / 12;
}

/* =============================== Algorithme ============================== */
/**
 * Modificateur d'ombre.
 * On compare la longueur de l'ombre portée par le bâti voisin à la largeur de
 * la rue. Ombre longue (matin/soir, ou bâti haut sur rue étroite) => rue
 * ombragée (+2 °C). Ombre courte (soleil haut + bâti bas) => rue ensoleillée,
 * modificateur croissant avec l'élévation jusqu'à +12 °C en plein midi.
 */
function shadeModifier(hour, hBati) {
  const elevDeg = sunElevation(hour);
  const elevRad = elevDeg * Math.PI / 180;
  const shadowLen = hBati > 0 ? hBati / Math.tan(elevRad) : 0;
  const shaded = hBati > 0 && shadowLen >= ALGO.streetWidthM;

  if (shaded) return { value: ALGO.shadeMin, sunlit: false, elevDeg };

  // Ensoleillée : interpolation shadeMin -> shadeMax selon l'élévation solaire.
  const f = Math.max(0, Math.min(1, elevDeg / ALGO.sunMaxElevDeg));
  const value = ALGO.shadeMin + (ALGO.shadeMax - ALGO.shadeMin) * f;
  return { value, sunlit: true, elevDeg };
}

/** Température ressentie d'un tronçon + détail des composantes. */
function feltTemperature(props, hour, scenario) {
  const tBase = SCENARIOS[scenario].tBase;
  const shade = shadeModifier(hour, props.h_bati || 0);
  const bitume = ALGO.bitume;
  const fraicheur = props.cool ? ALGO.fraicheur : 0;
  const temp = tBase + shade.value + bitume - fraicheur;
  return { temp, tBase, shade, bitume, fraicheur };
}

/* =============================== Couleurs ================================ */
function lerp(a, b, t) { return a + (b - a) * t; }

function colorForTemp(temp) {
  let f = (temp - TEMP_MIN) / (TEMP_MAX - TEMP_MIN);
  f = Math.max(0, Math.min(1, f));
  for (let i = 1; i < COLOR_STOPS.length; i++) {
    const [p0, c0] = COLOR_STOPS[i - 1];
    const [p1, c1] = COLOR_STOPS[i];
    if (f <= p1) {
      const t = (f - p0) / (p1 - p0);
      const r = Math.round(lerp(c0[0], c1[0], t));
      const g = Math.round(lerp(c0[1], c1[1], t));
      const b = Math.round(lerp(c0[2], c1[2], t));
      return `rgb(${r},${g},${b})`;
    }
  }
  const last = COLOR_STOPS[COLOR_STOPS.length - 1][1];
  return `rgb(${last[0]},${last[1]},${last[2]})`;
}

function buildLegendGradient() {
  const stops = COLOR_STOPS.map(([p, c]) =>
    `rgb(${c[0]},${c[1]},${c[2]}) ${Math.round(p * 100)}%`).join(", ");
  document.getElementById("legendBar").style.background =
    `linear-gradient(90deg, ${stops})`;
  document.getElementById("legMin").textContent = `${TEMP_MIN}°`;
  document.getElementById("legMax").textContent = `${TEMP_MAX}°+`;
}

/* =============================== Carte =================================== */
const map = L.map("map", { zoomControl: true, preferCanvas: true })
  .setView([48.8718, 2.3120], 14);

L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
  attribution:
    '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> ' +
    '&copy; <a href="https://carto.com/attributions">CARTO</a>',
  subdomains: "abcd",
  maxZoom: 20,
}).addTo(map);

/* ----------------------------- Styles couches --------------------------- */
function streetStyle(feature) {
  const { temp } = feltTemperature(feature.properties, state.hour, state.scenario);
  feature.properties._temp = temp;
  return { color: colorForTemp(temp), weight: 5, opacity: 0.9, lineCap: "round" };
}

function streetPopup(feature) {
  const p = feature.properties;
  const d = feltTemperature(p, state.hour, state.scenario);
  const tag = d.shade.sunlit
    ? '<span class="tag sun">Ensoleillée</span>'
    : '<span class="tag shade">Ombragée</span>';
  const cool = p.cool ? ' <span class="tag cool">Près d\'un espace frais</span>' : "";
  return `
    <div class="popup-title">Tronçon de rue ${tag}${cool}</div>
    <div class="popup-temp" style="color:${colorForTemp(d.temp)}">${d.temp.toFixed(1)} °C ressentis</div>
    <div class="popup-row"><span class="k">Base météo</span><span>${d.tBase} °C</span></div>
    <div class="popup-row"><span class="k">Ombre / soleil</span><span>+${d.shade.value.toFixed(1)} °C</span></div>
    <div class="popup-row"><span class="k">Bitume</span><span>+${d.bitume.toFixed(1)} °C</span></div>
    <div class="popup-row"><span class="k">Fraîcheur</span><span>${d.fraicheur ? "-" + d.fraicheur.toFixed(1) : "0.0"} °C</span></div>
    <hr style="border:none;border-top:1px solid #ddd;margin:6px 0" />
    <div class="popup-row"><span class="k">Hauteur bâti voisin</span><span>${(p.h_bati || 0).toFixed(0)} m</span></div>
    ${p.cool_dist != null ? `<div class="popup-row"><span class="k">Espace frais le + proche</span><span>${p.cool_dist} m</span></div>` : ""}
  `;
}

/* ----------------------------- Chargement ------------------------------- */
async function loadGeoJSON(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

async function init() {
  buildLegendGradient();
  try {
    const [bound, troncons, verts, ilots, arbres, meta] = await Promise.all([
      loadGeoJSON("data/arrondissement_8e.geojson"),
      loadGeoJSON("data/troncon_8e.geojson"),
      loadGeoJSON("data/espaces_verts_8e.geojson"),
      loadGeoJSON("data/ilots_fraicheur_8e.geojson"),
      loadGeoJSON("data/arbres_8e.geojson"),
      loadGeoJSON("data/meta.json"),
    ]);

    state.troncons = troncons;

    // Limite de l'arrondissement
    const boundary = L.geoJSON(bound, {
      style: { color: "#5a6b7b", weight: 2, dashArray: "6 5", fill: false },
      interactive: false,
    }).addTo(map);
    map.fitBounds(boundary.getBounds(), { padding: [20, 20] });

    // Espaces verts (parcs / jardins)
    state.layers.green = L.geoJSON(verts, {
      style: { color: "#2e9e5b", weight: 1, fillColor: "#86d6a3", fillOpacity: 0.45 },
      onEachFeature: (f, l) => l.bindPopup(
        `<div class="popup-title">${f.properties.nom_ev || "Espace vert"}</div>
         <div>${f.properties.type_ev || ""}</div>`),
    }).addTo(map);

    // Îlots de fraîcheur
    state.layers.ilot = L.geoJSON(ilots, {
      style: { color: "#1f8fb0", weight: 1, fillColor: "#79d0e6", fillOpacity: 0.35 },
      onEachFeature: (f, l) => l.bindPopup(
        `<div class="popup-title">${f.properties.nom || "Îlot de fraîcheur"}</div>
         <div>${f.properties.type || ""}</div>`),
    }).addTo(map);

    // Rues colorées par chaleur ressentie (au-dessus des zones)
    state.layers.heat = L.geoJSON(troncons, {
      style: streetStyle,
      onEachFeature: (f, l) => l.bindPopup(() => streetPopup(f)),
    }).addTo(map);

    // Arbres remarquables
    state.layers.tree = L.geoJSON(arbres, {
      pointToLayer: (f, latlng) => L.circleMarker(latlng, {
        radius: 5, color: "#1b7a3d", fillColor: "#34c759", fillOpacity: 0.9, weight: 1.5,
      }),
      onEachFeature: (f, l) => l.bindPopup(
        `<div class="popup-title">🌳 ${f.properties.com_nom_usuel || f.properties.arbres_libellefrancais || "Arbre remarquable"}</div>
         <div><i>${f.properties.com_nom_latin || ""}</i></div>
         ${f.properties.arbres_hauteurenm ? `<div>Hauteur : ${f.properties.arbres_hauteurenm} m</div>` : ""}`),
    }).addTo(map);

    renderStats(meta);
    updateSunInfo();
  } catch (err) {
    document.getElementById("stats").innerHTML =
      `<b>Erreur de chargement.</b><br />${err.message}<br /><br />` +
      `Servez le dossier via un serveur local :<br /><code>python3 -m http.server</code> ` +
      `puis ouvrez <code>http://localhost:8000</code>.`;
    console.error(err);
  }
}

/* ----------------------------- Mises à jour ----------------------------- */
function refreshHeat() {
  if (state.layers.heat) {
    state.layers.heat.setStyle(streetStyle);
  }
  updateSunInfo();
}

function updateSunInfo() {
  document.getElementById("sunElev").textContent = `${sunElevation(state.hour).toFixed(0)}°`;
  document.getElementById("sunAz").textContent = `${sunAzimuth(state.hour).toFixed(0)}°`;
}

function renderStats(meta) {
  const c = meta.counts;
  document.getElementById("stats").innerHTML =
    `<b>${c.troncons}</b> tronçons de rue · <b>${c.troncons_frais}</b> à proximité d'un espace frais<br />` +
    `<b>${c.espaces_verts}</b> espaces verts · <b>${c.ilots_fraicheur}</b> îlots de fraîcheur · <b>${c.arbres}</b> arbres remarquables<br />` +
    `Hauteur moyenne du bâti : <b>${meta.h_bati.mean} m</b> (max ${meta.h_bati.max} m, ${c.batiments_indexes.toLocaleString("fr")} bâtiments analysés)`;
}

/* ------------------------------ Interface ------------------------------- */
const hourInput = document.getElementById("hour");
hourInput.addEventListener("input", (e) => {
  state.hour = +e.target.value;
  document.getElementById("hourValue").textContent = state.hour;
  refreshHeat();
});

document.querySelectorAll('input[name="scenario"]').forEach((r) =>
  r.addEventListener("change", (e) => {
    if (e.target.checked) { state.scenario = e.target.value; refreshHeat(); }
  }));

const toggles = {
  layHeat: "heat", layGreen: "green", layIlot: "ilot", layTree: "tree",
};
Object.entries(toggles).forEach(([id, key]) => {
  document.getElementById(id).addEventListener("change", (e) => {
    const layer = state.layers[key];
    if (!layer) return;
    if (e.target.checked) layer.addTo(map);
    else map.removeLayer(layer);
  });
});

init();
