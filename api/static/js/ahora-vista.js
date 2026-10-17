// Logica PURA de los valores "ahora mismo" (sin DOM): ultimo registro de
// /devices/{id}/measurements/latest (pm25, pm10, temperature, humidity, received_at).
// La usan el panel (cuadricula 2x2) y la portada (miniatura). No hay PM1.0: el SDS011 no lo mide.
//
// Reglas:
// - un valor que falta o no es un numero finito se muestra como "—" (nunca undefined/NaN).
// - "Última medición: hace X min · HH:MM (hora de Colombia)".
// - mas de 10 min sin medir: aviso "el nodo puede estar apagado" (mismo umbral que la prediccion).
// - fallo de red sin datos previos: "No se pudo consultar la última medición" (nunca "Cargando…").
import { MIN_SIN_DATOS_AVISO } from "./prediccion-vista.js";

export const SIN_VALOR = "—";
export const NO_SE_PUDO = "No se pudo consultar la última medición";
export const SIN_MEDICIONES = "La estación aún no tiene mediciones";
export const AVISO_APAGADO = `Más de ${MIN_SIN_DATOS_AVISO} min sin mediciones: el nodo puede estar apagado`;

const TZ = "America/Bogota";
const fmtHora = new Intl.DateTimeFormat("es-CO", { timeZone: TZ, hour: "2-digit", minute: "2-digit", hour12: false });
const fmtDia = new Intl.DateTimeFormat("es-CO", { timeZone: TZ, day: "numeric", month: "short" });

export function numero(v, decimales) {
  if (typeof v !== "number" || !Number.isFinite(v)) return SIN_VALOR;
  return v.toLocaleString("es-CO", { minimumFractionDigits: decimales, maximumFractionDigits: decimales });
}

export function haceCuanto(minutos) {
  if (minutos < 1) return "hace menos de 1 min";
  if (minutos < 60) return `hace ${minutos} min`;
  const h = Math.floor(minutos / 60), m = minutos % 60;
  if (h < 24) return m ? `hace ${h} h ${m} min` : `hace ${h} h`;
  const d = Math.floor(h / 24), hr = h % 24;
  return hr ? `hace ${d} d ${hr} h` : `hace ${d} d`;
}

function horaColombia(fecha, ahora) {
  const hora = fmtHora.format(fecha);
  return fmtDia.format(fecha) === fmtDia.format(ahora) ? hora : `${fmtDia.format(fecha)} ${hora}`;
}

// m: respuesta de /measurements/latest o null. ahora: Date o milisegundos.
// opciones.fallo: true si la ultima consulta fallo (red, 5xx); sin m, lo dice.
export function vistaAhora(m, ahora = Date.now(), { fallo = false } = {}) {
  const valida = m && typeof m === "object";
  const valores = {
    pm25: numero(valida ? m.pm25 : null, 1),
    pm10: numero(valida ? m.pm10 : null, 1),
    temperatura: numero(valida ? m.temperature : null, 1),
    humedad: numero(valida ? m.humidity : null, 0),
  };
  const t = valida && typeof m.received_at === "string" ? new Date(m.received_at) : null;
  if (!t || Number.isNaN(t.getTime())) {
    return { ...valores, ultima: valida ? "Hora de la última medición desconocida" : fallo ? NO_SE_PUDO : SIN_MEDICIONES,
             aviso: "", antigua: false, minutos: null };
  }
  const ref = new Date(ahora);
  const minutos = Math.max(0, Math.floor((ref - t) / 60_000));
  const antigua = minutos > MIN_SIN_DATOS_AVISO;
  return {
    ...valores,
    ultima: `Última medición: ${haceCuanto(minutos)} · ${horaColombia(t, ref)} (hora de Colombia)`,
    aviso: antigua ? AVISO_APAGADO : "",
    antigua,
    minutos,
  };
}
