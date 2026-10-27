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

// Texto del chip de estado cuando la ultima medicion es vieja. En kiosco se lee de lejos.
export function textoSinDatos(minutos, kiosco = false) {
  if (typeof minutos !== "number" || !Number.isFinite(minutos)) return kiosco ? "Sin conexión" : "Sin datos";
  return kiosco ? `Sin conexión desde ${haceCuanto(minutos)}` : `Sin datos ${haceCuanto(minutos)}`;
}
