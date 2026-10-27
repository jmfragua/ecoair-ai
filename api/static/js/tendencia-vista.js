// Logica PURA de la tarjeta "Tendencia" (sin DOM). Sin datos -> estado vacio de una linea (sin ejes);
// el titulo nunca dice "ultimas 0,0 h": la cobertura parcial va solo en la nota.

const num = (v) => (typeof v === "number" && Number.isFinite(v)
  ? v.toLocaleString("es-CO", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) : "—");

// h: respuesta de /air-quality/history; nPuntos: puntos con datos -> {vacia, titulo, nota}
export function vistaTendencia(h, nPuntos) {
  const horas = (h && h.hours) || 24;
  const titulo = `Tendencia de las últimas ${horas} h`;
  if (!nPuntos) return { vacia: true, titulo, nota: `Sin datos en las últimas ${horas} h.` };
  const parcial = !!(h && h.parcial && h.horas_con_datos > 0);
  return {
    vacia: false, titulo,
    nota: parcial ? `Los datos cubren solo ${num(h.horas_con_datos)} h de las últimas ${horas} h; la gráfica muestra lo disponible.` : "",
  };
}
