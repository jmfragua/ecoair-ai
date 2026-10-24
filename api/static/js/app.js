// EcoAir AI - dashboard publico. Sin librerias ni recursos externos.
import {
  HORIZONTES_GRAFICA, OPCION_REPLAY, RANGOS, VELOCIDADES, avisoSalto, filasTooltip, mensajeError, msLocal, pathTramos,
  periodoMetricas, siguienteHora, textoMetricasSerie, urlModoReplay, urlModoVivo, vistaActualReplay, vistaEstaciones,
  vistaMetricas, vistaReplay,
} from "./replay-vista.js";
import { vistaRecomendacion, SIN_DATOS_RECIENTES } from "./recomendaciones-vista.js";
import { vistaAhora, etiquetaMetrica } from "./ahora-vista.js";
// Todo el texto que viene de la API se inserta con textContent (nunca innerHTML).

const PARAMS = new URLSearchParams(location.search);
const DEVICE = PARAMS.get("device") || "ecoair-node-01";
// Modo replay historico (?modo=replay): el panel se alimenta SOLO de /predictions/replay* (datos del
// IDEAM en external_measurements). No consulta mediciones en vivo, alertas ni suscripciones.
const MODO_REPLAY = PARAMS.get("modo") === "replay";
const REFRESH_MS = 10_000;          // calidad actual, ultima medicion y alertas
const HISTORY_REFRESH_MS = 60_000;  // la grafica usa intervalos de 5 min: basta cada minuto
const FETCH_TIMEOUT_MS = 8_000;
const LIMITE = { pm25: 37, pm10: 75 }; // Res. 2254/2017, limite diario (24 h)
const TZ = "America/Bogota";
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;

// Categorias del ICA (Res. 2254/2017, Tabla 5/6). El color nunca va solo: siempre con nombre.
const CATS = [
  { code: "buena", name: "Buena", short: "Buena", color: "#3fb950", lo: 0, hi: 50 },
  { code: "aceptable", name: "Aceptable", short: "Aceptable", color: "#e3c02b", lo: 51, hi: 100 },
  { code: "danina_sensibles", name: "Dañina para grupos sensibles", short: "Dañina sens.", color: "#f0883e", lo: 101, hi: 150 },
  { code: "danina", name: "Dañina para la salud", short: "Dañina", color: "#e5484d", lo: 151, hi: 200 },
  { code: "muy_danina", name: "Muy dañina para la salud", short: "Muy dañina", color: "#a371f7", lo: 201, hi: 300 },
  { code: "peligrosa", name: "Peligrosa", short: "Peligrosa", color: "#b9744f", lo: 301, hi: 500 },
];
const RULES = {
  ica_1h: "ICA (media 1 h)",
  tabla4_pm25: "Tabla 4 · PM2.5 (24 h)",
  tabla4_pm10: "Tabla 4 · PM10 (24 h)",
  sensor_congelado: "Sensor congelado",
  salto_temperatura: "Salto de temperatura",
  fuera_de_rango_fisico: "Valor fuera de rango",
  dispositivo_caido: "Dispositivo caído",
  isolation_forest: "Anomalía (modelo)",
};
const SEV = {
  critica: { label: "Crítica", icon: "!" },
  aviso: { label: "Aviso", icon: "!" },
  info: { label: "Info", icon: "i" },
};
const POLLUTANT = { pm25: "PM2.5", pm10: "PM10" };

const $ = (id) => document.getElementById(id);
const fmtHora = new Intl.DateTimeFormat("es-CO", { timeZone: TZ, hour: "2-digit", minute: "2-digit", hour12: false });
const fmtDia = new Intl.DateTimeFormat("es-CO", { timeZone: TZ, day: "numeric", month: "short" });
const fmtNum = (v, d = 1) => (v == null || Number.isNaN(v) ? "—" : Number(v).toLocaleString("es-CO", { minimumFractionDigits: d, maximumFractionDigits: d }));
const horaCol = (iso) => fmtHora.format(new Date(iso));
const fechaHoraCol = (iso) => {
  const d = new Date(iso);
  const hoy = fmtDia.format(new Date()) === fmtDia.format(d);
  return hoy ? fmtHora.format(d) : `${fmtDia.format(d)} ${fmtHora.format(d)}`;
};

function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") n.className = v;
    else if (v != null) n.setAttribute(k, v);
  }
  for (const c of children) if (c != null) n.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return n;
}
const svgEl = (tag, attrs = {}) => {
  const n = document.createElementNS("http://www.w3.org/2000/svg", tag); // espacio de nombres, no es una descarga
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
  return n;
};

// ---------- Red ----------
class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}
async function getJSON(path) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
  try {
    const r = await fetch(path, { signal: ctrl.signal, headers: { Accept: "application/json" } });
    if (!r.ok) {
      let detail = r.statusText;
      try { detail = (await r.json()).detail || detail; } catch { /* sin cuerpo JSON */ }
      throw new ApiError(r.status, detail);
    }
    return await r.json();
  } finally {
    clearTimeout(t);
  }
}
const dev = encodeURIComponent(DEVICE);

// ---------- Animacion de numeros ----------
function setNumber(node, value, decimals = 0) {
  const text = fmtNum(value, decimals);
  if (node.textContent === text) return;
  const from = parseFloat((node.dataset.v ?? "NaN"));
  node.dataset.v = value ?? "";
  if (REDUCED || value == null || Number.isNaN(from)) {
    node.textContent = text;
  } else {
    const start = performance.now(), dur = 700;
    const step = (now) => {
      const k = Math.min(1, (now - start) / dur), e = 1 - Math.pow(1 - k, 3);
      node.textContent = fmtNum(from + (value - from) * e, decimals);
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }
  node.classList.remove("flash"); void node.offsetWidth; node.classList.add("flash");
}

// ---------- Estado / avisos ----------
function setStatus(kind, text) {
  const s = $("status");
  s.className = `status status--${kind}`;
  $("status-text").textContent = text;
}
let lastOk = null;
function toast(msg) {
  const t = $("toast");
  if (!msg) { t.hidden = true; return; }
  t.textContent = msg; t.hidden = false;
}

// ---------- Medidor del ICA ----------
const G = { cx: 120, cy: 124, r: 100 };
const polar = (p) => {
  const a = Math.PI * (1 - p);
  return [G.cx + G.r * Math.cos(a), G.cy - G.r * Math.sin(a)];
};
const arcPath = (p0, p1) => {
  const [x0, y0] = polar(p0), [x1, y1] = polar(p1);
  return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${G.r} ${G.r} 0 0 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
};
function initGauge() {
  const bands = $("gauge-bands");
  CATS.forEach((c, i) => {
    const gap = 0.006;
    bands.append(svgEl("path", { d: arcPath(i / 6 + gap, (i + 1) / 6 - gap), class: "gauge__band", stroke: c.color, "data-code": c.code }));
  });
  const full = arcPath(0, 1);
  $("gauge-track").setAttribute("d", full);
  const arc = $("gauge-arc");
  arc.setAttribute("d", full);
  const len = Math.PI * G.r;
  arc.style.strokeDasharray = `${len}`;
  arc.style.strokeDashoffset = `${len}`;
  const [x, y] = polar(0);
  $("gauge-dot").setAttribute("cx", x); $("gauge-dot").setAttribute("cy", y);

  const scale = $("ica-scale");
  for (const c of CATS) {
    scale.append(el("li", { style: `--c:${c.color}`, "data-code": c.code, title: c.name, "aria-label": `${c.name}, ICA ${c.lo} a ${c.hi}` },
      c.short, el("span", { class: "scale__range" }, `${c.lo}–${c.hi}`)));
  }
}
function gaugePos(ica) {
  let i = CATS.findIndex((c) => ica <= c.hi);
  if (i === -1) i = CATS.length - 1; // por encima de 500: extremo del medidor
  const c = CATS[i];
  const f = Math.min(1, Math.max(0, (ica - c.lo) / (c.hi - c.lo)));
  return Math.min(1, (i + f) / CATS.length);
}
function setActiveBand(code) {
  for (const b of $("gauge-bands").children) b.classList.toggle("gauge__band--active", b.dataset.code === code);
}
function setGauge(ica) {
  const arc = $("gauge-arc"), len = Math.PI * G.r;
  const p = ica == null ? 0 : gaugePos(ica);
  arc.style.strokeDashoffset = `${len * (1 - p)}`;
  const [x, y] = polar(p);
  $("gauge-dot").setAttribute("cx", x.toFixed(2)); $("gauge-dot").setAttribute("cy", y.toFixed(2));
}

// ---------- Calidad del aire actual ----------
function renderCurrent(aq) {
  const card = document.querySelector(".card--ica");
  if (aq.estado !== "ok") {
    card.classList.add("is-nodata");
    document.documentElement.style.setProperty("--cat", "#6b6a63");
    const u = aq.ultima_medicion;
    const min = u ? Math.floor(u.antiguedad_s / 60) : null;
    setStatus("stale", u ? `Sin datos hace ${min} min` : "Sin datos");
    setGauge(null);
    $("ica-value").textContent = "—"; $("ica-value").dataset.v = "";
    $("ica-cat").textContent = "Sin datos recientes";
    // La hora y la antiguedad de la ultima medicion ya las muestra la cuadricula (now-ultima).
    $("ica-by").textContent = u ? "No se calcula el ICA: más de 5 min sin mediciones" : "El dispositivo aún no tiene mediciones";
    $("gauge-label").textContent = "Medidor del ICA: sin datos recientes";
    for (const li of $("ica-scale").children) li.removeAttribute("aria-current");
    setActiveBand(null);
    renderRec(null, null, SIN_DATOS_RECIENTES);
    for (const p of ["pm25", "pm10"]) { $(`${p}-1h`).textContent = "—"; renderLimit(p, null, null); }
    return;
  }

  card.classList.remove("is-nodata");
  setStatus("ok", "En línea");
  const g = aq.ica.general;
  const cat = CATS.find((c) => c.code === g.category_code) || CATS[0];
  document.documentElement.style.setProperty("--cat", cat.color);
  setNumber($("ica-value"), g.ica, 0);
  setGauge(g.ica);
  $("ica-cat").textContent = g.category_name;
  $("gauge-label").textContent = `Medidor del ICA: ${g.ica}, categoría ${g.category_name}`;
  $("ica-by").replaceChildren(`Lo define ${POLLUTANT[g.definido_por]}`,
    el("span", { class: "ica__parts" }, `ICA PM2.5: ${aq.ica.pm25.ica} · ICA PM10: ${aq.ica.pm10.ica}`));
  for (const li of $("ica-scale").children) {
    if (li.dataset.code === cat.code) li.setAttribute("aria-current", "true"); else li.removeAttribute("aria-current");
  }
  setActiveBand(cat.code);
  renderRec(aq.recomendacion, g.category_name);

  const v1 = aq.ventana_1h, v24 = aq.ventana_24h;
  setNumber($("pm25-1h"), v1.pm25_media, 1);
  setNumber($("pm10-1h"), v1.pm10_media, 1);
  renderLimit("pm25", v24.pm25_media, v24);
  renderLimit("pm10", v24.pm10_media, v24);
}

function renderLimit(p, media, v24) {
  const bar = $(`${p}-bar`), fill = bar.querySelector(".bar__fill");
  const lim = LIMITE[p];
  if (media == null) {
    $(`${p}-24h`).textContent = "—";
    fill.style.width = "0%"; bar.dataset.state = "";
    bar.setAttribute("aria-valuenow", "0"); bar.setAttribute("aria-valuetext", "sin datos");
    $(`${p}-note`).textContent = `Límite diario: ${lim} µg/m³`;
    return;
  }
  const pct = (media / lim) * 100;
  $(`${p}-24h`).textContent = `${fmtNum(media)} de ${lim}`;
  fill.style.width = `${Math.min(100, (media / (lim * 1.5)) * 100)}%`; // la marca del limite esta al 66,7 %
  bar.dataset.state = pct > 100 ? "crit" : pct >= 80 ? "warn" : "";
  bar.setAttribute("aria-valuenow", String(Math.round(pct)));
  bar.setAttribute("aria-valuemax", "150");
  bar.setAttribute("aria-valuetext", `${fmtNum(media)} µg/m³, ${Math.round(pct)} % del límite diario${pct > 100 ? " (lo supera)" : ""}`);
  const parcial = v24 && v24.parcial;
  const largo = (pct > 100 ? "Supera el límite diario" : `Límite diario: ${lim} µg/m³`)
    + (parcial ? ` · parcial: solo ${fmtNum(v24.horas_con_datos)} h de datos` : "");
  const corto = (pct > 100 ? "Supera el límite" : `Límite ${lim}`)
    + (parcial ? ` · parcial (${fmtNum(v24.horas_con_datos)} h)` : "");
  $(`${p}-note`).replaceChildren(el("span", { class: "t-long" }, largo), el("span", { class: "t-short" }, corto));
}

// Tarjeta de recomendaciones: resumen corto + desplegable con el detalle por grupos. Los textos
// vienen de shared/recomendaciones.py (los mismos del correo). Sin recomendacion valida, se dice.
let recCargada = false;
function renderRec(rec, catName, motivo) {
  const v = vistaRecomendacion(rec, motivo);
  // Sin recomendacion: solo un texto breve (nunca un cuadro vacio con "—" ni recomendaciones inventadas)
  for (const id of ["rec-cat", "rec-titulo-efectos", "rec-efectos"]) $(id).hidden = !v.disponible;
  $("rec-vacio").hidden = v.disponible;
  $("rec-vacio").textContent = v.disponible ? "" : v.mensaje;
  $("rec-cat").textContent = v.disponible ? catName : "";
  $("rec-titulo-efectos").textContent = v.tituloEfectos || "Qué significa";
  $("rec-efectos").textContent = v.disponible ? v.efectos : "";
  $("rec-resumen").textContent = v.resumen;
  $("rec-completo").replaceChildren(...v.secciones.map((s) => el("section", { class: "rec__sec" },
    el("h3", {}, s.titulo),
    s.nota ? el("p", { class: "rec__nota" }, s.nota) : null,
    ...s.parrafos.map((p) => el("p", {}, p)),
    s.lista.length ? el("ul", {}, ...s.lista.map((x) => el("li", {}, x))) : null)));
  $("rec-detalle").hidden = !v.disponible;
  $("rec-fuente").replaceChildren(...v.fuentes.map((f) => el("li", {}, f)));
  $("rec-etiqueta").textContent = v.etiqueta;
  if (v.disponible) recCargada = true;
}

// ---------- Ultima medicion ----------
function renderLatest(m) {
  setNumber($("pm25-now"), m.pm25, 1);
  setNumber($("pm10-now"), m.pm10, 1);
  setNumber($("temp"), m.temperature, 1);
  setNumber($("hum"), m.humidity, 0);
  setRange("temp", m.temperature);
  setRange("hum", m.humidity);
  const t = m.received_at ? new Date(m.received_at) : null;
  $("env-time").textContent = t && !Number.isNaN(t.getTime()) ? `Medido a las ${fechaHoraCol(m.received_at)} (hora de Colombia)` : " ";
}

// Cuadricula 2x2 de la tarjeta del ICA: ultimo registro (ultimaMedicion) y su antiguedad.
// Si la consulta falla se conservan los ultimos valores y la antiguedad sigue contando.
let ultimaMedicion = null;
function renderAhora(fallo) {
  const v = vistaAhora(ultimaMedicion, Date.now(), { fallo });
  $("now-pm25").textContent = v.pm25;
  $("now-pm10").textContent = v.pm10;
  $("now-temp").textContent = v.temperatura;
  $("now-hum").textContent = v.humedad;
  $("now-ultima").textContent = v.ultima;
  $("now-aviso").textContent = v.aviso;
  $("now-aviso").hidden = !v.aviso;
  const lbl = etiquetaMetrica(v);
  for (const p of ["pm25", "pm10"]) { $(`${p}-lbl-l`).textContent = lbl.largo; $(`${p}-lbl-s`).textContent = lbl.corto; }
}

// Franja de confort orientativa (no normativa) para temperatura y humedad.
function initRanges() {
  for (const r of document.querySelectorAll(".range")) {
    const { min, max, lo, hi } = r.dataset;
    const band = r.querySelector(".range__band");
    band.style.left = `${((lo - min) / (max - min)) * 100}%`;
    band.style.width = `${((hi - lo) / (max - min)) * 100}%`;
  }
}
function setRange(id, value) {
  const r = $(`${id}-range`), state = $(`${id}-state`);
  if (value == null) { r.classList.remove("has-value"); state.textContent = " "; return; }
  const min = +r.dataset.min, max = +r.dataset.max, lo = +r.dataset.lo, hi = +r.dataset.hi;
  const pct = Math.min(100, Math.max(0, ((value - min) / (max - min)) * 100));
  r.querySelector(".range__dot").style.left = `${pct}%`;
  r.classList.add("has-value");
  state.textContent = value < lo ? "Por debajo de la referencia"
    : value > hi ? "Por encima de la referencia" : "Dentro de la referencia";
}

// ---------- Alertas ----------
function duracion(desdeIso, hastaIso) {
  if (!desdeIso || !hastaIso) return null;
  const min = Math.max(0, Math.round((new Date(hastaIso) - new Date(desdeIso)) / 60_000));
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60), m = min % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}
function renderAlerts(list) {
  const ol = $("alerts");
  const abiertas = list.filter((a) => a.abierta).length;
  $("alerts-count").textContent = abiertas ? `${abiertas} abierta${abiertas > 1 ? "s" : ""}` : "ninguna abierta";
  $("alerts-empty").hidden = list.length > 0;
  ol.replaceChildren(...list.map((a) => {
    const sev = SEV[a.severity] || SEV.info;
    const inicio = a.started_at || a.detected_at;
    const fin = a.abierta ? null : a.ended_at;
    const tiempo = a.abierta
      ? `Desde ${fechaHoraCol(inicio)} (hora de Colombia)`
      : `${fechaHoraCol(inicio)} – ${fin ? fechaHoraCol(fin) : "?"} (hora de Colombia)`;
    const dur = duracion(inicio, a.abierta ? new Date().toISOString() : fin);
    return el("li", {},
      el("span", { class: `sev sev--${a.severity || "info"}`, "aria-hidden": "true" }, sev.icon),
      el("div", { class: "al__head" },
        el("span", { class: "al__rule" }, RULES[a.rule_code] || a.rule_code || a.kind),
        el("span", { class: "al__sev" }, sev.label),
        el("span", { class: `al__state${a.abierta ? " al__state--open" : ""}` }, a.abierta ? "Abierta" : "Cerrada"),
        a.kind === "anomaly" ? el("span", { class: "al__model", title: "Detección de un modelo (Isolation Forest), no es una regla de la norma" }, "modelo, experimental") : null,
        a.is_simulated ? el("span", { class: "al__sim" }, "SIMULACIÓN") : null),
      el("p", { class: "al__time" }, tiempo),
      dur ? el("p", { class: "al__time" },
        el("span", { class: "al__dur" }, `${a.abierta ? "Lleva" : "Duración:"} ${dur}`)) : null,
      el("details", { class: "al__detail" },
        el("summary", {}, "Ver detalle"),
        el("p", { class: "al__msg" }, a.message)));
  }));
}

// ---------- Grafica de tendencia (SVG propio) ----------
let lastHistory = null, hoverIdx = null;

// Colombia no tiene horario de verano: UTC-5 fijo. Sirve para alinear las marcas a la hora local.
const COL_OFFSET_MS = -5 * 3_600_000;
const PASOS_MIN = [15, 30, 60, 120, 180, 360, 720];
function pasoEjeX(spanMs, anchoPx) {
  const maxEtiquetas = Math.max(2, Math.floor(anchoPx / 56));
  const paso = PASOS_MIN.find((m) => spanMs / (m * 60_000) <= maxEtiquetas) ?? PASOS_MIN.at(-1);
  return paso * 60_000;
}

// Escala Y: por defecto ajustada a los datos (maximo x 1,5, minimo 10); con el interruptor
// "Ver contra limites", la escala completa que incluye los limites diarios (37 y 75).
const PASOS_Y = [1, 2, 3, 4, 5, 10, 15, 20, 25, 30, 40, 50, 75, 100, 150, 200, 250, 500];
let contraLimites = false;
try { contraLimites = localStorage.getItem("ecoair.escala") === "limites"; } catch { /* sin almacenamiento */ }
function escalaY(datosMax) {
  const objetivo = contraLimites ? Math.max(LIMITE.pm10 * 1.1, datosMax) : Math.max(10, datosMax * 1.5);
  const paso = PASOS_Y.find((s) => s * 4 >= objetivo) ?? Math.ceil(objetivo / 4 / 100) * 100;
  return paso * 4; // 4 divisiones con marcas enteras
}
function initScaleToggle() {
  const b = $("chart-scale");
  b.setAttribute("aria-pressed", String(contraLimites));
  b.addEventListener("click", () => {
    contraLimites = !contraLimites;
    b.setAttribute("aria-pressed", String(contraLimites));
    try { localStorage.setItem("ecoair.escala", contraLimites ? "limites" : "datos"); } catch { /* sin almacenamiento */ }
    if (lastHistory) renderChart(lastHistory);
  });
}

function renderChart(h) {
  lastHistory = h;
  const box = $("chart"), svg = $("chart-svg");
  const W = svg.clientWidth || box.clientWidth, H = svg.clientHeight || 260;
  const narrow = W < 420;
  const M = { l: 34, r: narrow ? 12 : 54, t: 14, b: 26 };
  const iw = Math.max(10, W - M.l - M.r), ih = Math.max(10, H - M.t - M.b);
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.replaceChildren();

  const pts = h.puntos.map((p) => ({ ...p, ts: new Date(p.t).getTime() }));
  const t1 = new Date(h.hasta_utc).getTime();
  // Si los datos cubren menos de las horas pedidas, el eje X empieza en el primer dato
  // (minimo 1 h) para que la curva no quede aplastada a la derecha.
  const t0 = pts.length
    ? Math.max(new Date(h.desde_utc).getTime(), Math.min(pts[0].ts, t1 - 3_600_000))
    : new Date(h.desde_utc).getTime();
  const yMax = escalaY(Math.max(0, ...pts.map((p) => Math.max(p.pm25 ?? 0, p.pm10 ?? 0))));
  const x = (ts) => M.l + ((ts - t0) / (t1 - t0)) * iw;
  const y = (v) => M.t + ih - (v / yMax) * ih;

  // Rejilla y eje Y
  const ticks = 4;
  for (let i = 0; i <= ticks; i++) {
    const v = (yMax / ticks) * i, yy = y(v);
    svg.append(svgEl("line", { x1: M.l, x2: M.l + iw, y1: yy, y2: yy, class: i === 0 ? "c-axis" : "c-grid" }));
    const t = svgEl("text", { x: M.l - 6, y: yy + 4, "text-anchor": "end", class: "c-tick" });
    t.textContent = String(Math.round(v));
    svg.append(t);
  }
  // Eje X (hora de Colombia): el paso mas corto que deje ~56 px por etiqueta.
  const paso = pasoEjeX(t1 - t0, iw);
  // hora local = ts + COL_OFFSET_MS; se busca la primera marca con hora local multiplo del paso
  for (let ts = Math.ceil((t0 + COL_OFFSET_MS) / paso) * paso - COL_OFFSET_MS; ts <= t1; ts += paso) {
    const xx = x(ts);
    if (xx < M.l + 14 || xx > M.l + iw - 14) continue; // evita cortar etiquetas en los bordes
    const t = svgEl("text", { x: xx, y: M.t + ih + 18, "text-anchor": "middle", class: "c-tick" });
    t.textContent = fmtHora.format(new Date(ts));
    svg.append(t);
  }

  // Lineas de referencia: limites diarios de la norma. Si no caben en la escala, se avisa al borde.
  let fuera = 0;
  for (const [p, lim] of Object.entries(LIMITE)) {
    if (lim > yMax) {
      const t = svgEl("text", { x: M.l + iw, y: M.t + 10 + 13 * fuera++, "text-anchor": "end", class: "c-offscale" });
      t.textContent = `límite diario ${POLLUTANT[p]}: ${lim} (fuera de escala)`;
      svg.append(t);
      continue;
    }
    svg.append(svgEl("line", { x1: M.l, x2: M.l + iw, y1: y(lim), y2: y(lim), class: "c-ref" }));
    const t = svgEl("text", { x: M.l + 6, y: y(lim) - 5, class: "c-reflabel" });
    t.textContent = `Límite diario ${POLLUTANT[p]}: ${lim}`;
    svg.append(t);
  }

  if (!pts.length) {
    const t = svgEl("text", { x: M.l + iw / 2, y: M.t + ih / 2, "text-anchor": "middle", class: "c-empty" });
    t.textContent = "Sin datos en las últimas 24 h";
    svg.append(t);
  }

  // Series: se cortan donde faltan datos (mas de 2 intervalos sin puntos)
  const gapMs = h.bucket_min * 60_000 * 2;
  const ends = [];
  for (const key of ["pm25", "pm10"]) {
    const segs = [];
    let cur = [];
    pts.forEach((p, i) => {
      if (p[key] == null) return;
      if (cur.length && p.ts - pts[i - 1].ts > gapMs) { segs.push(cur); cur = []; }
      cur.push(p);
    });
    if (cur.length) segs.push(cur);
    for (const s of segs) {
      const line = s.map((p, i) => `${i ? "L" : "M"}${x(p.ts).toFixed(1)},${y(p[key]).toFixed(1)}`).join("");
      const area = `${line}L${x(s.at(-1).ts).toFixed(1)},${y(0)}L${x(s[0].ts).toFixed(1)},${y(0)}Z`;
      svg.append(svgEl("path", { d: area, class: `c-area c-${key}` }));
      svg.append(svgEl("path", { d: line, class: `c-line c-${key}` }));
    }
    const last = [...pts].reverse().find((p) => p[key] != null);
    if (last) ends.push({ key, p: last, yy: y(last[key]) });
  }
  for (const e of ends) svg.append(svgEl("circle", { cx: x(e.p.ts), cy: e.yy, r: 4, class: `c-dot c-${e.key}` }));
  // Etiquetas al final de cada linea, solo si caben y no chocan (si no, la leyenda basta)
  const choque = ends.length === 2 && Math.abs(ends[0].yy - ends[1].yy) < 14;
  if (!narrow && !choque) {
    for (const e of ends) {
      const t = svgEl("text", { x: x(e.p.ts) + 8, y: e.yy + 4, class: "c-endlabel" });
      t.textContent = `${POLLUTANT[e.key]} ${fmtNum(e.p[e.key])}`;
      svg.append(t);
    }
  }

  // Capa de interaccion: crosshair que busca el punto mas cercano en X
  const cross = svgEl("line", { y1: M.t, y2: M.t + ih, class: "c-cross", visibility: "hidden" });
  const hd = { pm25: svgEl("circle", { r: 4, class: "c-dot c-pm25", visibility: "hidden" }),
               pm10: svgEl("circle", { r: 4, class: "c-dot c-pm10", visibility: "hidden" }) };
  svg.append(cross, hd.pm25, hd.pm10);
  const tip = $("chart-tip");

  const show = (i) => {
    if (!pts.length) return;
    hoverIdx = Math.max(0, Math.min(pts.length - 1, i));
    const p = pts[hoverIdx], xx = x(p.ts);
    cross.setAttribute("x1", xx); cross.setAttribute("x2", xx); cross.setAttribute("visibility", "visible");
    for (const k of ["pm25", "pm10"]) {
      if (p[k] == null) { hd[k].setAttribute("visibility", "hidden"); continue; }
      hd[k].setAttribute("cx", xx); hd[k].setAttribute("cy", y(p[k])); hd[k].setAttribute("visibility", "visible");
    }
    const row = (k) => el("div", { class: "tip__row" },
      el("span", { class: `key key--${k}`, "aria-hidden": "true" }),
      el("strong", {}, fmtNum(p[k])), el("span", {}, `${POLLUTANT[k]} µg/m³`));
    tip.replaceChildren(el("div", { class: "tip__time" }, `${fechaHoraCol(p.t)} (hora de Colombia)`), row("pm25"), row("pm10"));
    tip.hidden = false;
    const tw = tip.offsetWidth;
    tip.style.left = `${Math.min(Math.max(0, xx + 12), W - tw)}px`;
    box.setAttribute("aria-label", `${fechaHoraCol(p.t)}: PM2.5 ${fmtNum(p.pm25)}, PM10 ${fmtNum(p.pm10)} µg/m³`);
  };
  const hide = () => {
    hoverIdx = null; tip.hidden = true;
    cross.setAttribute("visibility", "hidden"); hd.pm25.setAttribute("visibility", "hidden"); hd.pm10.setAttribute("visibility", "hidden");
  };
  const nearest = (clientX) => {
    const r = svg.getBoundingClientRect();
    const ts = t0 + ((clientX - r.left - M.l) / iw) * (t1 - t0);
    let best = 0;
    for (let i = 1; i < pts.length; i++) if (Math.abs(pts[i].ts - ts) < Math.abs(pts[best].ts - ts)) best = i;
    return best;
  };
  box.onpointermove = (ev) => show(nearest(ev.clientX));
  box.onpointerleave = hide;
  box.onblur = hide;
  box.onkeydown = (ev) => {
    if (!pts.length) return;
    if (ev.key === "ArrowLeft" || ev.key === "ArrowRight") {
      ev.preventDefault();
      show((hoverIdx ?? pts.length - 1) + (ev.key === "ArrowLeft" ? -1 : 1));
    } else if (ev.key === "Escape") hide();
  };

  // Titulo, nota de datos parciales y tabla
  $("chart-title").textContent = h.parcial
    ? `Tendencia · últimas ${fmtNum(h.horas_con_datos, 1)} h de datos`
    : `Tendencia de las últimas ${h.hours} h`;
  const note = $("chart-note");
  note.hidden = !h.parcial;
  note.textContent = h.parcial
    ? `Los datos cubren solo ${fmtNum(h.horas_con_datos)} h de las últimas ${h.hours} h; la gráfica muestra lo disponible.`
    : "";
  $("table-body").replaceChildren(...[...pts].reverse().map((p) => el("tr", {},
    el("td", {}, fechaHoraCol(p.t)), el("td", {}, fmtNum(p.pm25)), el("td", {}, fmtNum(p.pm10)),
    el("td", {}, fmtNum(p.temperature)), el("td", {}, fmtNum(p.humidity, 0)))));

  simDatos = !!h.is_simulated;
  setSimulacion();
}

// ---------- Ciclo de actualizacion ----------
let deviceMissing = false;

async function refresh() {
  if (deviceMissing) return;
  const [aq, latest, alerts] = await Promise.allSettled([
    getJSON(`/devices/${dev}/air-quality/current`),
    getJSON(`/devices/${dev}/measurements/latest`),
    getJSON(`/devices/${dev}/alerts?estado=todas&limit=15`),
  ]);
  if (aq.status === "rejected" && aq.reason?.status === 404) {
    deviceMissing = true;
    setStatus("error", "Dispositivo no encontrado");
    toast(`El dispositivo "${DEVICE}" no existe. Revise el parámetro ?device= de la dirección.`);
    return;
  }
  let fallos = 0;
  if (aq.status === "fulfilled") renderCurrent(aq.value);
  else { fallos++; if (!recCargada) renderRec(null); }
  if (latest.status === "fulfilled") { ultimaMedicion = latest.value; renderAhora(false); renderLatest(latest.value); }
  else if (latest.reason?.status === 404) { ultimaMedicion = null; renderAhora(false); } // 404 aqui = aun no hay mediciones
  else { fallos++; renderAhora(true); }
  if (alerts.status === "fulfilled") renderAlerts(alerts.value);
  else if (alerts.reason?.status === 503) { $("alerts-empty").hidden = false; $("alerts-empty").textContent = "Las alertas no están disponibles en este momento."; }
  else fallos++;

  if (fallos === 0) {
    lastOk = new Date();
    $("updated").textContent = `${fmtHora.format(lastOk)} (hora de Colombia)`;
    toast(null);
  } else {
    if (!lastOk) setStatus("error", "Sin conexión con la API");
    toast(`No se pudo actualizar${lastOk ? ` desde las ${fmtHora.format(lastOk)}` : ""}. Se muestran los últimos datos recibidos; se reintentará en 10 s.`);
  }
}

async function refreshHistory() {
  if (deviceMissing) return;
  const box = $("chart");
  box.classList.add("is-refreshing");
  try {
    renderChart(await getJSON(`/devices/${dev}/air-quality/history?hours=24&bucket_min=5`));
  } catch {
    /* se conserva la grafica anterior; el aviso lo da refresh() */
  } finally {
    box.classList.remove("is-refreshing");
  }
}

// ---------- Replay historico ----------
// Solo datos externos del IDEAM (datos.gov.co y SISAIRE) via /predictions/replay*: no usa las
// mediciones del sensor propio, para que funcione con el hardware desconectado. Siempre pide
// buscar=siguiente: si a la hora elegida falta parte de la ventana, la API salta a la siguiente
// hora valida y la tarjeta lo avisa.
let replayTimer = null, replayVelocidad = VELOCIDADES[0].ms, metricasClave = "";
let replaySerie = null, replayGeom = null;

// ---- Grafica real frente a predicho (/predictions/replay/serie), SVG propio ----
async function cargarSerieReplay(at) {
  const st = $("replay-estacion").value;
  if (!st || !at) return;
  const url = `/predictions/replay/serie?station=${encodeURIComponent(st)}&at=${encodeURIComponent(at)}` +
              `&horas=${$("replay-rango").value}&horizonte=${$("replay-horizonte").value}`;
  try {
    renderSerieReplay(await getJSON(url));
  } catch (e) {
    replaySerie = null;
    $("replay-svg").replaceChildren();
    $("replay-gmet").textContent = mensajeError(e.status, e.message);
  }
}

function renderSerieReplay(r) {
  replaySerie = r;
  const svg = $("replay-svg"), box = $("replay-chart");
  const W = svg.clientWidth || box.clientWidth || 600, H = svg.clientHeight || 240;
  const M = { l: 36, r: 10, t: 18, b: 34 };
  const iw = Math.max(10, W - M.l - M.r), ih = Math.max(10, H - M.t - M.b);
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.replaceChildren();
  const pts = r.puntos || [];
  const n = pts.length;
  const ts = pts.map((p) => msLocal(p.target_local));
  const t0 = ts[0], t1 = ts[n - 1] ?? t0 + 3_600_000;
  const valores = (k) => pts.map((p) => (p[k] == null ? null : Number(p[k])));
  const real = valores("real"), pred = valores("predicho"), pers = valores("persistencia");
  const yMax = escalaY(Math.max(1, ...[...real, ...pred, ...pers].filter((v) => v != null)));
  const xT = (t) => M.l + (t1 > t0 ? ((t - t0) / (t1 - t0)) * iw : iw / 2);
  const x = (i) => xT(ts[i]);
  const y = (v) => M.t + ih - (v / yMax) * ih;
  replayGeom = { x, n, M, iw };

  for (let i = 0; i <= 4; i++) {
    const v = (yMax / 4) * i, yy = y(v);
    svg.append(svgEl("line", { x1: M.l, x2: M.l + iw, y1: yy, y2: yy, class: i === 0 ? "c-axis" : "c-grid" }));
    const t = svgEl("text", { x: M.l - 6, y: yy + 4, "text-anchor": "end", class: "c-tick" });
    t.textContent = String(Math.round(v));
    svg.append(t);
  }
  const u = svgEl("text", { x: M.l - 30, y: 10, class: "r-unidad" });
  u.textContent = "µg/m³";
  svg.append(u);
  // Varios dias: una marca por dia a medianoche de Colombia con la fecha corta (dd/m); si no, horas.
  const variosDias = t1 - t0 > 30 * 3_600_000;
  const paso = variosDias ? 86_400_000 : pasoEjeX(t1 - t0, iw);
  for (let t = Math.ceil((t0 + COL_OFFSET_MS) / paso) * paso - COL_OFFSET_MS; t <= t1; t += paso) {
    const xx = xT(t);
    if (xx < M.l + 14 || xx > M.l + iw - 14) continue;
    const tx = svgEl("text", { x: xx, y: M.t + ih + 16, "text-anchor": "middle", class: "c-tick" });
    const loc = new Date(t + COL_OFFSET_MS);
    tx.textContent = variosDias ? `${loc.getUTCDate()}/${loc.getUTCMonth() + 1}` : fmtHora.format(new Date(t));
    svg.append(tx);
  }
  const ex = svgEl("text", { x: M.l + iw, y: H - 2, "text-anchor": "end", class: "r-unidad" });
  ex.textContent = "hora objetivo (Colombia)";
  svg.append(ex);

  // Marca de la hora del replay (la emision actual)
  const tAt = msLocal(r.at_local);
  if (tAt >= t0 && tAt <= t1) {
    const xa = xT(tAt);
    svg.append(svgEl("line", { x1: xa, x2: xa, y1: M.t - 6, y2: M.t + ih, class: "r-ahora" }));
    const izq = xa > M.l + iw - 80;          // cerca del borde derecho: la etiqueta va a la izquierda
    const ta = svgEl("text", { x: izq ? xa - 4 : xa + 4, y: M.t - 6, "text-anchor": izq ? "end" : "start", class: "r-ahora-txt" });
    ta.textContent = `ahora ${String(r.at_local).slice(11)}`;
    svg.append(ta);
  }
  // Series: persistencia (fondo), predicho, real (encima). Huecos sin conectar; puntos aislados como marca.
  for (const [vals, cls, dot] of [[pers, "r-pers", "r-dot-pers"], [pred, "r-pred", "r-dot-pred"], [real, "r-real", "r-dot-real"]]) {
    const { d, aislados } = pathTramos(vals, x, y);
    if (d) svg.append(svgEl("path", { d, class: cls }));
    for (const i of aislados) svg.append(svgEl("circle", { cx: x(i), cy: y(vals[i]), r: 2.5, class: dot }));
  }
  if (!real.some((v) => v != null) && !pred.some((v) => v != null)) {
    const t = svgEl("text", { x: M.l + iw / 2, y: M.t + ih / 2, "text-anchor": "middle", class: "c-empty" });
    t.textContent = "Sin datos en este rango";
    svg.append(t);
  }
  $("replay-gmet").textContent = textoMetricasSerie(r);
  $("replay-tip").hidden = true;
}

function tipSerieReplay(ev) {
  if (!replaySerie || !replayGeom || !replayGeom.n) return;
  const box = $("replay-chart"), rect = box.getBoundingClientRect();
  const svg = $("replay-svg"), escala = (svg.viewBox.baseVal?.width || rect.width) / rect.width;
  const px = (ev.clientX - rect.left) * escala;
  let mejor = 0;
  for (let i = 1; i < replayGeom.n; i++) if (Math.abs(replayGeom.x(i) - px) < Math.abs(replayGeom.x(mejor) - px)) mejor = i;
  const t = filasTooltip(replaySerie.puntos[mejor]);
  const tip = $("replay-tip");
  tip.replaceChildren(el("div", { class: "tip__time" }, t.titulo),
    ...t.filas.map(([k, v]) => el("div", { class: "tip__row" }, el("span", {}, k), el("strong", {}, v))),
    el("div", { class: "tip__time" }, t.emision));
  tip.hidden = false;
  const xPx = replayGeom.x(mejor) / escala;
  tip.style.left = `${Math.max(0, Math.min(xPx + 12, rect.width - 190))}px`;
  svg.querySelector(".r-guia")?.remove();
  svg.append(svgEl("line", { x1: replayGeom.x(mejor), x2: replayGeom.x(mejor), y1: 12, y2: (svg.viewBox.baseVal?.height || 240) - 30, class: "r-guia" }));
}


async function cargarReplay() {
  const st = $("replay-estacion").value, at = $("replay-at").value;
  if (!st || !at) return false;
  $("replay-msg").textContent = "Calculando…";
  let r;
  try {
    r = await getJSON(`/predictions/replay?station=${encodeURIComponent(st)}&at=${encodeURIComponent(at)}&buscar=siguiente`);
  } catch (e) {
    $("replay-filas").replaceChildren();
    $("replay-ventana").textContent = "";
    $("replay-msg").textContent = mensajeError(e.status, e.message);
    return false;
  }
  if (r.at_local) $("replay-at").value = r.at_local;
  const v = vistaReplay(r);
  $("replay-filas").replaceChildren(...v.filas.map((f) => el("tr", {},
    el("td", {}, f.horizonte), el("td", {}, f.hora), el("td", {}, f.predicho), el("td", {}, f.real),
    el("td", {}, f.persistencia), el("td", {}, f.error))));
  $("replay-msg").textContent = v.mensaje;
  $("replay-ventana").textContent = v.ventana;
  $("replay-fuentes").textContent = v.fuentes;
  $("replay-salto").textContent = avisoSalto(r);
  cargarSerieReplay(r.at_local || at);
  if (MODO_REPLAY) {
    renderReplayPanel(r);
    history.replaceState(null, "", urlModoReplay(location.href, st, r.at_local));
  }
  const per = periodoMetricas(r.at_local || at);
  const clave = per ? `${st}|${per.desde}|${per.hasta}` : "";
  if (per && clave !== metricasClave) {        // el MAE del periodo solo se pide al cambiar de dia o estacion
    metricasClave = clave;
    try {
      $("replay-mae").textContent = vistaMetricas(await getJSON(
        `/predictions/replay/metrics?station=${encodeURIComponent(st)}&desde=${per.desde}&hasta=${per.hasta}`));
    } catch {
      metricasClave = "";
      $("replay-mae").textContent = vistaMetricas(null);
    }
  }
  return true;
}

// Panel principal en modo replay: ICA solo con PM2.5; PM10, temperatura y humedad no existen en el
// historico importado (solo PM2.5): se dice, no se inventan.
function renderReplayPanel(r) {
  const a = vistaActualReplay(r);
  if (!a) return;
  const cat = CATS.find((c) => c.code === a.codigo) || CATS[0];
  document.querySelector(".card--ica").classList.remove("is-nodata");
  document.documentElement.style.setProperty("--cat", cat.color);
  setNumber($("ica-value"), a.ica, 0);
  setGauge(a.ica);
  $("ica-cat").textContent = a.categoria;
  $("gauge-label").textContent = `Medidor del ICA (solo PM2.5): ${a.ica}, categoría ${a.categoria}`;
  $("ica-by").textContent = a.icaBy;
  document.querySelector(".ica__base").textContent = a.base;
  for (const li of $("ica-scale").children) {
    if (li.dataset.code === cat.code) li.setAttribute("aria-current", "true"); else li.removeAttribute("aria-current");
  }
  setActiveBand(cat.code);
  renderRec(a.recomendacion, a.categoria);

  setNumber($("pm25-now"), a.pm25, 1);
  setNumber($("pm25-1h"), a.pm25, 1);
  $("pm25-lbl-l").textContent = "hora del histórico · media 1 h ";
  $("pm25-lbl-s").textContent = "media 1 h ";
  renderLimit("pm25", a.media24, { parcial: false, horas_con_datos: a.horas24 });

  for (const id of ["pm10-now", "pm10-1h", "temp", "hum"]) { $(id).textContent = "—"; $(id).dataset.v = ""; }
  $("pm10-lbl-l").textContent = `${a.noDisponible} · `;
  $("pm10-lbl-s").textContent = `${a.noDisponible} · `;
  renderLimit("pm10", null, null);
  $("pm10-note").textContent = `PM10 ${a.noDisponible}`;
  setRange("temp", null);
  setRange("hum", null);
  $("temp-state").textContent = a.noDisponible;
  $("hum-state").textContent = a.noDisponible;
  $("env-time").textContent = "Solo se importó PM2.5 de las estaciones del IDEAM.";

  $("now-pm25").textContent = r.actual.pm25_1h == null ? "—" : Number(r.actual.pm25_1h).toLocaleString("es-CO", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
  for (const id of ["now-pm10", "now-temp", "now-hum"]) $(id).textContent = "—";
  $("now-ultima").textContent = `Hora del histórico: ${String(r.at_local).replace("T", " ")} (hora de Colombia) · PM10, temperatura y humedad: ${a.noDisponible}`;
  $("now-aviso").hidden = true;
  setStatus("replay", a.estado);
  $("updated").textContent = `replay ${String(r.at_local).replace("T", " ")} (hora de Colombia)`;
}

function detenerReplay(msg) {
  clearTimeout(replayTimer);
  replayTimer = null;
  $("replay-play").textContent = "▶ Play";
  $("replay-play").setAttribute("aria-pressed", "false");
  if (msg) $("replay-salto").textContent = msg;
}

async function pasoReplay() {
  const sig = siguienteHora($("replay-at").value);
  if (!sig) { detenerReplay(); return; }
  $("replay-at").value = sig;
  if (!(await cargarReplay())) { detenerReplay("Fin de los datos disponibles: el replay quedó en pausa."); return; }
  if (replayTimer !== null) replayTimer = setTimeout(pasoReplay, replayVelocidad);
}

function alternarPlay() {
  if (replayTimer !== null) { detenerReplay(); return; }
  $("replay-play").textContent = "⏸ Pausa";
  $("replay-play").setAttribute("aria-pressed", "true");
  replayTimer = setTimeout(pasoReplay, replayVelocidad);
}

async function iniciarReplay() {
  $("replay-rango").replaceChildren(...RANGOS.map((o) => el("option", { value: String(o.horas) }, o.label)));
  $("replay-horizonte").replaceChildren(...HORIZONTES_GRAFICA.map((h) => el("option", { value: String(h) }, `${h} h`)));
  for (const id of ["replay-rango", "replay-horizonte"]) {
    $(id).addEventListener("change", () => cargarSerieReplay($("replay-at").value));
  }
  const caja = $("replay-chart");
  caja.addEventListener("pointermove", tipSerieReplay);
  caja.addEventListener("pointerdown", tipSerieReplay);
  caja.addEventListener("pointerleave", () => { $("replay-tip").hidden = true; $("replay-svg").querySelector(".r-guia")?.remove(); });
  let rr;
  new ResizeObserver(() => { clearTimeout(rr); rr = setTimeout(() => replaySerie && renderSerieReplay(replaySerie), 120); }).observe(caja);
  $("replay-velocidad").replaceChildren(...VELOCIDADES.map((v) => el("option", { value: String(v.ms) }, v.label)));
  $("replay-velocidad").addEventListener("change", () => { replayVelocidad = Number($("replay-velocidad").value); });
  $("replay-play").addEventListener("click", alternarPlay);
  $("replay-form").addEventListener("submit", (ev) => { ev.preventDefault(); detenerReplay(); cargarReplay(); });
  $("replay-estacion").addEventListener("change", () => {
    detenerReplay();
    const o = $("replay-estacion").selectedOptions[0];
    if (o && o.dataset.at) $("replay-at").value = o.dataset.at;
    cargarReplay();
  });
  try {
    const v = vistaEstaciones(await getJSON("/predictions/replay/estaciones"));
    $("replay-estacion").replaceChildren(...v.opciones.map((o) => el("option", { value: o.value, "data-at": o.at }, o.label)));
    const pedida = MODO_REPLAY && v.opciones.find((o) => o.value === PARAMS.get("station"));
    $("replay-estacion").value = pedida ? pedida.value : v.station;
    $("replay-at").value = (MODO_REPLAY && PARAMS.get("at")) || (pedida ? pedida.at : v.at);
    $("replay-msg").textContent = v.mensaje;
    await cargarReplay();
  } catch (e) {
    $("replay-msg").textContent = mensajeError(e.status, e.message);
  }
}

// Modo replay: nada en vivo (ni mediciones, ni grafica, ni alertas, ni suscripcion -> ningun correo).
function iniciarModoReplay() {
  document.body.classList.add("modo-replay");
  $("replay-banner").hidden = false;
  $("sim-banner").hidden = true;
  $("sim-badge").hidden = true;
  document.title = "REPLAY HISTÓRICO · EcoAir AI";
  setStatus("replay", "Replay histórico");
  $("alerts").replaceChildren();
  $("alerts-count").textContent = "modo replay";
  $("alerts-empty").hidden = false;
  $("alerts-empty").textContent = "En modo replay histórico no se evalúan alertas ni se envían correos.";
  $("chart-note").hidden = false;
  $("chart-note").textContent = "La gráfica de 24 h usa las mediciones del sensor propio: no se muestra en modo replay.";
  $("subs-form").hidden = true;
  $("subs-form").after(el("p", { class: "empty" }, "En modo replay histórico no hay suscripciones ni se envían correos."));
  iniciarReplay();
}

// ---------- Suscripcion por correo ----------
async function loadConsent() {
  const span = $("subs-consent"), btn = $("subs-btn");
  try {
    const c = await getJSON("/subscriptions/consent");
    span.textContent = c.texto; // incluye "contacto no configurado" si falta CONTACT_EMAIL
    btn.disabled = false;
  } catch {
    span.textContent = "No se pudo cargar el texto de autorización. Inténtalo más tarde.";
    btn.disabled = true;
  }
}

function initSubscriptionForm() {
  const form = $("subs-form"), msg = $("subs-msg"), btn = $("subs-btn");
  const decir = (texto, kind) => { msg.textContent = texto; msg.dataset.kind = kind || ""; };
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const email = $("subs-email");
    if (!email.value.trim() || !email.checkValidity()) { decir("Escribe un correo válido.", "error"); email.focus(); return; }
    if (!$("subs-ok").checked) { decir("Debes aceptar la autorización para suscribirte.", "error"); $("subs-ok").focus(); return; }
    btn.disabled = true; decir("Enviando…");
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
    try {
      const r = await fetch("/subscriptions", {
        method: "POST", signal: ctrl.signal,
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ email: email.value.trim(), device_id: DEVICE,
                               accepted_terms: true, min_severity: $("subs-sev").value }),
      });
      const data = await r.json().catch(() => ({}));
      if (r.status === 202) { decir(data.mensaje, "ok"); form.reset(); }
      else if (typeof data.detail === "string") decir(data.detail, "error");
      else decir("Revisa los datos del formulario.", "error");
    } catch {
      decir("No se pudo enviar. Revisa la conexión e inténtalo de nuevo.", "error");
    } finally {
      clearTimeout(t); btn.disabled = false;
    }
  });
}

// Aviso de SIMULACION: se activa si el dispositivo o sus datos son simulados. Nunca se presenta
// una lectura simulada como si fuera del sensor real.
let simDispositivo = false, simDatos = false;
function setSimulacion() {
  if (MODO_REPLAY) return;                 // el modo replay tiene su propio banner
  const sim = simDispositivo || simDatos;
  $("sim-badge").hidden = !sim;
  $("sim-banner").hidden = !sim;
  document.title = `${sim ? "SIMULACIÓN · " : ""}EcoAir AI · Calidad del aire`;
}

// Selector de estacion: cambia ?device= y recarga la pagina.
async function loadDevices() {
  const sel = $("device-select");
  $("device-name").textContent = DEVICE;
  $("device-name").value = DEVICE;
  try {
    const lista = await getJSON("/devices");
    const opciones = lista.map((d) => {
      const nombre = `${d.name ? `${d.name} (${d.device_id})` : d.device_id}${d.is_simulated ? " · SIMULACIÓN" : ""}`;
      const o = el("option", { value: d.device_id }, nombre);
      if (d.device_id === DEVICE) { o.selected = true; simDispositivo = !!d.is_simulated; }
      return o;
    });
    if (!lista.some((d) => d.device_id === DEVICE)) opciones.unshift(el("option", { value: DEVICE, selected: "" }, DEVICE));
    opciones.push(el("option", { value: OPCION_REPLAY }, "Replay histórico (IDEAM)"));
    if (MODO_REPLAY) for (const o of opciones) o.selected = o.value === OPCION_REPLAY;
    sel.replaceChildren(...opciones);
    setSimulacion();
  } catch { /* se queda el identificador */ }
  sel.addEventListener("change", () => {
    location.assign(sel.value === OPCION_REPLAY ? urlModoReplay(location.href) : urlModoVivo(location.href, sel.value));
  });
}

function start() {
  initGauge();
  initRanges();
  initScaleToggle();
  loadDevices();
  if (MODO_REPLAY) { iniciarModoReplay(); return; }
  initSubscriptionForm();
  loadConsent();
  // /panel#suscripcion: al cargar los datos cambia la altura de las tarjetas; se vuelve a llevar
  // la vista al ancla cuando termina la primera actualizacion.
  refresh().finally(() => { if (location.hash) document.querySelector(location.hash)?.scrollIntoView(); });
  refreshHistory();
  iniciarReplay();
  setInterval(refresh, REFRESH_MS);
  setInterval(refreshHistory, HISTORY_REFRESH_MS);
  let rt;
  new ResizeObserver(() => { clearTimeout(rt); rt = setTimeout(() => lastHistory && renderChart(lastHistory), 120); }).observe($("chart"));
  document.addEventListener("visibilitychange", () => { if (!document.hidden) { refresh(); refreshHistory(); } });
}

start();
