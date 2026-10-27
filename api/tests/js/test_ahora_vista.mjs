// Tests de los valores "ahora mismo" del panel (2x2) y de la portada (miniatura). Logica pura.
import { test } from "node:test";
import assert from "node:assert/strict";
import { vistaAhora, haceCuanto, numero, SIN_VALOR, NO_SE_PUDO, SIN_MEDICIONES, AVISO_APAGADO }
  from "../../static/js/ahora-vista.js";

// 15:00 UTC = 10:00 en Colombia (UTC-5, sin horario de verano)
const AHORA = Date.parse("2026-10-17T15:00:00Z");
const M = { id: 1, device_id: "ecoair-node-01", received_at: "2026-10-17T14:58:00Z",
            temperature: 24.36, humidity: 61.6, pm25: 12.04, pm10: 20.5 };
const sinBasura = (v) => {
  for (const k of ["pm25", "pm10", "temperatura", "humedad", "ultima", "aviso"]) assert.ok(!/undefined|NaN|null|Cargando/.test(String(v[k])), `${k}: ${v[k]}`);
};

test("todos los valores: 2x2 con unidades decimales y hora de Colombia", () => {
  const v = vistaAhora(M, AHORA);
  assert.deepEqual([v.pm25, v.pm10, v.temperatura, v.humedad], ["12,0", "20,5", "24,4", "62"]);
  assert.equal(v.ultima, "Última medición: hace 2 min · 09:58 (hora de Colombia)");
  assert.equal(v.aviso, "");
  assert.equal(v.antigua, false);
  assert.ok(!("pm1" in v) && !("pm10_0" in v));
  sinBasura(v);
});

test("valores faltantes o no numericos: '—', nunca undefined/NaN", () => {
  const v = vistaAhora({ received_at: M.received_at, pm25: null, pm10: undefined, temperature: NaN, humidity: "61" }, AHORA);
  assert.deepEqual([v.pm25, v.pm10, v.temperatura, v.humedad], [SIN_VALOR, SIN_VALOR, SIN_VALOR, SIN_VALOR]);
  assert.match(v.ultima, /hace 2 min/);
  sinBasura(v);
  const sinHora = vistaAhora({ pm25: 10 }, AHORA);
  assert.equal(sinHora.pm25, "10,0");
  assert.equal(sinHora.ultima, "Hora de la última medición desconocida");
  sinBasura(sinHora);
  assert.equal(numero(Infinity, 1), SIN_VALOR);
});

test("medicion antigua (mas de 10 min): aviso de nodo apagado, conserva los valores", () => {
  assert.equal(vistaAhora({ ...M, received_at: "2026-10-17T14:50:00Z" }, AHORA).aviso, "", "10 min justos no avisa");
  const v = vistaAhora({ ...M, received_at: "2026-10-17T14:35:00Z" }, AHORA);
  assert.equal(v.aviso, AVISO_APAGADO);
  assert.match(v.aviso, /el nodo puede estar apagado/);
  assert.equal(v.ultima, "Última medición: hace 25 min · 09:35 (hora de Colombia)");
  assert.equal(v.pm25, "12,0");
  const ayer = vistaAhora({ ...M, received_at: "2026-10-16T13:00:00Z" }, AHORA);
  assert.match(ayer.ultima, /^Última medición: hace 1 día 2 h · 16 de oct\.? 08:00 \(hora de Colombia\)$/);
  assert.equal(ayer.antigua, true);
});

test("fallo de red: sin datos previos lo dice; nunca 'Cargando…'", () => {
  const v = vistaAhora(null, AHORA, { fallo: true });
  assert.deepEqual([v.pm25, v.pm10, v.temperatura, v.humedad], [SIN_VALOR, SIN_VALOR, SIN_VALOR, SIN_VALOR]);
  assert.equal(v.ultima, NO_SE_PUDO);
  assert.equal(v.aviso, "");
  sinBasura(v);
  // 404 (estacion sin mediciones) no es un fallo de red
  assert.equal(vistaAhora(null, AHORA).ultima, SIN_MEDICIONES);
  // con datos previos y fallo: se conservan y la antiguedad sigue contando
  const despues = vistaAhora(M, AHORA + 20 * 60_000, { fallo: true });
  assert.equal(despues.pm25, "12,0");
  assert.match(despues.ultima, /hace 22 min/);
  assert.equal(despues.aviso, AVISO_APAGADO);
});

test("hace cuanto: minutos, horas y dias; reloj adelantado no da negativos", () => {
  assert.deepEqual([0, 1, 59, 60, 61, 1440, 1500, 1860, 15491].map(haceCuanto),
    ["hace menos de 1 min", "hace 1 min", "hace 59 min", "hace 1 h", "hace 1 h 1 min", "hace 1 día", "hace 1 día 1 h",
     "hace 1 día 7 h", "hace 10 días 18 h"]);
  assert.match(vistaAhora({ ...M, received_at: "2026-10-17T15:03:00Z" }, AHORA).ultima, /hace menos de 1 min/);
});

test("etiqueta de PM2.5/PM10: 'actual' con datos recientes; fuera de linea, 'última lectura' y su hora", async () => {
  const { etiquetaMetrica } = await import("../../static/js/ahora-vista.js");
  assert.deepEqual(etiquetaMetrica(vistaAhora(M, AHORA)), { largo: "actual · media 1 h ", corto: "media 1 h " });
  // 10 min justos: todavia no es fuera de linea (misma condicion que el aviso)
  assert.equal(etiquetaMetrica(vistaAhora({ ...M, received_at: "2026-10-17T14:50:00Z" }, AHORA)).largo, "actual · media 1 h ");
  const fuera = etiquetaMetrica(vistaAhora({ ...M, received_at: "2026-10-17T14:35:00Z" }, AHORA));
  assert.equal(fuera.largo, "última lectura 09:35 · media 1 h ");
  assert.match(fuera.corto, /última lectura 09:35/);
  assert.ok(!/actual/.test(fuera.largo + fuera.corto));
  // sin medicion: no inventa una hora
  assert.equal(etiquetaMetrica(vistaAhora(null, AHORA, { fallo: true })).largo, "actual · media 1 h ");
});
