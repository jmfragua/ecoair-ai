// Logica PURA de la fila "Preguntas rapidas" (solo vista del nodo en vivo). Sin IA y sin llamadas
// nuevas: responde con lo que el panel ya cargo (/air-quality/current, /measurements/latest y la
// tendencia de 24 h) y con textos fijos.
// - TODOS los textos fijos estan en TEXTOS (un solo sitio para editarlos).
// - Los textos de la norma NO se escriben aqui: vienen de la API (shared/recomendaciones.py e
//   api/ica.py), la misma fuente que usan la tarjeta de recomendacion y los correos.
import { haceCuanto, numero, vistaAhora } from "./ahora-vista.js";

export const TEXTOS = {
  titulo: "Preguntas rápidas",
  preguntas: [
    { id: "ahora", etiqueta: "¿Cómo está el aire ahora?" },
    { id: "color", etiqueta: "¿Qué significa este color?" },
    { id: "hacer", etiqueta: "¿Qué debo hacer?" },
    { id: "hoy", etiqueta: "¿Cómo estuvo hoy?" },
    { id: "pm25", etiqueta: "¿Qué es PM2.5?" },
    { id: "oficial", etiqueta: "¿Es un equipo oficial?" },
  ],
  sinDatos: "Sin datos recientes",
  sinMediciones: "La estación aún no tiene mediciones.",
  sinDatosRecomendar: "Sin datos recientes para recomendar",
  datosInsuficientes: "Datos insuficientes",
  icaNoDisponible: "ICA: no se calcula (más de 5 min sin mediciones).",
  colorSinDatos: "Sin datos recientes: el semáforo está en gris porque no se calcula el ICA.",
  etiquetaFija: "Texto fijo, no IA.",
  orientativo: "orientativo",
  promedioNota: "Promedio simple de los intervalos de 5 min con datos; indicativo, no es la media normativa de 24 h.",
  simulados: "Datos simulados: no son mediciones de un sensor real.",
  pm25: [
    "PM2.5 son partículas finas de 2,5 micras o menos, que pueden llegar a los pulmones.",
    "PM10 son partículas de 10 micras o menos; incluyen las PM2.5 y otras más gruesas.",
  ],
  oficial: [
    "No. Es una estación de bajo costo con sensor SDS011; no sustituye mediciones oficiales ni es un equipo certificado.",
  ],
  // Estacion SIMULADA: la primera linea de toda respuesta con datos lo dice; nunca "medicion real".
  simulacion: "SIMULACIÓN · datos inventados para demostración, no son mediciones de un sensor real.",
  oficialSimulada: [
    "No. Esta es una estación SIMULADA: sus datos son inventados para demostración y no los mide ningún sensor.",
  ],
};

export const MIN_LECTURAS_HOY = 6;
const NOMBRE_CONTAMINANTE = { pm25: "PM2.5", pm10: "PM10" };
const TZ = "America/Bogota";
const fmtFecha = new Intl.DateTimeFormat("es-CO", {
  timeZone: TZ, day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false,
});

const aqValida = (aq) => !!(aq && aq.estado === "ok" && aq.ica && aq.ica.general);
const recValida = (aq) => aqValida(aq) && aq.recomendacion && typeof aq.recomendacion.efectos === "string";
const fuente = (f) => (f && f.documento ? `Fuente: ${f.documento} · pág. ${f.pagina}` : "");

// datos: {aq, ultima, historia, simulado}; ahora: ms o Date. -> {titulo, lineas: [texto], fuente: texto}
export function responder(id, datos = {}, ahora = Date.now()) {
  const r = responderBase(id, datos, ahora);
  if (!(datos && datos.simulado)) return r;
  if (id === "oficial") return { ...r, lineas: [...TEXTOS.oficialSimulada] };
  if (id === "pm25") return r;                                   // texto general, no habla de la estacion
  return { ...r, lineas: [TEXTOS.simulacion, ...r.lineas.filter((l) => l !== TEXTOS.simulados)] };
}

function responderBase(id, datos, ahora) {
  const p = TEXTOS.preguntas.find((x) => x.id === id);
  const r = { titulo: p ? p.etiqueta : "", lineas: [], fuente: "" };
  const { aq = null, ultima = null, historia = null } = datos || {};
  switch (id) {
    case "ahora": {
      const v = vistaAhora(ultima, ahora);
      if (!ultima || v.minutos == null) {
        r.lineas = [TEXTOS.sinDatos, TEXTOS.sinMediciones];
      } else if (v.antigua) {
        // Mas de 10 min sin datos: nunca se presenta como medicion actual.
        const t = fmtFecha.format(new Date(ultima.received_at));
        r.lineas = [`${TEXTOS.sinDatos} · última lectura ${haceCuanto(v.minutos)} (${t}, hora de Colombia).`];
      } else {
        r.lineas = [`PM2.5: ${v.pm25} µg/m³ · PM10: ${v.pm10} µg/m³ (última lectura ${haceCuanto(v.minutos)}).`];
        if (aqValida(aq)) {
          const g = aq.ica.general;
          r.lineas.push(`Calidad del aire: ${g.category_name} (ICA ${g.ica}; la define ` +
            `${NOMBRE_CONTAMINANTE[g.definido_por] || g.definido_por}, media de la última hora).`);
        } else {
          r.lineas.push(TEXTOS.icaNoDisponible);
        }
      }
      return r;
    }
    case "color": {
      if (!recValida(aq)) { r.lineas = [TEXTOS.colorSinDatos]; return r; }
      const g = aq.ica.general, rec = aq.recomendacion;
      r.lineas = [`${rec.nombre || g.category_name}${g.color ? ` · color ${g.color}` : ""}.`, rec.efectos];
      r.fuente = fuente(rec.fuentes_por_bloque && rec.fuentes_por_bloque.efectos);
      return r;
    }
    case "hacer": {
      if (!recValida(aq)) { r.lineas = [TEXTOS.sinDatosRecomendar]; return r; }
      const rec = aq.recomendacion;
      const acciones = Array.isArray(rec.acciones) && rec.acciones.length ? rec.acciones : [rec.resumen].filter(Boolean);
      r.lineas = [...acciones, TEXTOS.etiquetaFija];
      const f = rec.fuentes_por_bloque && rec.fuentes_por_bloque.acciones;
      r.fuente = fuente(f) + (f && /EPA/.test(f.documento) ? ` (${TEXTOS.orientativo})` : "");
      return r;
    }
    case "hoy": {
      const vals = (historia && Array.isArray(historia.puntos) ? historia.puntos : [])
        .map((x) => x && x.pm25).filter((x) => typeof x === "number" && Number.isFinite(x));
      if (!vals.length) { r.lineas = [TEXTOS.sinDatos]; return r; }
      if (vals.length < MIN_LECTURAS_HOY) {
        r.lineas = [`${TEXTOS.datosInsuficientes} (${vals.length} de ${MIN_LECTURAS_HOY} intervalos de 5 min como mínimo).`];
        return r;
      }
      const prom = vals.reduce((a, b) => a + b, 0) / vals.length;
      r.lineas = [
        `PM2.5 en las últimas 24 h: mínimo ${numero(Math.min(...vals), 1)}, máximo ${numero(Math.max(...vals), 1)} ` +
        `y promedio ${numero(prom, 1)} µg/m³ (${vals.length} intervalos de 5 min).`,
        TEXTOS.promedioNota,
      ];
      if (historia.is_simulated) r.lineas.push(TEXTOS.simulados);
      return r;
    }
    case "pm25": r.lineas = [...TEXTOS.pm25]; return r;
    case "oficial": r.lineas = [...TEXTOS.oficial]; return r;
    default: return r;
  }
}
