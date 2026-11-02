// Tests de las preguntas rapidas y de que se ve en cada modo (logica pura).
import { test } from "node:test";
import assert from "node:assert/strict";
import { MIN_LECTURAS_HOY, TEXTOS, responder } from "../../static/js/preguntas-vista.js";
import { modos, secciones } from "../../static/js/modo-vista.js";

const AHORA = Date.parse("2026-10-17T15:00:00Z");                 // 10:00 en Colombia
const MED = (minAntes) => ({ pm25: 14.2, pm10: 22.8, temperature: 24.3, humidity: 61,
  received_at: new Date(AHORA - minAntes * 60_000).toISOString() });
// Respuesta de /air-quality/current con la forma real (los textos de la norma vienen de la API).
const AQ = {
  estado: "ok",
  ica: { general: { ica: 53, category_code: "aceptable", category_name: "Aceptable", color: "amarillo", definido_por: "pm25" } },
  recomendacion: {
    nombre: "Aceptable (ICA 51–100)", efectos: "TEXTO-EFECTOS-DE-LA-API",
    resumen: "RESUMEN-DE-LA-API", acciones: ["ACCION-1-DE-LA-API", "ACCION-2-DE-LA-API"],
    fuentes_por_bloque: {
      efectos: { documento: "Res. 2254/2017, Art. 19, Tabla 5", pagina: "8 (PDF)" },
      acciones: { documento: "EPA AQI Technical Assistance Document (mayo 2026), Tabla 5, material particulado", pagina: "8 impresa (PDF 13)" },
    },
  },
};
const AQ_SIN = { estado: "sin_datos", ultima_medicion: { antiguedad_s: 1500 } };
const HIST = (vals, extra = {}) => ({ puntos: vals.map((v, i) => ({ t: `2026-10-17T${String(i % 24).padStart(2, "0")}:00:00Z`, pm25: v })), ...extra });

test("un solo diccionario con los 6 botones y los textos fijos pedidos", () => {
  assert.deepEqual(TEXTOS.preguntas.map((p) => p.etiqueta), ["¿Cómo está el aire ahora?", "¿Qué significa este color?",
    "¿Qué debo hacer?", "¿Cómo estuvo hoy?", "¿Qué es PM2.5?", "¿Es un equipo oficial?"]);
  assert.deepEqual(responder("oficial").lineas,
    ["No. Es una estación de bajo costo con sensor SDS011; no sustituye mediciones oficiales ni es un equipo certificado."]);
  const pm = responder("pm25").lineas;
  assert.match(pm[0], /2,5 micras o menos.*pulmones/);
  assert.match(pm[1], /PM10/);
  assert.equal(responder("oficial").titulo, "¿Es un equipo oficial?");
});

test("1) ahora: datos frescos -> PM2.5, PM10, categoria y hace cuanto", () => {
  const r = responder("ahora", { aq: AQ, ultima: MED(2) }, AHORA);
  assert.equal(r.lineas[0], "PM2.5: 14,2 µg/m³ · PM10: 22,8 µg/m³ (última lectura hace 2 min).");
  assert.equal(r.lineas[1], "Calidad del aire: Aceptable (ICA 53; la define PM2.5, media de la última hora).");
  // entre 5 y 10 min: la lectura aun es reciente pero el ICA no se calcula
  assert.equal(responder("ahora", { aq: AQ_SIN, ultima: MED(7) }, AHORA).lineas[1],
    "ICA: no se calcula (más de 5 min sin mediciones).");
});

test("1) ahora: datos viejos (> 10 min) -> nunca como actuales; sin datos -> 'Sin datos recientes'", () => {
  const r = responder("ahora", { aq: AQ_SIN, ultima: MED(25) }, AHORA);
  assert.equal(r.lineas.length, 1);
  assert.match(r.lineas[0], /^Sin datos recientes · última lectura hace 25 min \(17 (de )?oct\.?,? 09:35, hora de Colombia\)\.$/);
  assert.doesNotMatch(r.lineas[0], /14,2|22,8/);                        // los valores viejos no se presentan
  assert.match(responder("ahora", { aq: AQ, ultima: MED(10 * 60 * 24 + 30) }, AHORA).lineas[0], /hace 10 días/);
  assert.equal(responder("ahora", { aq: null, ultima: MED(10) }, AHORA).lineas[0].startsWith("PM2.5"), true, "10 min justos: reciente");
  assert.deepEqual(responder("ahora", {}, AHORA).lineas, ["Sin datos recientes", "La estación aún no tiene mediciones."]);
});

test("2) color: categoria + efectos de la norma tal como vienen de la API, con su fuente", () => {
  const r = responder("color", { aq: AQ, ultima: MED(1) }, AHORA);
  assert.deepEqual(r.lineas, ["Aceptable (ICA 51–100) · color amarillo.", "TEXTO-EFECTOS-DE-LA-API"]);
  assert.equal(r.fuente, "Fuente: Res. 2254/2017, Art. 19, Tabla 5 · pág. 8 (PDF)");
  assert.match(responder("color", { aq: AQ_SIN }, AHORA).lineas[0], /^Sin datos recientes/);
  assert.match(responder("color", {}, AHORA).lineas[0], /^Sin datos recientes/);
});

test("3) que hacer: acciones de la API (EPA marcada orientativa); sin datos -> no recomienda", () => {
  const r = responder("hacer", { aq: AQ, ultima: MED(1) }, AHORA);
  assert.deepEqual(r.lineas, ["ACCION-1-DE-LA-API", "ACCION-2-DE-LA-API", "Texto fijo, no IA."]);
  assert.match(r.fuente, /^Fuente: EPA AQI .* \(orientativo\)$/);
  assert.deepEqual(responder("hacer", { aq: AQ_SIN, ultima: MED(25) }, AHORA).lineas, ["Sin datos recientes para recomendar"]);
  assert.deepEqual(responder("hacer", {}, AHORA).lineas, ["Sin datos recientes para recomendar"]);
});

test("4) hoy: minimo, maximo y promedio de la tendencia; < 6 -> insuficientes; nada -> sin datos", () => {
  const r = responder("hoy", { historia: HIST([10, 12, null, 14, 20, 8, 16]) }, AHORA);
  assert.equal(r.lineas[0], "PM2.5 en las últimas 24 h: mínimo 8,0, máximo 20,0 y promedio 13,3 µg/m³ (6 intervalos de 5 min).");
  assert.match(r.lineas[1], /indicativo, no es la media normativa/);
  assert.match(responder("hoy", { historia: HIST([1, 2, 3, 4, 5, 6], { is_simulated: true }) }).lineas.at(-1), /simulados/);
  assert.equal(MIN_LECTURAS_HOY, 6);
  assert.match(responder("hoy", { historia: HIST([10, 12, 14, 20, 8]) }).lineas[0], /^Datos insuficientes/);
  assert.deepEqual(responder("hoy", { historia: HIST([]) }).lineas, ["Sin datos recientes"]);
  assert.deepEqual(responder("hoy", {}).lineas, ["Sin datos recientes"]);
});

test("sin datos del nodo: 1, 3 y 4 responden 'Sin datos recientes' sin lanzar errores", () => {
  for (const datos of [{}, { aq: null, ultima: null, historia: null }, null, undefined]) {
    for (const p of TEXTOS.preguntas) assert.doesNotThrow(() => responder(p.id, datos, AHORA));
    assert.match(responder("ahora", datos, AHORA).lineas[0], /^Sin datos recientes/);
    assert.match(responder("hacer", datos, AHORA).lineas[0], /^Sin datos recientes/);
    assert.match(responder("hoy", datos, AHORA).lineas[0], /^Sin datos recientes/);
  }
});

test("(a)(b)(c) que se ve: replay solo con ?modo=replay; preguntas solo en la vista del nodo sin kiosco", () => {
  const nodo = secciones(modos("?device=ecoair-node-01"));
  assert.deepEqual([nodo.replay, nodo.preguntas, nodo.ubicacion, nodo.proyecto], [false, true, true, true]);
  const replay = secciones(modos("?modo=replay&station=29509"));
  assert.deepEqual([replay.replay, replay.preguntas, replay.ubicacion], [true, false, false]);
  const kiosco = secciones(modos("?device=ecoair-node-01&modo=kiosco"));
  assert.deepEqual([kiosco.replay, kiosco.preguntas, kiosco.ubicacion, kiosco.proyecto], [false, false, false, false]);
  const ambos = secciones(modos("?modo=replay,kiosco"));
  assert.deepEqual([ambos.replay, ambos.preguntas], [true, false]);
});

test("estacion simulada: toda respuesta con datos empieza por SIMULACIÓN y ninguna la presenta como real", () => {
  const datos = { aq: AQ, ultima: MED(2), historia: HIST([1, 2, 3, 4, 5, 6], { is_simulated: true }), simulado: true };
  for (const p of TEXTOS.preguntas) {
    const r = responder(p.id, datos, AHORA);
    const texto = r.lineas.join(" ");
    assert.doesNotMatch(texto.replace(TEXTOS.simulacion, ""), /SDS011|sensor real/, p.id);   // solo "NO son de un sensor real"
    if (p.id !== "pm25") assert.match(texto, /SIMULACI[ÓO]N|SIMULADA/, p.id);
  }
  assert.equal(responder("ahora", datos, AHORA).lineas[0], TEXTOS.simulacion);
  assert.match(responder("oficial", datos).lineas[0], /^No\. Esta es una estación SIMULADA/);
  assert.equal(responder("hoy", datos).lineas.filter((l) => /simula[cd]/i.test(l)).length, 1);   // sin aviso duplicado
  // sin datos tambien lo dice; y la estacion real no cambia
  assert.deepEqual(responder("ahora", { simulado: true }, AHORA).lineas, [TEXTOS.simulacion, "Sin datos recientes", "La estación aún no tiene mediciones."]);
  assert.equal(responder("oficial", { simulado: false }).lineas[0], TEXTOS.oficial[0]);
  assert.equal(responder("ahora", { aq: AQ, ultima: MED(2) }, AHORA).lineas[0].startsWith("PM2.5"), true);
});
