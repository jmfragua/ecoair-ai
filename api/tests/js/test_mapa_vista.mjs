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

test("ubicacion interactiva: vista del Replay, limites 11-17, sin rueda, tactil con dos dedos", async () => {
  const m = await import("../../static/js/mapa-vista.js");
  const eco = { lat: 6.23, lon: -75.6, nombre: "Estación EcoAir", fuente: "~1 km" };
  const r = { ...R, ecoair: eco, mensaje_ecoair: "" };
  const v = m.vistaUbicacion(r, { device: "ecoair-node-01" });
  assert.deepEqual(v.vista, m.encuadre(m.vistaMapa(r, "")));            // misma vista inicial que el Replay
  assert.ok(!("marcadores" in v));                                     // las estaciones solo encuadran
  assert.deepEqual(v.limites, m.LIMITES);                              // punto dentro del Valle de Aburra
  const fuera = m.limitesCon({ lat: 6.5, lon: -75.4 });
  assert.ok(fuera[1][0] > 6.5 && fuera[1][1] > -75.4 && fuera[0][0] === 6.05);   // amplia N y E, no S
  const pc = m.opcionesUbicacion(false), cel = m.opcionesUbicacion(true);
  assert.deepEqual([pc.minZoom, pc.maxZoom, pc.zoomControl, pc.scrollWheelZoom, pc.dragging], [11, 17, true, false, true]);
  assert.deepEqual([cel.dragging, cel.touchZoom, cel.scrollWheelZoom], [false, true, false]);   // un dedo = scroll de la pagina
  assert.equal(m.TEXTO_CENTRAR, "Centrar en la estación");
  assert.equal(m.ZOOM_UBICACION, 14);
  assert.equal(m.vistaUbicacion({ ...r, ecoair: null }, { device: "ecoair-node-01" }).texto, "Ubicación no configurada");
  assert.equal(m.vistaUbicacion(r, { device: "ecoair-sim-01", simulado: true }).texto, "SIMULACIÓN, sin ubicación real");
});

test("arreglo tactil comun a los dos mapas (Replay y ubicacion)", async () => {
  const m = await import("../../static/js/mapa-vista.js");
  assert.deepEqual(m.interaccionTactil(true), { dragging: false, touchZoom: true });    // un dedo = pagina
  assert.deepEqual(m.interaccionTactil(false), { dragging: true, touchZoom: true });    // raton: arrastre normal
  const u = m.opcionesUbicacion(true);
  assert.deepEqual([u.dragging, u.touchZoom], [false, true]);
});
