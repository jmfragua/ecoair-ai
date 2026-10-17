// Tests de la tarjeta "Recomendación" (logica pura). Con RECOMENDACIONES_JSON usa el texto REAL de
// shared/recomendaciones.py (lo genera el test de pytest), el mismo que usan los correos.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { vistaRecomendacion, NO_DISPONIBLES } from "../../static/js/recomendaciones-vista.js";

const reales = process.env.RECOMENDACIONES_JSON
  ? JSON.parse(readFileSync(process.env.RECOMENDACIONES_JSON, "utf8")) : null;

test("cada nivel real se renderiza con resumen, desplegable completo y fuentes", { skip: !reales }, () => {
  assert.equal(Object.keys(reales).length, 6);
  for (const [codigo, rec] of Object.entries(reales)) {
    const v = vistaRecomendacion(rec);
    assert.ok(v.disponible, codigo);
    assert.equal(v.efectos, rec.efectos);                    // mismo texto que el correo
    assert.ok(v.resumen.length > 10, codigo);
    const titulos = v.secciones.map((s) => s.titulo);
    for (const t of [rec.titulos.poblacion_general, rec.titulos.grupos_sensibles, rec.titulos.acciones]) {
      assert.ok(titulos.includes(t), `${codigo}: falta ${t}`);
    }
    const acciones = v.secciones.find((s) => s.titulo === rec.titulos.acciones);
    assert.deepEqual(acciones.lista, rec.acciones);
    assert.equal(titulos.includes(rec.titulos.si_sube), Boolean(rec.si_sube), codigo);
    assert.equal(titulos.includes(rec.titulos.cuando_consultar), Boolean(rec.cuando_consultar), codigo);
    assert.ok(v.fuentes.length >= 2 && v.etiqueta === "Texto fijo de la norma, no IA", codigo);
    for (const s of v.secciones) assert.ok(s.parrafos.length || s.lista.length, `${codigo}: ${s.titulo} vacio`);
  }
});

test("sin recomendacion (sin datos o fallo) se dice y no hay desplegable", () => {
  for (const r of [null, undefined, {}, { efectos: "x" }, "error"]) {
    const v = vistaRecomendacion(r);
    assert.equal(v.disponible, false);
    assert.equal(v.mensaje, NO_DISPONIBLES);
    assert.deepEqual(v.secciones, []);
  }
  assert.equal(vistaRecomendacion(null, "Sin datos recientes").mensaje, "Sin datos recientes");
});

test("sin datos recientes: texto breve, sin recomendaciones inventadas", async () => {
  const { SIN_DATOS_RECIENTES } = await import("../../static/js/recomendaciones-vista.js");
  assert.equal(SIN_DATOS_RECIENTES,
    "Sin datos recientes: las recomendaciones aparecerán cuando el nodo vuelva a enviar mediciones.");
  const v = vistaRecomendacion(null, SIN_DATOS_RECIENTES);
  assert.equal(v.disponible, false);
  assert.equal(v.mensaje, SIN_DATOS_RECIENTES);
  assert.deepEqual([v.efectos, v.resumen, v.secciones, v.fuentes, v.etiqueta], ["", "", [], [], ""]);
});
