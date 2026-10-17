// Logica PURA de la tarjeta "Prediccion experimental" (sin DOM): decide que se muestra en cada
// estado de /devices/{id}/forecast. La usa app.js y la prueban los tests (node --test).
//
// Reglas:
// - estado "sin_datos" o lista de horizontes vacia: se muestran texto, tendencia, fuentes y aviso.
// - otro estado con horizontes: se muestran los horizontes (los no disponibles lo dicen).
// - respuesta invalida o fallo de red: "Predicción no disponible por ahora".
// - la persistencia nunca se presenta como prediccion (el backend no la envia como valor).
// - si la ultima medicion tiene mas de 10 min: "Última medición hace X min (el nodo puede estar apagado)".

export const NO_DISPONIBLE_POR_AHORA = "Predicción no disponible por ahora";
export const MIN_SIN_DATOS_AVISO = 10;

export function avisoUltimaMedicion(minutos) {
  if (typeof minutos !== "number" || !Number.isFinite(minutos) || minutos <= MIN_SIN_DATOS_AVISO) return "";
  return `Última medición hace ${Math.floor(minutos)} min (el nodo puede estar apagado)`;
}

// Devuelve { horizontes: [{etiqueta, disponible, pm25, mae_modelo, mae_persistencia}], mensaje,
//            tendencia, fuentes, aviso, ultima }
export function vistaPrediccion(r) {
  const vacia = { horizontes: [], mensaje: NO_DISPONIBLE_POR_AHORA, tendencia: "", fuentes: "", aviso: "", ultima: "" };
  if (!r || typeof r !== "object" || typeof r.estado !== "string") return vacia;

  const horizontes = Array.isArray(r.horizontes) ? r.horizontes : [];
  const comunes = {
    fuentes: r.fuentes || "",
    aviso: r.aviso || "",
    ultima: avisoUltimaMedicion(r.minutos_desde_ultima_medicion),
  };

  if (r.estado === "sin_datos" || horizontes.length === 0) {
    return {
      ...comunes,
      horizontes: [],
      mensaje: r.texto || NO_DISPONIBLE_POR_AHORA,
      tendencia: r.tendencia || "",
    };
  }

  return {
    ...comunes,
    horizontes: horizontes.map((h) => ({
      etiqueta: `En ${h.minutos / 60} h`,
      disponible: !!h.disponible && h.pm25 != null,
      pm25: h.disponible ? h.pm25 : null,
      mae_modelo: h.mae_modelo ?? null,
      mae_persistencia: h.mae_persistencia ?? null,
    })),
    mensaje: "",
    tendencia: "",
  };
}
