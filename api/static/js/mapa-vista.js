// Logica PURA del mapa de estaciones (sin DOM ni Leaflet). Usa /mapa/estaciones: coordenadas fijas
// de api/mapa.py (fuente: IDEAM) y el punto APROXIMADO de EcoAir, si el equipo lo configuro.

export const LEAFLET_JS = "/vendor/leaflet-1.9.4/leaflet.js";
export const LEAFLET_CSS = "/vendor/leaflet-1.9.4/leaflet.css";
// Teselas de OpenStreetMap: unico recurso externo del panel. Politica de uso de OSM: uso ligero, sin
// precarga masiva -> zoom acotado, area acotada al Valle de Aburra, sin rueda del raton y sin buffer extra.
export const URL_TESELAS = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
export const ATRIBUCION_OSM =
  '© <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors';
export const OPCIONES_TESELAS = { minZoom: 11, maxZoom: 17, keepBuffer: 0, updateWhenIdle: true, crossOrigin: false };
export const LIMITES = [[6.05, -75.75], [6.45, -75.45]]; // Valle de Aburra (sur-oeste, nor-este)
export const TEXTO_REFERENCIA = "estación de referencia";
export const TEXTO_APROX = "ubicación aproximada";
export const SIN_TESELAS = "No se pudo cargar el mapa base de OpenStreetMap (sin internet): se muestran solo los marcadores.";
export const SIN_LEAFLET = "El mapa no está disponible en este momento.";

const valida = (e) => e && typeof e.lat === "number" && typeof e.lon === "number"
  && Number.isFinite(e.lat) && Number.isFinite(e.lon);

// -> {marcadores, ecoair, avisos}. Solo estaciones con coordenadas numericas; nunca se inventa una.
export function vistaMapa(r, seleccionada) {
  const lista = r && Array.isArray(r.estaciones) ? r.estaciones : [];
  const marcadores = lista.filter(valida).map((e) => ({
    station: e.station, lat: e.lat, lon: e.lon, nombre: e.nombre,
    seleccionada: e.station === seleccionada,
    popup: { titulo: e.nombre, lineas: [TEXTO_REFERENCIA, `Fuente: ${e.fuente}`] },
  }));
  const sinCoord = lista.filter((e) => e && !valida(e)).map((e) => e.nombre || e.station);
  const faltan = (r && Array.isArray(r.faltantes) ? r.faltantes : []).map((f) => f.nombre || f.station);
  const avisos = [];
  const todas = [...faltan, ...sinCoord];
  if (todas.length) avisos.push(`Sin coordenadas verificadas (no se dibujan): ${todas.join(", ")}.`);
  const e = r && r.ecoair;
  let ecoair = null;
  if (valida(e)) {
    ecoair = { lat: e.lat, lon: e.lon, nombre: e.nombre,
      popup: { titulo: e.nombre, lineas: [TEXTO_APROX, e.fuente].filter(Boolean) } };
  } else if (r && r.mensaje_ecoair) {
    avisos.push(r.mensaje_ecoair);
  }
  return { marcadores, ecoair, avisos };
}

// Encuadre: todas las estaciones dibujadas (y EcoAir, si hay) -> [[latMin, lonMin], [latMax, lonMax]]
export function encuadre(v) {
  const pts = [...v.marcadores, ...(v.ecoair ? [v.ecoair] : [])];
  if (!pts.length) return LIMITES;
  const lats = pts.map((p) => p.lat), lons = pts.map((p) => p.lon);
  return [[Math.min(...lats), Math.min(...lons)], [Math.max(...lats), Math.max(...lons)]];
}

// Estilo del marcador: la seleccionada se distingue por tamano y borde, no solo por color.
export function estiloMarcador(seleccionada) {
  return seleccionada
    ? { radius: 11, weight: 4, color: "#ffffff", fillColor: "#3987e5", fillOpacity: 1 }
    : { radius: 7, weight: 2, color: "#0d0d0d", fillColor: "#3987e5", fillOpacity: 0.8 };
}

// ---------- Tarjeta "Ubicacion de la estacion" (panel en vivo) ----------
// Solo el punto aproximado de EcoAir (ECOAIR_UBICACION_APROX), nunca las estaciones del IDEAM.
export const DISPOSITIVO_UBICACION = "ecoair-node-01";
export const ZOOM_UBICACION = 14;            // barrio (~1 km de radio visible), no calle; fijo
export const SIN_UBICACION = "Ubicación no configurada";
export const SIM_UBICACION = "SIMULACIÓN, sin ubicación real";

// -> {estado: "oculta" | "simulacion" | "sin_config" | "mapa", texto, ecoair}
export function vistaUbicacion(r, { device, simulado = false, replay = false } = {}) {
  if (replay) return { estado: "oculta", texto: "", ecoair: null };
  if (simulado) return { estado: "simulacion", texto: SIM_UBICACION, ecoair: null };
  const e = r && r.ecoair;
  if (device !== DISPOSITIVO_UBICACION || !valida(e)) return { estado: "sin_config", texto: SIN_UBICACION, ecoair: null };
  const ecoair = { lat: e.lat, lon: e.lon, nombre: e.nombre,
    popup: { titulo: e.nombre, lineas: [TEXTO_APROX, e.fuente].filter(Boolean) } };
  // Vista inicial = la del mapa del Replay (las estaciones solo encuadran; NO se dibujan aqui).
  const vista = encuadre({ ...vistaMapa(r, ""), ecoair });
  return { estado: "mapa", texto: `${TEXTO_APROX} · radio de ~1 km`, ecoair, vista, limites: limitesCon(ecoair) };
}

// Limites del Valle de Aburra, ampliados si el punto de EcoAir quedara fuera (asi "Centrar" siempre llega).
export function limitesCon(p) {
  const [[s, o], [n, e]] = LIMITES;
  if (!valida(p)) return LIMITES;
  const m = 0.02;
  return [[Math.min(s, p.lat - m), Math.min(o, p.lon - m)], [Math.max(n, p.lat + m), Math.max(e, p.lon + m)]];
}

export const TEXTO_CENTRAR = "Centrar en la estación";
export const AYUDA_TACTIL = "Mueve el mapa con dos dedos; con uno se desplaza la página.";

// Interaccion del mapa de ubicacion: igual que el del Replay (zoom 11-17, +/-, sin rueda). Con pantalla
// tactil, un dedo NO arrastra el mapa (no atrapa el scroll de la pagina): se mueve y amplia con dos dedos.
export function opcionesUbicacion(tactil) {
  return { minZoom: OPCIONES_TESELAS.minZoom, maxZoom: OPCIONES_TESELAS.maxZoom, zoomControl: true,
    scrollWheelZoom: false, ...interaccionTactil(tactil), doubleClickZoom: true, boxZoom: false,
    keyboard: true, maxBoundsViscosity: 1 };
}

// Comun a los dos mapas: con pantalla tactil un dedo desplaza la pagina (Leaflet deja touch-action
// pan-x pan-y) y dos dedos mueven y amplian el mapa; con raton se arrastra normal.
export function interaccionTactil(tactil) {
  return { dragging: !tactil, touchZoom: true };
}

// Circulo que marca la incertidumbre del punto de EcoAir (redondeado a 2 decimales, ~1 km).
export const RADIO_APROX_M = 1000;
