// Tests de la tarjeta "Replay historico" (logica pura).
import { test } from "node:test";
import assert from "node:assert/strict";
import { AVISO_REPLAY, mensajeError, periodoMetricas, vistaEstaciones, vistaMetricas, vistaReplay }
  from "../../static/js/replay-vista.js";

test("aviso fijo exacto", () => {
  assert.equal(AVISO_REPLAY,
    "Replay histórico con datos de estaciones de referencia del IDEAM. No es un pronóstico en tiempo real ni un equipo certificado.");
});

test("estaciones: prefiere Trafico Centro y su hora por defecto; sin datos lo dice", () => {
  const v = vistaEstaciones({ estaciones: [
    { station: "29493", nombre: "Casa de Justicia", desde_local: "2020-01-01T00:00", hasta_local: "2025-12-31T23:00", at_por_defecto: "2025-12-31T17:00" },
    { station: "29509", nombre: "Tráfico Centro", desde_local: "2020-01-01T00:00", hasta_local: "2026-09-30T23:00", at_por_defecto: "2026-09-30T12:00" },
    { station: "x", nombre: "Sin ventana", at_por_defecto: null },
  ] });
  assert.deepEqual([v.station, v.at, v.opciones.length], ["29509", "2026-09-30T12:00", 2]);
  assert.match(v.opciones[1].label, /Tráfico Centro · 2020-01-01 a 2026-09-30/);
  assert.match(vistaEstaciones(null).mensaje, /No hay datos históricos/);
});

test("replay: predicho vs real, sin dato y error con signo", () => {
  const v = vistaReplay({ at_local: "2026-09-30T12:00", fuentes: ["IDEAM SISAIRE"],
    ventana: { h1_a_h6: [20, 19, 18, 18, 17, 16], media_24h: 18.4, horas_24h: 24 },
    predicciones: [
      { horizon_hours: 4, target_local: "2026-09-30T16:00", predicted_pm25: 17.2, observed_pm25: 15, persistence_pm25: 20, error_modelo: 2.2 },
      { horizon_hours: 5, target_local: "2026-09-30T17:00", predicted_pm25: 16.9, observed_pm25: null, persistence_pm25: 20, error_modelo: null },
    ] });
  assert.deepEqual(v.filas[0], { horizonte: "4 h", hora: "09-30 16:00", predicho: "17,2", real: "15,0", persistencia: "20,0", error: "+2,2" });
  assert.equal(v.filas[1].real, "sin dato");
  assert.equal(v.filas[1].error, "—");
  assert.match(v.ventana, /media 24 h 18,4 µg\/m³ \(24 de 24 h\)/);
  assert.equal(v.fuentes, "Datos: IDEAM SISAIRE.");
  assert.match(vistaReplay(null).mensaje, /no disponible/);
});

test("errores, periodo de 30 dias y metricas", () => {
  assert.match(mensajeError(422, "Ventana incompleta: faltan horas"), /No se puede calcular a esa hora: Ventana incompleta/);
  assert.match(mensajeError(503), /no está disponible/);
  assert.deepEqual(periodoMetricas("2026-09-30T12:00"), { desde: "2026-09-01", hasta: "2026-09-30" });
  assert.equal(periodoMetricas("x"), null);
  const m = vistaMetricas({ estado: "pocas_horas", desde: "2026-09-01", hasta: "2026-09-30", mensaje: "Pocas horas válidas: 50.",
    por_horizonte: [{ horizon_hours: 4, n: 50, mae_modelo: 6.123, mae_persistencia: 8.5 }] });
  assert.match(m, /4 h: modelo 6,12 frente a persistencia 8,50 µg\/m³ \(n = 50\)\. Pocas horas válidas/);
});

test("modo replay del panel: actual solo PM2.5, siguiente hora, salto y URLs", async () => {
  const m = await import("../../static/js/replay-vista.js");
  assert.equal(m.BANNER_REPLAY,
    "REPLAY HISTÓRICO · datos reales del IDEAM de una fecha pasada. No es en vivo ni un equipo certificado.");
  const a = m.vistaActualReplay({ at_local: "2026-09-30T12:00", actual: { pm25_1h: 21, media_24h: 23.5, horas_24h: 24,
    ica: { ica: 70, category_code: "aceptable", category_name: "Aceptable" }, recomendacion: { efectos: "x" } } });
  assert.equal(a.icaBy, "ICA con PM2.5 solamente");
  assert.deepEqual([a.ica, a.codigo, a.pm25, a.media24, a.noDisponible], [70, "aceptable", 21, 23.5, "no disponible en el histórico"]);
  assert.equal(a.estado, "Replay histórico · 2026-09-30 12:00");
  assert.equal(m.vistaActualReplay({}), null);
  assert.equal(m.siguienteHora("2026-09-30T23:00"), "2026-10-01T00:00");
  assert.equal(m.siguienteHora("x"), null);
  assert.match(m.avisoSalto({ at_local: "2026-09-30T15:00", salto: { pedida_local: "2026-09-30T13:00", horas: 2 } }),
    /Faltaban horas en la ventana de 2026-09-30 13:00: se saltó 2 h hasta 2026-09-30 15:00/);
  assert.equal(m.avisoSalto({}), "");
  const r = new URL(m.urlModoReplay("http://x/panel?device=ecoair-node-01", "29509", "2026-09-30T12:00"));
  assert.deepEqual([r.searchParams.get("modo"), r.searchParams.get("device"), r.searchParams.get("station")], ["replay", null, "29509"]);
  const v = new URL(m.urlModoVivo(r.toString(), "ecoair-node-01"));
  assert.deepEqual([v.searchParams.get("modo"), v.searchParams.get("at"), v.searchParams.get("device")], [null, null, "ecoair-node-01"]);
  assert.equal(m.VELOCIDADES[0].ms, 3000);
});
