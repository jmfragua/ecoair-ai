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
