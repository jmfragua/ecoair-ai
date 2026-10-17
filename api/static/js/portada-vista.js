// Logica PURA de la tarjeta de estado de la portada (sin DOM). Usa las mismas respuestas que el
// panel: /devices (marca de simulacion) y /devices/{id}/air-quality/current (mismo calculo del ICA).
// Nunca deja "Cargando…": sin respuesta valida o sin datos, dice "Sin datos recientes".

export const SIN_DATOS = "Sin datos recientes";

// Rotulo de la miniatura: con la estacion fuera de linea (estado "fuera" o ultima medicion de mas
// de 10 min) los cuatro valores son la ultima lectura, no valores actuales.
// estado: resultado de vistaEstado; ahora: resultado de vistaAhora (ahora-vista.js).
export function rotuloUltimaLectura(estado, ahora) {
  const fuera = (estado && estado.conexion === "fuera") || (ahora && ahora.antigua);
  if (!fuera || !ahora || !ahora.hora) return "";
  return `Última lectura · ${ahora.hora} (hora de Colombia)`;
}

// aq: respuesta de /air-quality/current o null si fallo la consulta.
// dispositivo: entrada de /devices (con is_simulated) o null.
export function vistaEstado(aq, dispositivo) {
  const simulado = !!(dispositivo && dispositivo.is_simulated);
  if (!aq || typeof aq !== "object" || typeof aq.estado !== "string") {
    return { conexion: "desconocida", textoConexion: "No se pudo consultar la estación",
             ica: null, categoria: SIN_DATOS, color: null, base: "", simulado };
  }
  if (aq.estado !== "ok" || !aq.ica || !aq.ica.general) {
    return { conexion: "fuera", textoConexion: "Estación fuera de línea",
             ica: null, categoria: SIN_DATOS, color: null, base: "", simulado };
  }
  const g = aq.ica.general;
  return {
    conexion: "en_linea", textoConexion: "Estación en línea",
    ica: g.ica, categoria: g.category_name, color: g.category_code,
    base: "ICA con la media de la última hora (indicativo) · Res. 2254/2017",
    simulado,
  };
}
