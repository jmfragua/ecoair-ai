// Tests del mapa de estaciones (logica pura).
import { test } from "node:test";
import assert from "node:assert/strict";
import { ATRIBUCION_OSM, LEAFLET_JS, OPCIONES_TESELAS, TEXTO_APROX, TEXTO_REFERENCIA, URL_TESELAS, encuadre,
  estiloMarcador, vistaMapa } from "../../static/js/mapa-vista.js";

const R = {
  estaciones: [
    { station: "29509", nombre: "Tráfico Centro (Medellín)", lat: 6.252709, lon: -75.569656, fuente: "IDEAM g4t8-zkc3" },
    { station: "29493", nombre: "Casa de Justicia (Itagüí)", lat: 6.185667, lon: -75.597206, fuente: "IDEAM g4t8-zkc3" },
    { station: "x", nombre: "Rota", lat: null, lon: -75.5, fuente: "?" },
  ],
  faltantes: [{ station: "31854", nombre: "Fiscalía Sector Caribe (Medellín)" }],
  ecoair: null,
  mensaje_ecoair: "La ubicación de la estación EcoAir no está configurada: no se dibuja.",
};

test("atribucion OSM obligatoria, Leaflet local y uso ligero de teselas", () => {
  assert.equal(ATRIBUCION_OSM.replace(/<[^>]+>/g, ""), "© OpenStreetMap contributors");
  assert.equal(URL_TESELAS, "https://tile.openstreetmap.org/{z}/{x}/{y}.png");
  assert.equal(LEAFLET_JS, "/vendor/leaflet-1.9.4/leaflet.js");
  assert.ok(OPCIONES_TESELAS.maxZoom <= 17 && OPCIONES_TESELAS.minZoom >= 10 && OPCIONES_TESELAS.keepBuffer === 0);
});

test("marcadores: solo con coordenadas, seleccionada marcada y popup de referencia", () => {
  const v = vistaMapa(R, "29493");
  assert.deepEqual(v.marcadores.map((m) => [m.station, m.seleccionada]), [["29509", false], ["29493", true]]);
  assert.deepEqual(v.marcadores[0].popup, { titulo: "Tráfico Centro (Medellín)",
    lineas: [TEXTO_REFERENCIA, "Fuente: IDEAM g4t8-zkc3"] });
  assert.equal(TEXTO_REFERENCIA, "estación de referencia");
  assert.match(v.avisos[0], /Sin coordenadas verificadas \(no se dibujan\): Fiscalía Sector Caribe \(Medellín\), Rota\./);
  assert.match(v.avisos[1], /EcoAir no está configurada/);
  assert.equal(v.ecoair, null);
});

test("EcoAir: ubicacion aproximada y encuadre", () => {
  const v = vistaMapa({ ...R, ecoair: { lat: 6.25, lon: -75.57, nombre: "Estación EcoAir", fuente: "aprox. ~1 km" },
    mensaje_ecoair: "" }, "29509");
  assert.deepEqual(v.ecoair.popup.lineas, [TEXTO_APROX, "aprox. ~1 km"]);
  assert.equal(TEXTO_APROX, "ubicación aproximada");
  assert.equal(v.avisos.length, 1);
  assert.deepEqual(encuadre(v), [[6.185667, -75.597206], [6.252709, -75.569656]]);
  assert.equal(vistaMapa(null, "").marcadores.length, 0);
});

test("seleccionada se distingue por tamano y borde, no solo por color", () => {
  const a = estiloMarcador(true), b = estiloMarcador(false);
  assert.ok(a.radius > b.radius && a.weight > b.weight && a.color !== b.color);
});

test("tarjeta de ubicacion: solo EcoAir, sin configurar, simulada y oculta en replay", async () => {
  const m = await import("../../static/js/mapa-vista.js");
  const r = { estaciones: R.estaciones, ecoair: { lat: 6.25, lon: -75.57, nombre: "Estación EcoAir", fuente: "~1 km" } };
  const v = m.vistaUbicacion(r, { device: "ecoair-node-01" });
  assert.equal(v.estado, "mapa");
  assert.deepEqual([v.ecoair.lat, v.ecoair.lon], [6.25, -75.57]);
  assert.deepEqual(v.ecoair.popup.lineas, ["ubicación aproximada", "~1 km"]);
  assert.ok(!("marcadores" in v) && !JSON.stringify(v).includes("Tráfico"));   // nunca estaciones del IDEAM
  assert.ok(m.ZOOM_UBICACION >= 13 && m.ZOOM_UBICACION <= 15);                 // barrio, no calle
  for (const sin of [{ ...r, ecoair: null }, null, { ecoair: { lat: "6.2", lon: -75 } }]) {
    assert.deepEqual(m.vistaUbicacion(sin, { device: "ecoair-node-01" }), { estado: "sin_config", texto: "Ubicación no configurada", ecoair: null });
  }
  assert.equal(m.vistaUbicacion(r, { device: "otro-nodo" }).estado, "sin_config");
  const s = m.vistaUbicacion(r, { device: "ecoair-sim-01", simulado: true });
  assert.deepEqual([s.estado, s.texto, s.ecoair], ["simulacion", "SIMULACIÓN, sin ubicación real", null]);
  assert.equal(m.vistaUbicacion(r, { device: "ecoair-node-01", replay: true }).estado, "oculta");
});
