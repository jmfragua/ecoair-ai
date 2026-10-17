// Logica PURA de la tarjeta "Recomendación" (sin DOM). Los textos y los titulos vienen tal cual de
// shared/recomendaciones.py a traves de la API: es la misma fuente que usan los correos.
// Texto fijo de la norma, no IA.

export const NO_DISPONIBLES = "Recomendaciones no disponibles por ahora";

// rec: objeto "recomendacion" de /air-quality/current, o null (sin datos / fallo).
// motivo: texto a mostrar cuando no hay recomendacion (p. ej. "Sin datos recientes").
export function vistaRecomendacion(rec, motivo = NO_DISPONIBLES) {
  const valida = rec && typeof rec === "object" && typeof rec.efectos === "string" && rec.titulos;
  if (!valida) {
    return { disponible: false, mensaje: motivo || NO_DISPONIBLES, efectos: "", resumen: "",
             secciones: [], fuentes: [], etiqueta: "" };
  }
  const t = rec.titulos;
  const g = rec.grupos_sensibles || {};
  const secciones = [
    { titulo: t.poblacion_general, parrafos: [rec.poblacion_general].filter(Boolean), lista: [] },
    { titulo: t.grupos_sensibles, nota: g.quienes || "", parrafos: [g.recomendacion].filter(Boolean), lista: [] },
    { titulo: t.acciones, parrafos: [], lista: Array.isArray(rec.acciones) ? rec.acciones : [] },
  ];
  if (rec.cuando_consultar) secciones.push({ titulo: t.cuando_consultar, parrafos: [rec.cuando_consultar], lista: [] });
  if (rec.si_sube) secciones.push({ titulo: t.si_sube, parrafos: [rec.si_sube], lista: [] });
  return {
    disponible: true,
    mensaje: "",
    tituloEfectos: t.efectos,
    efectos: rec.efectos,
    resumen: rec.resumen || rec.recomendacion || "",
    secciones: secciones.filter((s) => s.parrafos.length || s.lista.length),
    fuentes: (rec.fuente || []).map((f) => `${f.documento} · pág. ${f.pagina}`),
    etiqueta: rec.etiqueta || "Texto fijo de la norma, no IA",
  };
}
