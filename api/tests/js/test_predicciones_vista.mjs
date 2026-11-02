// Tests de la tarjeta "Prediccion experimental" con /predictions/latest (logica pura).
import { test } from "node:test";
import assert from "node:assert/strict";
import { vistaPredicciones, AVISO_FIJO, NO_DISPONIBLE_POR_AHORA } from "../../static/js/predicciones-vista.js";

const OK = {
  estado: "ok", created_at: "2026-10-20T15:00:00+00:00", minutos_desde_emision: 30,
  aviso: "Entrenado con datos externos…", fuentes: "Modelo entrenado con datos externos (IDEAM…). Experimental.",
  predicciones: [
    { horizon_hours: 3, target_time: "2026-10-20T18:00:00+00:00", predicted_pm25: 11.2, visible: false },
    { horizon_hours: 4, target_time: "2026-10-20T19:00:00+00:00", predicted_pm25: 12.04, visible: true },
    { horizon_hours: 5, target_time: "2026-10-20T20:00:00+00:00", predicted_pm25: 12.5, visible: true },
  ],
};

test("solo horizontes visibles (4 y 5 h) con hora de Colombia y aviso fijo", () => {
  const v = vistaPredicciones(OK);
  assert.deepEqual(v.items.map((i) => i.etiqueta), ["En 4 h", "En 5 h"]);
  assert.deepEqual(v.items.map((i) => i.valor), ["12,0", "12,5"]);
  assert.equal(v.items[0].hasta, "hacia las 14:00");          // 19:00 UTC = 14:00 Colombia
  assert.equal(v.emision, "Emitida a las 10:00 (hora de Colombia)");
  assert.equal(v.avisoFijo, AVISO_FIJO);
  assert.match(AVISO_FIJO, /experimental/i);
  assert.match(AVISO_FIJO, /estaciones de referencia/);
  assert.match(AVISO_FIJO, /no es un equipo certificado/i);
  assert.equal(v.mensaje, "");
});

test("fallo de red o respuesta invalida: no disponible, nunca 'Cargando…'", () => {
  for (const r of [null, undefined, {}, "x", { estado: 3 }]) {
    const v = vistaPredicciones(r);
    assert.equal(v.mensaje, NO_DISPONIBLE_POR_AHORA);
    assert.deepEqual(v.items, []);
    assert.ok(!/Cargando/.test(v.mensaje));
    assert.equal(v.avisoFijo, AVISO_FIJO);
  }
});

test("sin predicciones, desactualizada y ninguna visible", () => {
  assert.match(vistaPredicciones({ estado: "sin_predicciones", predicciones: [] }).mensaje, /Todavía no hay/);
  const vieja = vistaPredicciones({ ...OK, estado: "desactualizada", minutos_desde_emision: 190 });
  assert.match(vieja.emision, /hace 3 h: puede estar desactualizada/);
  const ninguna = vistaPredicciones({ ...OK, predicciones: [OK.predicciones[0]] });
  assert.deepEqual(ninguna.items, []);
  assert.match(ninguna.mensaje, /Ningún horizonte/);
});

// ---------- Tarjeta "Prediccion de PM2.5 · SIMULACION" (solo ecoair-sim-NN) ----------
import { vistaPrediccionSimulada, TITULO_SIMULADA, SIN_PREDICCIONES_SIMULADAS } from "../../static/js/predicciones-vista.js";
import { secciones, modos } from "../../static/js/modo-vista.js";

const AVISO_API = "SIMULACIÓN · predicción calculada sobre datos inventados para demostración; sin métricas de precisión ni comparación con mediciones reales.";
const SIM = { ...OK, is_simulated: true, simulacion: AVISO_API, regla_en_vivo: null };

test("simulada: 4 y 5 h con el aviso de la API, sin metricas", () => {
  const v = vistaPrediccionSimulada(SIM);
  assert.equal(v.titulo, "Predicción de PM2.5 · SIMULACIÓN");
  assert.equal(TITULO_SIMULADA, v.titulo);
  assert.deepEqual(v.items.map((i) => [i.etiqueta, i.valor, i.hasta]), [["En 4 h", "12,0", "hacia las 14:00"], ["En 5 h", "12,5", "hacia las 15:00"]]);
  assert.equal(v.aviso, AVISO_API);                       // el mismo texto que devuelve la API
  assert.equal(v.mensaje, "");
  assert.doesNotMatch(JSON.stringify(v), /MAE|RMSE|persistencia|IDEAM/);   // sin metricas de precision
});

test("simulada: sin predicciones, fallo, o respuesta NO marcada como simulada", () => {
  assert.equal(SIN_PREDICCIONES_SIMULADAS, "Aún no hay predicciones simuladas (se generan cada hora)");
  assert.equal(vistaPrediccionSimulada({ estado: "sin_predicciones", predicciones: [], is_simulated: false }).mensaje, SIN_PREDICCIONES_SIMULADAS);
  assert.equal(vistaPrediccionSimulada({ ...SIM, predicciones: [OK.predicciones[0]] }).mensaje, SIN_PREDICCIONES_SIMULADAS);
  for (const r of [null, undefined, {}, "x"]) assert.equal(vistaPrediccionSimulada(r).mensaje, NO_DISPONIBLE_POR_AHORA);
  const real = vistaPrediccionSimulada({ ...OK, is_simulated: false });     // nunca valores reales en esta tarjeta
  assert.deepEqual(real.items, []) ;
  assert.equal(real.aviso, "");
});

test("la tarjeta solo existe para ecoair-sim-NN, fuera del replay y del kiosco", () => {
  const ver = (search, device) => secciones({ ...modos(search), device }).prediccionSim;
  assert.equal(ver("?device=ecoair-sim-01", "ecoair-sim-01"), true);
  assert.equal(ver("?device=ecoair-node-01", "ecoair-node-01"), false);
  assert.equal(ver("", "ecoair-node-01"), false);
  assert.equal(ver("?device=ecoair-sim-01&modo=kiosco", "ecoair-sim-01"), false);
  assert.equal(ver("?modo=replay", "ecoair-sim-01"), false);
  assert.equal(secciones().prediccionSim, false);
});

test("lineas de la tarjeta de PM2.5 (simulada): 4 y 5 h; sin predicciones o con fallo, ninguna", async () => {
  const { lineasPm25Simulada, ETIQUETA_PM25_SIMULADA } = await import("../../static/js/predicciones-vista.js");
  assert.equal(ETIQUETA_PM25_SIMULADA, "predicción");
  assert.deepEqual(lineasPm25Simulada(SIM), ["En 4 h: 12,0 µg/m³ · hacia las 14:00", "En 5 h: 12,5 µg/m³ · hacia las 15:00"]);
  for (const r of [null, undefined, {}, "x", { estado: "sin_predicciones", predicciones: [] },
    { ...SIM, predicciones: [OK.predicciones[0]] },          // solo 3 h (oculto): nada
    { ...OK, is_simulated: false }]) {                        // nunca valores no simulados
    assert.deepEqual(lineasPm25Simulada(r), []);
  }
  assert.doesNotMatch(lineasPm25Simulada(SIM).join(" "), /PM10/);
});
