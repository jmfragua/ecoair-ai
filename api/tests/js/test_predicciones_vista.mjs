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
