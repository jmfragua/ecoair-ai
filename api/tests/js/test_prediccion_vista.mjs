// Tests de la tarjeta "Prediccion experimental" (logica pura). Ejecutar: node --test api/tests/js
import { test } from "node:test";
import assert from "node:assert/strict";
import { vistaPrediccion, avisoUltimaMedicion, NO_DISPONIBLE_POR_AHORA }
  from "../../static/js/prediccion-vista.js";

// Respuesta real reportada (la tarjeta se quedaba en "Cargando…")
const SIN_DATOS = {
  estado: "sin_datos", horizontes: [],
  texto: "Predicción no disponible: faltan datos de las últimas 3 horas.",
  tendencia: "Tendencia de las últimas 3 horas: sin datos suficientes.",
  fuentes: "Modelo entrenado con datos simulados. Experimental.",
  aviso: "Atención: ...",
};

test("sin_datos muestra texto, tendencia, fuentes y aviso (nunca 'Cargando…')", () => {
  const v = vistaPrediccion(SIN_DATOS);
  assert.deepEqual(v.horizontes, []);
  assert.equal(v.mensaje, SIN_DATOS.texto);
  assert.equal(v.tendencia, SIN_DATOS.tendencia);
  assert.equal(v.fuentes, SIN_DATOS.fuentes);
  assert.equal(v.aviso, SIN_DATOS.aviso);
  assert.notEqual(v.mensaje, "Cargando…");
});

test("horizontes vacios con otro estado tambien muestra el texto", () => {
  const v = vistaPrediccion({ estado: "no_supera_linea_base", horizontes: [],
    texto: "Predicción no disponible: el modelo no superó la línea base.", tendencia: "T" });
  assert.equal(v.mensaje, "Predicción no disponible: el modelo no superó la línea base.");
  assert.equal(v.tendencia, "T");
});

test("estado ok muestra los horizontes; el no disponible no trae valor", () => {
  const v = vistaPrediccion({ estado: "ok", fuentes: "F", horizontes: [
    { minutos: 60, pm25: null, disponible: false },
    { minutos: 180, pm25: 23.4, disponible: true, mae_modelo: 4.2, mae_persistencia: 4.7 }] });
  assert.equal(v.mensaje, "");
  assert.deepEqual(v.horizontes.map((h) => [h.etiqueta, h.disponible, h.pm25]),
    [["En 1 h", false, null], ["En 3 h", true, 23.4]]);
});

test("fallo de red o respuesta invalida: 'Predicción no disponible por ahora'", () => {
  for (const r of [null, undefined, {}, { horizontes: [] }, "error"]) {
    assert.equal(vistaPrediccion(r).mensaje, NO_DISPONIBLE_POR_AHORA);
  }
});

test("aviso de ultima medicion solo con mas de 10 min y datos reales", () => {
  assert.equal(avisoUltimaMedicion(47), "Última medición hace 47 min (el nodo puede estar apagado)");
  assert.equal(avisoUltimaMedicion(10), "");
  assert.equal(avisoUltimaMedicion(3), "");
  assert.equal(avisoUltimaMedicion(null), "");          // sin dato: no se inventa
  assert.equal(vistaPrediccion({ ...SIN_DATOS, minutos_desde_ultima_medicion: 47 }).ultima,
    "Última medición hace 47 min (el nodo puede estar apagado)");
});
