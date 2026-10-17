"""Entrena y evalua prediccion_pm25_v3 (EXPERIMENTAL): variables RELATIVAS al nivel, PM2.5 horario
externo de OpenAQ. Solo el CSV local (ninguna base). NO sobrescribe v1 ni v2 y NO activa v3.

Objetivo: eliminar el sesgo de regresion a la media de v2 (variables de nivel absoluto).

Diseno DECLARADO ANTES de ver resultados (no se cambia despues de mirar la prueba):
- c = 2.0 ug/m3 fija (no se afina).
- Variables: lr_h2_h1 = log((h2+c)/(h1+c)), lr_h3_h1 = log((h3+c)/(h1+c)),
  lr_m3_h1 = log((media_3h+c)/(h1+c)), pend_rel = (h1-h3)/(media_3h+c), hora_sin, hora_cos
  (hora de Colombia). h1, h2, h3 = medias horarias mas recientes; ver shared/prediccion.py.
- Objetivo: log((y+c)/(h1+c)). Prediccion: (h1+c)*exp(f) - c, nunca menor que 0.
- Mismo CSV, mismas muestras y misma division cronologica que v2 (ai/entrenar_prediccion_v2.py).
  Rejilla de alpha fija ALPHAS; alpha = menor MAE (ug/m3) en la VALIDACION. Ajuste final solo con
  entrenamiento.
- Criterio de sesgo (series constantes de 3, 6, 12 y 25 ug/m3 a las 03, 08, 13 y 20 h de Colombia):
  para los niveles 3 y 6, |pred - valor| <= 1.0 a 1 h y 3 h, y <= 1.5 a 6 h. Se reporta tal cual.
- Invariancia de escala: (pred + c)/(valor + c) igual para todos los niveles (misma hora y horizonte).
- Visible (horizontes_visibles) solo si en la prueba mejora el MAE de la persistencia en >= 5 % Y
  cumple el criterio de sesgo.
- La prueba NO es virgen: ya se miro una vez con v2.

  python ai/entrenar_prediccion_v3.py
"""

import argparse
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import entrenar_prediccion_v2 as v2mod  # noqa: E402
from entrenar_prediccion import ajustar_ridge, errores  # noqa: E402
from shared import prediccion as pr  # noqa: E402

NOMBRE = "prediccion_pm25_v3"
SALIDA = RAIZ / "modelos" / f"{NOMBRE}.json"
PROTEGIDOS = (RAIZ / "modelos" / "prediccion_pm25_v1.json", RAIZ / "modelos" / "prediccion_pm25_v2.json")
V2 = PROTEGIDOS[1]

C = 2.0
ALPHAS = [0.01, 0.1, 1.0, 10.0, 100.0, 1000.0, 10000.0]
HORIZONTES_H = (1, 3, 6)
NIVELES = (3.0, 6.0, 12.0, 25.0)
HORAS_COLOMBIA = (3, 8, 13, 20)
NIVELES_CRITERIO = (3.0, 6.0)
TOLERANCIA = {60: 1.0, 180: 1.0, 360: 1.5}
MEJORA_MINIMA_PCT = 5.0
AVISO = v2mod.AVISO
TEXTO_FUENTES = "Modelo entrenado con datos externos (OpenAQ, estación MED-UNNV El Volador, Medellín). Experimental."
NOTA_PRUEBA = ("El conjunto de prueba (2019-12 a 2020-02) ya se habia mirado una vez con prediccion_pm25_v2: "
               "no es una prueba virgen.")


# ---------- Muestras (mismas condiciones que v2) ----------

def muestras(serie, nombre, horizonte, c=C):
    """X relativas, objetivo log, y real, persistencia, hora del objetivo y X horarias (para v2)."""
    X, ylog, y, P, H, Xh = [], [], [], [], [], []
    for t in sorted(serie):
        obj = t + horizonte
        if obj not in serie or v2mod.conjunto_de(t - 2) != nombre or v2mod.conjunto_de(obj) != nombre:
            continue
        if not all(k in serie for k in (t, t - 1, t - 2)):
            continue
        x = pr.variables_relativas_desde(serie[t], serie[t - 1], serie[t - 2], t * 60, c)
        X.append([x[v] for v in pr.VARIABLES_RELATIVAS])
        Xh.append(x)
        ylog.append(math.log((serie[obj] + c) / (serie[t] + c)))
        y.append(serie[obj])
        P.append(serie[t])
        H.append(v2mod.hora_colombia(obj))
    return (np.array(X).reshape(-1, len(pr.VARIABLES_RELATIVAS)), np.array(ylog), np.array(y),
            np.array(P), np.array(H, dtype=int), Xh)


def predecir(Z, P, b, coef, c=C):
    return np.maximum(0.0, (P + c) * np.exp(b + Z @ coef) - c)


# ---------- Sesgo con series constantes (con shared/prediccion.py, como en vivo) ----------

def sesgo_constantes(modelo):
    """{horizonte: {hora: {nivel: pred - nivel}}} con una serie por minuto constante de 3 h."""
    base = datetime(2026, 11, 5, 0, 0, tzinfo=timezone.utc)
    out = {}
    for h in pr.horizontes_de(modelo):
        out[str(h)] = {}
        for hc in HORAS_COLOMBIA:
            ahora = base + timedelta(hours=hc + 5)
            fila = {}
            for nivel in NIVELES:
                s = pr.serie_por_minuto([(ahora - timedelta(minutes=i), nivel) for i in range(180)])
                x = pr.variables_para(modelo, s, pr.minuto(ahora))
                fila[str(nivel)] = pr.predecir_horizonte(modelo, x, h) - nivel
            out[str(h)][str(hc)] = fila
    return out


def cumple_sesgo(sesgo, horizonte):
    tol = TOLERANCIA[horizonte]
    return all(abs(sesgo[str(horizonte)][str(hc)][str(n)]) <= tol for hc in HORAS_COLOMBIA for n in NIVELES_CRITERIO)


def invariancia_escala(sesgo, horizonte, c=C, tol=1e-9):
    """(pred + c)/(nivel + c) igual en todos los niveles para cada hora."""
    for hc in HORAS_COLOMBIA:
        r = [(sesgo[str(horizonte)][str(hc)][str(n)] + n + c) / (n + c) for n in NIVELES]
        if max(r) - min(r) > tol:
            return False
    return True


# ---------- Entrenamiento y evaluacion ----------

def entrenar_y_evaluar(serie, v2=None):
    base = [pr.variables_relativas_desde(serie[t], serie[t - 1], serie[t - 2], t * 60, C)
            for t in sorted(serie) if all(k in serie for k in (t, t - 1, t - 2))
            and v2mod.conjunto_de(t - 2) == v2mod.conjunto_de(t) == "entrenamiento"]
    Xb = np.array([[x[v] for v in pr.VARIABLES_RELATIVAS] for x in base])
    if len(Xb) < 200:
        raise ValueError(f"datos de entrenamiento insuficientes ({len(Xb)} ventanas)")
    media, escala = Xb.mean(axis=0), Xb.std(axis=0)
    escala[escala == 0] = 1.0
    tabla = v2mod.media_por_hora(serie)
    horizontes, n_muestras, crudas = {}, {}, {}
    for hh in HORIZONTES_H:
        tr, va, te = (muestras(serie, c, hh) for c in ("entrenamiento", "validacion", "prueba"))
        n_muestras[str(hh * 60)] = {"entrenamiento": len(tr[2]), "validacion": len(va[2]), "prueba": len(te[2])}
        if len(tr[2]) < 200 or len(va[2]) < 50 or len(te[2]) < 50:
            raise ValueError(f"muestras insuficientes a {hh} h: {n_muestras[str(hh * 60)]}")
        Ztr, Zva, Zte = ((X - media) / escala for X in (tr[0], va[0], te[0]))
        mae_val = {}
        for a in ALPHAS:
            b, coef = ajustar_ridge(Ztr, tr[1], a)
            mae_val[a] = errores(va[2], predecir(Zva, va[3], b, coef))[0]
        mejor = min(ALPHAS, key=lambda a: mae_val[a])
        b, coef = ajustar_ridge(Ztr, tr[1], mejor)
        pred = predecir(Zte, te[3], b, coef)
        mae_m, rmse_m = errores(te[2], pred)
        mae_p, rmse_p = errores(te[2], te[3])
        mae_h, rmse_h = errores(te[2], np.array([tabla[k] for k in te[4]]))
        met = {"conjunto": "prueba", "n_prueba": int(len(te[2])),
               "mae_modelo": round(mae_m, 3), "rmse_modelo": round(rmse_m, 3),
               "mae_persistencia": round(mae_p, 3), "rmse_persistencia": round(rmse_p, 3),
               "mae_media_horaria": round(mae_h, 3), "rmse_media_horaria": round(rmse_h, 3),
               "mejora_mae_pct": round(100 * (mae_p - mae_m) / mae_p, 1) if mae_p else None,
               "mejora_rmse_pct": round(100 * (rmse_p - rmse_m) / rmse_p, 1) if rmse_p else None}
        if v2 is not None:   # v2 sobre exactamente las mismas muestras de prueba
            p2 = np.array([pr.predecir_horizonte(v2, x, hh * 60) for x in te[5]])
            mae2, rmse2 = errores(te[2], p2)
            met.update({"mae_v2": round(mae2, 3), "rmse_v2": round(rmse2, 3)})
        horizontes[str(hh * 60)] = {
            "intercepto": float(b), "coef": [float(v) for v in coef], "alpha": mejor,
            "mae_validacion_por_alpha": {str(a): round(v, 4) for a, v in mae_val.items()},
            "metricas": met, "supera_persistencia": bool(mae_m < mae_p),
        }
        crudas[hh * 60] = (mae_m, mae_p)
    return media, escala, horizontes, n_muestras, crudas


def construir_modelo(serie, info, ruta_entrada, huella, v2=None):
    media, escala, horizontes, n_muestras, crudas = entrenar_y_evaluar(serie, v2)
    unico = lambda c: next(iter(c)) if len(c) == 1 else sorted(c)  # noqa: E731
    modelo = {
        "nombre": NOMBRE,
        "tipo": "ridge_sobre_log_razon_a_la_persistencia",
        "resolucion": "hora",
        "transformacion": "relativa",
        "c": C,
        "persistencia": "pm25_h1",
        "experimental": True,
        "creado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "advertencia": "Prediccion experimental. " + AVISO,
        "aviso_publico": AVISO,
        "texto_fuentes": TEXTO_FUENTES,
        "variables": pr.VARIABLES_RELATIVAS,
        "media": [float(v) for v in media],
        "escala": [float(v) for v in escala],
        "horizontes": horizontes,
        "linea_base": "persistencia: media de PM2.5 de la ultima hora (pm25_h1)",
        "rival_adicional": "media por hora del dia (hora de Colombia), solo con el entrenamiento",
        "objetivo": "log((y + c)/(pm25_h1 + c)), y = media horaria que termina en ahora + horizonte",
        "fuentes": [{
            "tipo": "externos",
            "fuente": unico(info["fuentes"]), "estacion": unico(info["estaciones"]),
            "ciudad": unico(info["ciudades"]), "licencia": unico(info["licencias"]),
            "proveedor_archivo": "OpenAQ (location_id 10641, MED-UNNV)",
            "archivo": ruta_entrada.name, "sha256": huella,
            "filas_csv": info["filas"], "horas_con_datos": len(serie),
            "detalle": "CSV local de tools/convertir_openaq.py; ninguna base de datos",
        }],
        "conjuntos": {c: v2mod.rango(serie, c) for c in v2mod.CONJUNTOS},
        "prueba_no_virgen": NOTA_PRUEBA,
        "muestras_por_horizonte": n_muestras,
        "hiperparametros": {"c": C, "alphas": ALPHAS, "horizontes_h": list(HORIZONTES_H),
                            "seleccion_alpha": "menor MAE (ug/m3) en validacion", "ajuste_final": "solo entrenamiento",
                            "regla_disponible": "MAE del modelo < MAE de la persistencia en la prueba",
                            "regla_visible": (f"mejora del MAE >= {MEJORA_MINIMA_PCT} % sobre la persistencia en la "
                                              "prueba Y criterio de sesgo cumplido")},
        "criterio_sesgo": {"niveles": list(NIVELES_CRITERIO), "horas_colombia": list(HORAS_COLOMBIA),
                           "tolerancia_ug_m3": {str(k): v for k, v in TOLERANCIA.items()}},
        "supuestos": v2mod.SUPUESTOS,
        "sin_variables": "No usa temperatura ni humedad (la estacion externa solo trae PM2.5).",
    }
    sesgo = sesgo_constantes(modelo)
    visibles = []
    for h, d in horizontes.items():
        hm = int(h)
        mae_m, mae_p = crudas[hm]
        mejora = 100 * (mae_p - mae_m) / mae_p if mae_p else float("-inf")
        d["sesgo_series_constantes"] = {hc: {n: round(v, 3) for n, v in f.items()} for hc, f in sesgo[h].items()}
        d["cumple_sesgo"] = cumple_sesgo(sesgo, hm)
        d["invariancia_escala"] = invariancia_escala(sesgo, hm)
        if mejora >= MEJORA_MINIMA_PCT and d["cumple_sesgo"]:
            visibles.append(hm)
    modelo["horizontes_visibles"] = visibles
    return modelo


def main(argv=None):
    p = argparse.ArgumentParser(description="Entrena prediccion_pm25_v3 (variables relativas, CSV local)")
    p.add_argument("--entrada", type=Path, default=v2mod.ENTRADA)
    p.add_argument("--salida", type=Path, default=SALIDA)
    p.add_argument("--v2", type=Path, default=V2, help="JSON de v2 para compararlo en las mismas muestras")
    args = p.parse_args(argv)
    if any(args.salida.resolve() == x.resolve() for x in PROTEGIDOS):
        print("ERROR: no se sobrescriben prediccion_pm25_v1.json ni prediccion_pm25_v2.json")
        return 2
    try:
        with open(args.entrada, encoding="utf-8", newline="") as f:
            serie, info = v2mod.leer_csv(f)
        v2 = json.loads(args.v2.read_text(encoding="utf-8")) if args.v2 and args.v2.is_file() else None
        modelo = construir_modelo(serie, info, args.entrada, v2mod.sha256(args.entrada), v2)
    except (ValueError, OSError, KeyError) as e:
        print(f"ERROR: {e}")
        return 2
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_text(json.dumps(modelo, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Entrada: {args.entrada.name} · {len(serie)} horas · c = {C}")
    print(f"{'horiz.':<7}{'n':>6}{'MAE v3':>8}{'MAE v2':>8}{'MAE pers':>9}{'MAE hora':>9}"
          f"{'RMSE v3':>9}{'RMSE v2':>9}{'RMSE pers':>10}{'RMSE hora':>10}{'mejora':>8}  alpha  sesgo  escala")
    for h, d in modelo["horizontes"].items():
        m = d["metricas"]
        print(f"{int(h) // 60} h{'':<4}{m['n_prueba']:>6}{m['mae_modelo']:>8.3f}{m.get('mae_v2', float('nan')):>8.3f}"
              f"{m['mae_persistencia']:>9.3f}{m['mae_media_horaria']:>9.3f}{m['rmse_modelo']:>9.3f}"
              f"{m.get('rmse_v2', float('nan')):>9.3f}{m['rmse_persistencia']:>10.3f}{m['rmse_media_horaria']:>10.3f}"
              f"{str(m['mejora_mae_pct']) + '%':>8}  {d['alpha']:<6g} {'cumple' if d['cumple_sesgo'] else 'NO':<6} "
              f"{'si' if d['invariancia_escala'] else 'NO'}")
    print(f"horizontes_visibles: {modelo['horizontes_visibles']}\n{NOTA_PRUEBA}\nModelo escrito en {args.salida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
