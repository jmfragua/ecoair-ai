// Tests de la tarjeta "Tendencia" (logica pura).
import { test } from "node:test";
import assert from "node:assert/strict";
import { vistaTendencia } from "../../static/js/tendencia-vista.js";

test("sin datos: estado vacio de una linea y titulo sin 'ultimas 0,0 h'", () => {
  const v = vistaTendencia({ hours: 24, parcial: true, horas_con_datos: 0 }, 0);
  assert.deepEqual(v, { vacia: true, titulo: "Tendencia de las últimas 24 h", nota: "Sin datos en las últimas 24 h." });
  assert.equal(vistaTendencia(null, 0).vacia, true);
});

test("con datos parciales: la cobertura va en la nota, no en el titulo", () => {
  const v = vistaTendencia({ hours: 24, parcial: true, horas_con_datos: 2.5 }, 30);
  assert.equal(v.titulo, "Tendencia de las últimas 24 h");
  assert.equal(v.vacia, false);
  assert.match(v.nota, /Los datos cubren solo 2,5 h de las últimas 24 h/);
  assert.equal(vistaTendencia({ hours: 24, parcial: false, horas_con_datos: 24 }, 288).nota, "");
  for (const x of [v, vistaTendencia({ hours: 24, parcial: true, horas_con_datos: 0 }, 0)]) assert.doesNotMatch(x.titulo, /0,0/);
});
