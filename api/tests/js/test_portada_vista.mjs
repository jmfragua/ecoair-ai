// Tests de la tarjeta de estado de la portada (logica pura).
import { test } from "node:test";
import assert from "node:assert/strict";
import { vistaEstado, SIN_DATOS } from "../../static/js/portada-vista.js";

const OK = { estado: "ok", ica: { general: { ica: 76, category_name: "Aceptable", category_code: "aceptable" } } };

test("fallo de red o respuesta invalida: 'Sin datos recientes', nunca 'Cargando…'", () => {
  for (const aq of [null, undefined, {}, "error", { estado: 1 }]) {
    const v = vistaEstado(aq, null);
    assert.equal(v.categoria, SIN_DATOS);
    assert.equal(v.ica, null);
    assert.equal(v.conexion, "desconocida");
    assert.ok(!/Cargando/.test(v.textoConexion));
  }
});

test("sin datos recientes: estacion fuera de linea", () => {
  const v = vistaEstado({ estado: "sin_datos", ultima_medicion: null }, { device_id: "x", is_simulated: false });
  assert.deepEqual([v.conexion, v.textoConexion, v.categoria, v.ica], ["fuera", "Estación fuera de línea", SIN_DATOS, null]);
});

test("en linea con ICA y categoria (mismo calculo que el panel)", () => {
  const v = vistaEstado(OK, { device_id: "ecoair-node-01", is_simulated: false });
  assert.deepEqual([v.conexion, v.textoConexion, v.ica, v.categoria, v.color, v.simulado],
    ["en_linea", "Estación en línea", 76, "Aceptable", "aceptable", false]);
  assert.match(v.base, /media de la última hora/);
});

test("dispositivo simulado lleva la marca SIMULACION", () => {
  assert.equal(vistaEstado(OK, { device_id: "ecoair-sim-01", is_simulated: true }).simulado, true);
  assert.equal(vistaEstado(null, { is_simulated: true }).simulado, true);
});
