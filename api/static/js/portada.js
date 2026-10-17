// Portada de EcoAir AI: estado en vivo de la estacion principal. Sin librerias externas.
import { rotuloUltimaLectura, vistaEstado } from "./portada-vista.js";
import { vistaAhora } from "./ahora-vista.js";

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
    if (!r.ok) throw Object.assign(new Error(`HTTP ${r.status}`), { status: r.status });
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

// Miniatura PM2.5 / PM10 / temperatura / humedad. Si falla la consulta se conservan los ultimos valores;
// con mas de 10 min sin medir se avisa que el nodo puede estar apagado.
let ultimaMedicion = null;
function renderMini(fallo, estado) {
  const v = vistaAhora(ultimaMedicion, Date.now(), { fallo });
  const rotulo = rotuloUltimaLectura(estado, v);
  $("mini-titulo").textContent = rotulo;
  $("mini-titulo").hidden = !rotulo;
  $("mini-pm25").textContent = v.pm25;
  $("mini-pm10").textContent = v.pm10;
  $("mini-temp").textContent = v.temperatura;
  $("mini-hum").textContent = v.humedad;
  const aviso = $("mini-aviso");
  aviso.replaceChildren();
  if (v.aviso) aviso.append(v.aviso, Object.assign(document.createElement("span"), { textContent: v.ultima }));
  aviso.hidden = !v.aviso;
}

async function actualizar() {
  const dev = encodeURIComponent(DEVICE);
  const [aq, devs, latest] = await Promise.allSettled([
    getJSON(`/devices/${dev}/air-quality/current`),
    getJSON("/devices"),
    getJSON(`/devices/${dev}/measurements/latest`),
  ]);
  const dispositivo = devs.status === "fulfilled" && Array.isArray(devs.value)
    ? devs.value.find((d) => d.device_id === DEVICE) || null : null;
  const estado = vistaEstado(aq.status === "fulfilled" ? aq.value : null, dispositivo);
  render(estado);
  if (latest.status === "fulfilled") ultimaMedicion = latest.value;
  else if (latest.reason?.status === 404) ultimaMedicion = null; // aun no hay mediciones
  renderMini(latest.status === "rejected" && latest.reason?.status !== 404, estado);
}

function fallo() {
  const estado = vistaEstado(null, null);
  render(estado);
  renderMini(true, estado);
}
actualizar().catch(fallo);
setInterval(() => actualizar().catch(fallo), REFRESH_MS);
