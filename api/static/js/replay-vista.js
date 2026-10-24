// Logica PURA de la tarjeta "Replay historico" (sin DOM). Usa /predictions/replay/estaciones,
// /predictions/replay y /predictions/replay/metrics: SOLO datos externos del IDEAM (datos.gov.co y
// SISAIRE), nunca las mediciones del sensor propio. Nunca se queda en "Cargando…".

export const AVISO_REPLAY =
  "Replay histórico con datos de estaciones de referencia del IDEAM. No es un pronóstico en tiempo real ni un equipo certificado.";
export const ESTACION_PREFERIDA = "29509";
export const DIAS_METRICAS = 30;

const num = (v, d = 1) => (typeof v === "number" && Number.isFinite(v)
  ? v.toLocaleString("es-CO", { minimumFractionDigits: d, maximumFractionDigits: d }) : "—");
const conSigno = (v) => (typeof v === "number" && Number.isFinite(v) ? (v > 0 ? "+" : "") + num(v, 1) : "—");
const local = (iso) => (typeof iso === "string" ? iso.replace("T", " ") : "—");

// -> {opciones: [{value, label, at}], station, at, mensaje}
export function vistaEstaciones(r) {
  const lista = r && Array.isArray(r.estaciones) ? r.estaciones.filter((e) => e && e.at_por_defecto) : [];
  if (!lista.length) {
    return { opciones: [], station: "", at: "", mensaje: "No hay datos históricos disponibles para el replay." };
  }
  const opciones = lista.map((e) => ({ value: e.station, at: e.at_por_defecto,
    label: `${e.nombre} · ${local(e.desde_local).slice(0, 10)} a ${local(e.hasta_local).slice(0, 10)}` }));
  const elegida = opciones.find((o) => o.value === ESTACION_PREFERIDA) || opciones[0];
  return { opciones, station: elegida.value, at: elegida.at, mensaje: "" };
}

// -> {filas, ventana, fuentes, mensaje}
export function vistaReplay(r) {
  if (!r || typeof r !== "object" || !Array.isArray(r.predicciones)) {
    return { filas: [], ventana: "", fuentes: "", mensaje: "Replay no disponible por ahora." };
  }
  const filas = r.predicciones.map((p) => ({
    horizonte: `${p.horizon_hours} h`,
    hora: typeof p.target_local === "string" ? p.target_local.slice(5).replace("T", " ") : "—",
    predicho: num(p.predicted_pm25),
    real: p.observed_pm25 == null ? "sin dato" : num(p.observed_pm25),
    persistencia: num(p.persistence_pm25),
    error: conSigno(p.error_modelo),
  }));
  const v = r.ventana || {};
  return {
    filas,
    ventana: `Emisión ${local(r.at_local)} (hora de Colombia) · última hora ${num((v.h1_a_h6 || [])[0])} µg/m³ · ` +
             `media 24 h ${num(v.media_24h)} µg/m³ (${v.horas_24h ?? "—"} de 24 h)`,
    fuentes: r.fuentes && r.fuentes.length ? `Datos: ${r.fuentes.join(" y ")}.` : "",
    mensaje: filas.length ? "" : "El modelo no tiene horizontes visibles.",
  };
}

// Error de la API -> texto (422 trae el motivo, p. ej. horas que faltan en la ventana).
export function mensajeError(status, detalle) {
  if (status === 422 && detalle) return `No se puede calcular a esa hora: ${detalle}`;
  if (status === 404) return "Esa estación no está disponible para el replay.";
  if (status === 503) return "El replay no está disponible en este momento (base de datos o modelo).";
  return "No se pudo consultar el replay.";
}

// Periodo de las metricas: los 30 dias que terminan en el dia elegido.
export function periodoMetricas(atLocal) {
  const d = new Date(`${String(atLocal).slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(d.getTime())) return null;
  const desde = new Date(d.getTime() - (DIAS_METRICAS - 1) * 86_400_000);
  return { desde: desde.toISOString().slice(0, 10), hasta: d.toISOString().slice(0, 10) };
}

export function vistaMetricas(m) {
  if (!m || !Array.isArray(m.por_horizonte)) return "MAE del periodo no disponible por ahora.";
  const partes = m.por_horizonte.filter((h) => h.n > 0).map((h) =>
    `${h.horizon_hours} h: modelo ${num(h.mae_modelo, 2)} frente a persistencia ${num(h.mae_persistencia, 2)} µg/m³ (n = ${h.n})`);
  const base = `MAE del ${m.desde} al ${m.hasta}: ` + (partes.length ? partes.join(" · ") : "sin horas válidas");
  return m.estado === "ok" ? base : `${base}. ${m.mensaje || ""}`.trim();
}

// ---------- Modo replay del panel principal ----------
export const BANNER_REPLAY =
  "REPLAY HISTÓRICO · datos reales del IDEAM de una fecha pasada. No es en vivo ni un equipo certificado.";
export const NO_DISPONIBLE = "no disponible en el histórico";
export const OPCION_REPLAY = "__replay__";
export const VELOCIDADES = [
  { ms: 3000, label: "1 h cada 3 s" },
  { ms: 1000, label: "1 h cada 1 s" },
  { ms: 6000, label: "1 h cada 6 s" },
];

// Bloque "actual" de /predictions/replay -> lo que muestra el panel principal (solo PM2.5).
export function vistaActualReplay(r) {
  const a = r && r.actual;
  if (!a || !a.ica || typeof a.ica.ica !== "number") return null;
  return {
    ica: a.ica.ica, codigo: a.ica.category_code, categoria: a.ica.category_name,
    icaBy: "ICA con PM2.5 solamente",
    base: "Media de la hora de PM2.5 (dato histórico del IDEAM) · Res. 2254/2017",
    pm25: a.pm25_1h, media24: a.media_24h, horas24: a.horas_24h,
    recomendacion: a.recomendacion || null,
    estado: `Replay histórico · ${local(r.at_local)}`,
    noDisponible: NO_DISPONIBLE,
  };
}

// "2026-09-30T12:00" + 1 h (hora de Colombia, sin cambio de horario) -> "2026-09-30T13:00".
export function siguienteHora(atLocal) {
  const t = String(atLocal).slice(0, 16);
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(t)) return null;
  const d = new Date(`${t}:00Z`);
  if (Number.isNaN(d.getTime())) return null;
  return new Date(d.getTime() + 3_600_000).toISOString().slice(0, 16);
}

export function avisoSalto(r) {
  const s = r && r.salto;
  if (!s) return "";
  return `Faltaban horas en la ventana de ${local(s.pedida_local)}: se saltó ${s.horas} h hasta ${local(r.at_local)}.`;
}

export function urlModoReplay(href, station, at) {
  const u = new URL(href);
  u.searchParams.set("modo", "replay");
  u.searchParams.delete("device");
  if (station) u.searchParams.set("station", station);
  if (at) u.searchParams.set("at", at);
  return u.toString();
}

export function urlModoVivo(href, device) {
  const u = new URL(href);
  for (const k of ["modo", "station", "at"]) u.searchParams.delete(k);
  u.searchParams.set("device", device);
  return u.toString();
}

// ---------- Grafica real frente a predicho (/predictions/replay/serie) ----------
export const RANGOS = [{ horas: 24, label: "24 h" }, { horas: 72, label: "72 h" }, { horas: 168, label: "7 días" }];
export const HORIZONTES_GRAFICA = [4, 5];

// Tramos de indices consecutivos con valor (null corta el tramo: los huecos no se conectan).
export function tramos(valores) {
  const out = [];
  let ini = null;
  valores.forEach((v, i) => {
    const hay = typeof v === "number" && Number.isFinite(v);
    if (hay && ini === null) ini = i;
    if (!hay && ini !== null) { out.push([ini, i - 1]); ini = null; }
  });
  if (ini !== null) out.push([ini, valores.length - 1]);
  return out;
}

// Path SVG por tramos + indices aislados (tramos de 1 punto, que se dibujan como marca).
export function pathTramos(valores, x, y) {
  let d = "";
  const aislados = [];
  for (const [a, b] of tramos(valores)) {
    if (a === b) { aislados.push(a); continue; }
    for (let i = a; i <= b; i++) d += `${i === a ? "M" : "L"}${x(i).toFixed(1)},${y(valores[i]).toFixed(1)}`;
  }
  return { d, aislados };
}

// "2026-09-30T16:00" (hora de Colombia) -> milisegundos UTC
export function msLocal(t) {
  return new Date(`${String(t).slice(0, 16)}:00-05:00`).getTime();
}

export function textoMetricasSerie(r) {
  const m = r && r.metricas;
  if (!m) return "";
  if (!m.n) return `Sin horas con valor real y predicho en el rango (n = 0).`;
  const rango = (RANGOS.find((x) => x.horas === r.horas) || {}).label || `${r.horas} h`;
  return `Últimas ${rango}, a ${r.horizonte} h: MAE modelo ${num(m.mae_modelo, 2)} (RMSE ${num(m.rmse_modelo, 2)}) frente a ` +
         `persistencia ${num(m.mae_persistencia, 2)} (RMSE ${num(m.rmse_persistencia, 2)}) µg/m³, con n = ${m.n} horas.`;
}

export function filasTooltip(p) {
  const v = (x) => (x == null ? "sin dato" : `${num(x)} µg/m³`);
  return {
    titulo: `${local(p.target_local)} (hora objetivo, Colombia)`,
    filas: [["Real", v(p.real)], ["Predicho", v(p.predicho)], ["Persistencia", v(p.persistencia)]],
    emision: `emitida ${local(p.emision_local)}`,
  };
}
