// Portada de EcoAir AI: estado en vivo de la estacion principal. Sin librerias externas.
import { vistaEstado } from "./portada-vista.js";

const DEVICE = "ecoair-node-01";
const TIMEOUT_MS = 8000;
const REFRESH_MS = 30000;
// Colores de categoria del ICA: los mismos del panel (siempre acompanados del nombre).
const COLORES = { buena: "#3fb950", aceptable: "#e3c02b", danina_sensibles: "#f0883e",
                  danina: "#e5484d", muy_danina: "#a371f7", peligrosa: "#b9744f" };
const $ = (id) => document.getElementById(id);

async function getJSON(path) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    const r = await fetch(path, { signal: ctrl.signal, headers: { Accept: "application/json" } });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return await r.json();
  } finally {
    clearTimeout(t);
  }
}

function render(v) {
  const estado = $("estado");
  estado.className = `status status--${v.conexion === "en_linea" ? "ok" : v.conexion === "fuera" ? "stale" : "error"}`;
  $("estado-texto").textContent = v.textoConexion;
  $("ica-valor").textContent = v.ica == null ? "—" : String(v.ica);
  $("ica-categoria").textContent = v.categoria;
  $("ica-base").textContent = v.base;
  document.documentElement.style.setProperty("--cat", COLORES[v.color] || "#6b6a63");
  $("sim").hidden = !v.simulado;
}

async function actualizar() {
  const [aq, devs] = await Promise.allSettled([
    getJSON(`/devices/${encodeURIComponent(DEVICE)}/air-quality/current`),
    getJSON("/devices"),
  ]);
  const dispositivo = devs.status === "fulfilled" && Array.isArray(devs.value)
    ? devs.value.find((d) => d.device_id === DEVICE) || null : null;
  render(vistaEstado(aq.status === "fulfilled" ? aq.value : null, dispositivo));
}

actualizar().catch(() => render(vistaEstado(null, null)));
setInterval(() => actualizar().catch(() => render(vistaEstado(null, null))), REFRESH_MS);
