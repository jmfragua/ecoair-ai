// Tests del modo kiosco y de la franja del proyecto (logica pura).
import { test } from "node:test";
import assert from "node:assert/strict";
import { modos, textoSinDatos } from "../../static/js/modo-vista.js";
import { MARGEN_QR, matrizQr, pathQr, urlQr } from "../../static/js/proyecto-vista.js";

test("modos: kiosco solo, replay solo y combinados", () => {
  assert.deepEqual(modos("?device=ecoair-node-01&modo=kiosco"), { replay: false, kiosco: true });
  assert.deepEqual(modos("?modo=replay&station=29509"), { replay: true, kiosco: false });
  assert.deepEqual(modos("?modo=replay,kiosco"), { replay: true, kiosco: true });
  assert.deepEqual(modos("?modo=replay&modo=KIOSCO"), { replay: true, kiosco: true });
  assert.deepEqual(modos(""), { replay: false, kiosco: false });
});

test("estado del nodo: 'Sin conexión desde hace X' en kiosco", () => {
  assert.equal(textoSinDatos(1860, true), "Sin conexión desde hace 1 día 7 h");
  assert.equal(textoSinDatos(1860, false), "Sin datos hace 1 día 7 h");
  assert.equal(textoSinDatos(null, true), "Sin conexión");
});

test("URL del QR: BASE_URL valida o el origen del panel", () => {
  assert.deepEqual(urlQr({ url_panel: "https://ecoair.example.org/panel" }, "http://localhost:8000"),
    { url: "https://ecoair.example.org/panel", fuente: "BASE_URL" });
  assert.deepEqual(urlQr({ url_panel: null }, "http://192.168.1.200:8000/"), { url: "http://192.168.1.200:8000/panel", fuente: "origen" });
  assert.equal(urlQr({ url_panel: "javascript:x" }, "http://a").fuente, "origen");
});

test("QR: matriz cuadrada, patrones de posicion y path con zona de silencio", () => {
  const m = matrizQr("https://ecoair.example.org/panel");
  const n = m.length;
  assert.ok(n >= 21 && (n - 17) % 4 === 0 && m.every((f) => f.length === n));   // tamaño valido de QR
  for (const [f, c] of [[0, 0], [0, n - 7], [n - 7, 0]]) {                      // tres cuadros de 7 x 7
    assert.ok(m[f][c] && m[f + 6][c + 6] && !m[f + 1][c + 1] && m[f + 3][c + 3]);
  }
  assert.deepEqual(matrizQr("https://ecoair.example.org/panel"), m);             // determinista
  const { d, lado } = pathQr(m);
  assert.equal(lado, n + 2 * MARGEN_QR);
  assert.ok(d.startsWith(`M${MARGEN_QR} ${MARGEN_QR}h1v1h-1z`));
  assert.equal((d.match(/z/g) || []).length, m.flat().filter(Boolean).length);
});
