// Logica PURA de la franja del proyecto (pie del panel): URL y codigo QR. El QR se genera aqui, en el
// navegador, con qrcode-generator 2.0.4 (MIT) servido desde /vendor/: ningun servicio externo.
import qrcode from "../vendor/qrcode-generator-2.0.4/qrcode.mjs";

export const MARGEN_QR = 4;   // zona de silencio del estandar QR (4 modulos)

// /proyecto -> {url, fuente}. Sin BASE_URL valida se usa el origen con el que se abrio el panel.
export function urlQr(info, origen) {
  if (info && typeof info.url_panel === "string" && /^https?:\/\//.test(info.url_panel)) {
    return { url: info.url_panel, fuente: "BASE_URL" };
  }
  return { url: `${String(origen || "").replace(/\/+$/, "")}/panel`, fuente: "origen" };
}

// Texto -> matriz de booleanos (true = modulo oscuro). Correccion de errores M (15 %).
export function matrizQr(texto) {
  const qr = qrcode(0, "M");
  qr.addData(String(texto));
  qr.make();
  const n = qr.getModuleCount();
  return Array.from({ length: n }, (_, f) => Array.from({ length: n }, (_, c) => qr.isDark(f, c)));
}

// Matriz -> {d, lado}: un path SVG con un cuadrado por modulo oscuro, desplazado por la zona de silencio.
export function pathQr(matriz, margen = MARGEN_QR) {
  let d = "";
  matriz.forEach((fila, f) => fila.forEach((oscuro, c) => {
    if (oscuro) d += `M${c + margen} ${f + margen}h1v1h-1z`;
  }));
  return { d, lado: matriz.length + 2 * margen };
}
