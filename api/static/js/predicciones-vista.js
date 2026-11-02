// Logica PURA de la tarjeta "Prediccion experimental" (sin DOM), con /devices/{id}/predictions/latest.
// Las predicciones las emite el alerter cada hora (modelo v4). La API ya devuelve solo los horizontes
// visibles (v4: 4 y 5 h); aqui se vuelve a filtrar por "visible" por si acaso.
// Nunca se queda en "Cargando…": sin respuesta valida se dice "no disponible por ahora".

export const AVISO_FIJO =
  "Estimación experimental, entrenada con estaciones de referencia del IDEAM. No es un equipo certificado.";
export const NO_DISPONIBLE_POR_AHORA = "Predicción no disponible por ahora";

const fmtHora = new Intl.DateTimeFormat("es-CO", { timeZone: "America/Bogota", hour: "2-digit", minute: "2-digit", hour12: false });

function numero(v) {
  return typeof v === "number" && Number.isFinite(v)
    ? v.toLocaleString("es-CO", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) : "—";
}

function hora(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : fmtHora.format(d);
}

// r: respuesta de la API o null (fallo). -> {items, mensaje, emision, aviso, fuentes, avisoFijo}
export function vistaPredicciones(r) {
  const vacia = { items: [], mensaje: NO_DISPONIBLE_POR_AHORA, emision: "", aviso: "", fuentes: "", avisoFijo: AVISO_FIJO };
  if (!r || typeof r !== "object" || typeof r.estado !== "string") return vacia;
  const comunes = { aviso: r.aviso || "", fuentes: r.fuentes || "", avisoFijo: AVISO_FIJO };
  if (r.estado === "sin_predicciones") {
    return { ...comunes, items: [], emision: "", mensaje: r.mensaje || "Todavía no hay predicciones para esta estación." };
  }
  const preds = (Array.isArray(r.predicciones) ? r.predicciones : []).filter((p) => p && p.visible === true);
  const items = preds.map((p) => ({
    etiqueta: `En ${p.horizon_hours} h`,
    hasta: `hacia las ${hora(p.target_time)}`,
    valor: numero(p.predicted_pm25),
  }));
  let emision = r.created_at ? `Emitida a las ${hora(r.created_at)} (hora de Colombia)` : "";
  if (r.estado === "desactualizada" && typeof r.minutos_desde_emision === "number") {
    emision += ` · hace ${Math.floor(r.minutos_desde_emision / 60)} h: puede estar desactualizada`;
  }
  return { ...comunes, items, emision,
           mensaje: items.length ? "" : (r.mensaje || "Ningún horizonte de predicción está visible por ahora.") };
}

// ---------- Tarjeta "Prediccion de PM2.5 · SIMULACION" (solo ecoair-sim-NN) ----------
// Predicciones del alerter sobre datos INVENTADOS (is_simulated=true). El aviso es el que devuelve la API
// ("SIMULACION · ..."), sin metricas de precision. Si la respuesta no viene marcada como simulada, no se
// muestra ningun valor: esta tarjeta nunca presenta una prediccion como si fuera de datos reales.
export const TITULO_SIMULADA = "Predicción de PM2.5 · SIMULACIÓN";
export const SIN_PREDICCIONES_SIMULADAS = "Aún no hay predicciones simuladas (se generan cada hora)";
export const HORIZONTES_SIMULADA = [4, 5];

// r: respuesta de /predictions/latest o null. -> {titulo, items, mensaje, emision, aviso}
export function vistaPrediccionSimulada(r) {
  const base = { titulo: TITULO_SIMULADA, items: [], mensaje: NO_DISPONIBLE_POR_AHORA, emision: "", aviso: "" };
  if (!r || typeof r !== "object" || typeof r.estado !== "string") return base;
  if (r.estado === "sin_predicciones") return { ...base, mensaje: SIN_PREDICCIONES_SIMULADAS };
  if (r.is_simulated !== true) return base;
  const v = vistaPredicciones(r);
  const items = (Array.isArray(r.predicciones) ? r.predicciones : [])
    .filter((p) => p && p.visible === true && HORIZONTES_SIMULADA.includes(p.horizon_hours))
    .map((p) => ({ etiqueta: `En ${p.horizon_hours} h`, hasta: `hacia las ${hora(p.target_time)}`, valor: numero(p.predicted_pm25) }));
  return { ...base, items, emision: v.emision, aviso: typeof r.simulacion === "string" ? r.simulacion : "",
           mensaje: items.length ? "" : SIN_PREDICCIONES_SIMULADAS };
}

// ---------- Lineas dentro de la tarjeta grande de PM2.5 (solo ecoair-sim-NN) ----------
// Solo PM2.5: el modelo no predice PM10. El valor grande de la tarjeta sigue siendo el medido. Sin
// predicciones simuladas validas (o si la API fallo) -> [] y la tarjeta no muestra nada.
export const ETIQUETA_PM25_SIMULADA = "predicción · simulado";

export function lineasPm25Simulada(r) {
  return vistaPrediccionSimulada(r).items.map((i) => `${i.etiqueta}: ${i.valor} µg/m³ · ${i.hasta}`);
}
