// Logica PURA de los modos del panel (sin DOM).
//   ?modo=replay            replay historico
//   ?modo=kiosco            pantalla grande (feria): solo lo esencial, letra grande, sin formularios ni mapas
//   ?modo=replay,kiosco     (o ?modo=replay&modo=kiosco) las dos cosas
import { haceCuanto } from "./ahora-vista.js";

export function modos(search) {
  const p = new URLSearchParams(search || "");
  const valores = new Set(p.getAll("modo").flatMap((v) => v.split(",")).map((v) => v.trim().toLowerCase()));
  return { replay: valores.has("replay"), kiosco: valores.has("kiosco") };
}

// Que se ve en cada modo. La vista del nodo (sin ?modo=replay) es SOLO en vivo: sin tarjeta de replay,
// sin mapa de las estaciones del IDEAM y sin replay-vista.js. Las preguntas rapidas solo en la vista del
// nodo y fuera del kiosco (nadie puede pulsar en la pantalla de la feria). La tarjeta de prediccion solo
// existe para la estacion SIMULADA (ecoair-sim-NN), fuera del replay y del kiosco: el nodo real no la muestra.
export function secciones({ replay = false, kiosco = false, device = "" } = {}) {
  return {
    prediccionSim: !replay && !kiosco && esDispositivoSimulado(device),   // "Prediccion de PM2.5 · SIMULACION"
    replay,                                   // tarjeta Replay historico + mapa de estaciones del IDEAM
    preguntas: !replay && !kiosco,            // fila "Preguntas rapidas"
    ubicacion: !replay && !kiosco,            // mapa "Ubicacion de la estacion" (solo EcoAir)
    proyecto: !kiosco,                        // franja del proyecto con el QR
  };
}

// ---------- Selector de estacion: cambiar entre vista del nodo y replay ----------
// Dispositivos de simulacion (tools/simular_continuo.py solo escribe en ecoair-sim-NN). La etiqueta
// SIMULACION se muestra por el nombre aunque /devices o la tendencia aun no hayan respondido.
export function esDispositivoSimulado(device) {
  return /^ecoair-sim-\d+$/.test(String(device || ""));
}

// Texto de una estacion en el selector. Con nombre_lugar (solo ecoair-sim-01 y ECOAIR_SIM_NOMBRE_LUGAR en la
// api): "<lugar> (<id>)". Sin el: como siempre, "<nombre> (<id>)" y " · SIMULACIÓN" si es simulada.
export function textoOpcionEstacion(d) {
  if (d && typeof d.nombre_lugar === "string" && d.nombre_lugar.trim()) return `${d.nombre_lugar.trim()} (${d.device_id})`;
  return `${d.name ? `${d.name} (${d.device_id})` : d.device_id}${d.is_simulated ? " · SIMULACIÓN" : ""}`;
}

export const OPCION_REPLAY = "__replay__";

export function urlModoReplay(href, station, at) {
  const u = new URL(href);
  u.searchParams.set("modo", "replay");
  u.searchParams.delete("device");
  if (station) u.searchParams.set("station", station);
  if (at) u.searchParams.set("at", at);
  return u.toString();
}

export function urlModoVivo(href, device) {
  const u = new URL(href);
  for (const k of ["modo", "station", "at"]) u.searchParams.delete(k);
  u.searchParams.set("device", device);
  return u.toString();
}

// Texto del chip de estado cuando la ultima medicion es vieja. En kiosco se lee de lejos.
export function textoSinDatos(minutos, kiosco = false) {
  if (typeof minutos !== "number" || !Number.isFinite(minutos)) return kiosco ? "Sin conexión" : "Sin datos";
  return kiosco ? `Sin conexión desde ${haceCuanto(minutos)}` : `Sin datos ${haceCuanto(minutos)}`;
}
