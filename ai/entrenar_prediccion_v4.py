"""Entrena y evalua prediccion_pm25_v4 (EXPERIMENTAL): PM2.5 a 3, 4 y 5 h con variables RELATIVAS
al nivel, entrenado con PM2.5 horario del IDEAM (datos.gov.co, 3 estaciones del AMVA, 2020-2025).

Datos: los mismos del importador (tools/importar_ideam.py: descarga Socrata + conversion + limpieza
de ceros exactos y rachas de 6+ horas), todo en memoria. NO lee ninguna base de datos ni guarda
archivos de datos. NO sobrescribe v1, v2 ni v3.

Diseno DECLARADO ANTES de ver resultados (no se cambia despues de mirar la prueba):
- Estaciones: 29509, 29493, 31854 (IDEAM). Un solo modelo para las 3. 31366 (Biblioteca Fernando B.)
  EXCLUIDA: de 2020-01 a 2023-12 sus valores son incoherentes (medianas mensuales 97-1955 ug/m3
  frente a 15-44 de Trafico Centro; 12.517 valores > 1000) tras la actualizacion del IDEAM del
  2026-10-24; no se usa ni en entrenamiento ni en prueba (decision del equipo). Sus filas no se tocan.
- c = 2.0 fija. Variables (shared/prediccion.py, variables_v4_desde): lr_h2..lr_h6, lr_m6, lr_m24,
  pend_rel6, hora_sin/cos y dia_sin/cos (hora de Colombia; festivos NO distinguidos: limitacion).
- Ventana: h1..h6 completas y media de 24 h con >= 18 de 24 horas. Sin imputar.
- Objetivo: log((y+c)/(h1+c)), y = media de la hora que termina en T+h. Prediccion (h1+c)*exp(f) - c >= 0.
- Horizontes 3, 4 y 5 h. Division cronologica por mes UTC; ventana (24 h) y objetivo en el MISMO
  conjunto: entrenamiento 2020-01..2023-06 · validacion 2023-07..2023-12 (solo alpha) ·
  prueba A = 2024 · prueba B = 2025 (solo IDEAM). Ambas se evaluan UNA vez con el modelo ya fijado y
  se reportan por separado; ninguna se uso con v2 ni v3.
- Alpha de ALPHAS por menor MAE (ug/m3) en validacion; ajuste final solo con entrenamiento.
- Rivales en la prueba: persistencia (h1) y media por hora del dia de CADA estacion (solo entrenamiento).
- Criterio de sesgo (series constantes 3 y 6 ug/m3, 03/08/13/20 h, un miercoles y un domingo):
  |pred - valor| <= 1.0 a 3 h y <= 1.5 a 4 y 5 h.
- Visible (horizontes_visibles): mejora del MAE >= 5 % sobre la persistencia en la PRUEBA A (la declarada
  primero) Y sesgo cumplido. La prueba B se reporta aparte y no cambia esa regla.

  python ai/entrenar_prediccion_v4.py
"""

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).resolve().parent.parent
for p in (RAIZ, Path(__file__).resolve().parent, RAIZ / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from entrenar_prediccion import ajustar_ridge, errores  # noqa: E402
from shared import prediccion as pr  # noqa: E402

NOMBRE = "prediccion_pm25_v4"
SALIDA = RAIZ / "modelos" / f"{NOMBRE}.json"
PROTEGIDOS = tuple(RAIZ / "modelos" / f"prediccion_pm25_v{i}.json" for i in (1, 2, 3))

ESTACIONES = ("29509", "29493", "31854")
EXCLUIDAS = {"31366": ("Biblioteca Fernando B.: de 2020-01 a 2023-12 valores incoherentes (medianas mensuales "
                       "97-1955 ug/m3 frente a 15-44 de Trafico Centro; 12.517 valores > 1000) en el conjunto "
                       "del IDEAM actualizado el 2026-10-24. Excluida de entrenamiento y pruebas; filas sin tocar.")}
DESDE, HASTA = date(2020, 1, 1), date(2025, 12, 31)
C = 2.0
ALPHAS = [0.01, 0.1, 1.0, 10.0, 100.0, 1000.0, 10000.0]
HORIZONTES_H = (3, 4, 5)
CONJUNTOS = {"entrenamiento": ("2020-01", "2023-06"), "validacion": ("2023-07", "2023-12"),
             "prueba_a": ("2024-01", "2024-12"), "prueba_b": ("2025-01", "2025-12")}
PRUEBAS = ("prueba_a", "prueba_b")
NIVELES = (3.0, 6.0, 12.0, 25.0)
NIVELES_CRITERIO = (3.0, 6.0)
HORAS_COLOMBIA = (3, 8, 13, 20)
DIAS_SESGO = {"miercoles": date(2026, 11, 4), "domingo": date(2026, 11, 8)}
TOLERANCIA = {180: 1.0, 240: 1.5, 300: 1.5}
MEJORA_MINIMA_PCT = 5.0
AVISO = ("Entrenado con datos externos históricos del IDEAM (3 estaciones del Valle de Aburrá, 2020-2023; "
         "evaluado en 2024 y 2025); "
         "no representa la precisión con el sensor propio.")
TEXTO_FUENTES = ("Modelo entrenado con datos externos (IDEAM, datos.gov.co, estaciones del AMVA). "
                 "Experimental.")
LIMITACIONES = [
    "Los festivos de Colombia no se distinguen: el dia de la semana es la unica variable de calendario.",
    "Estaciones oficiales del AMVA; el sensor propio (SDS011, bajo costo) mide distinto.",
    "Valor del IDEAM en T = media de la hora que termina en T, horas en hora de Colombia (supuesto del importador).",
    "Limpieza aplicada: ceros exactos y rachas de 6+ horas con el mismo valor descartados.",
]


# ---------- Datos ----------

def mes(h):
    return datetime.fromtimestamp(h * 3600, tz=timezone.utc).strftime("%Y-%m")


def conjunto_de(h):
    m = mes(h)
    for nombre, (ini, fin) in CONJUNTOS.items():
        if ini <= m <= fin:
            return nombre
    return None


def hora_colombia(h):
    return datetime.fromtimestamp(h * 3600, tz=pr.COLOMBIA).hour


def cargar_series(abrir=None):
    """{station: {hora_utc (horas desde 1970): pm25}} y la procedencia, desde la API del IDEAM."""
    import importar_ideam as ii
    where = ii.filtro_soql(DESDE, HASTA, list(ESTACIONES))
    filas, res = ii.aplicar_limpieza(*ii.convertir(ii.descargar(where, abrir=abrir), DESDE, HASTA))
    series = defaultdict(dict)
    for f in filas:
        series[f["station"]][int(f["measured_at"].timestamp() // 3600)] = float(f["pm25"])
    huella = hashlib.sha256()
    for st in sorted(series):
        for h in sorted(series[st]):
            huella.update(f"{st}|{h}|{series[st][h]:.1f}\n".encode())
    info = {"fuente": ii.SOURCE, "licencia": ii.LICENCIA, "api": ii.API, "filtro_soql": where,
            "conteos_importador": res["conteos"], "conteos_limpieza": res["limpieza"]["conteos"],
            "sha256_datos": huella.hexdigest()}
    return dict(series), info


def entrada_en(serie, t):
    """Variables v4 en la hora t (fin de la hora t = 'ahora'), o None si la ventana no esta completa."""
    horas = [serie.get(t - k) for k in range(pr.V4_HORAS_MEDIA)]
    return pr.variables_v4_desde(horas, t * 60, C)


def muestras(series, nombre, horizonte):
    """X, objetivo log, y real, persistencia, hora Colombia del objetivo, estacion y variables (dict)."""
    X, ylog, y, P, H, E, XS = [], [], [], [], [], [], []
    for st in sorted(series):
        serie = series[st]
        for t in sorted(serie):
            obj = t + horizonte
            if obj not in serie or conjunto_de(t - (pr.V4_HORAS_MEDIA - 1)) != nombre or conjunto_de(obj) != nombre:
                continue
            x = entrada_en(serie, t)
            if x is None:
                continue
            X.append([x[v] for v in pr.VARIABLES_V4])
            ylog.append(math.log((serie[obj] + C) / (serie[t] + C)))
            y.append(serie[obj])
            P.append(serie[t])
            H.append(hora_colombia(obj))
            E.append(st)
            XS.append(x)
    return (np.array(X).reshape(-1, len(pr.VARIABLES_V4)), np.array(ylog), np.array(y), np.array(P),
            np.array(H, dtype=int), E, XS)


def media_por_hora(series):
    """{station: {hora Colombia: media}} SOLO con horas del entrenamiento."""
    out = {}
    for st, serie in series.items():
        vals = defaultdict(list)
        for h, v in serie.items():
            if conjunto_de(h) == "entrenamiento":
                vals[hora_colombia(h)].append(v)
        out[st] = {k: float(np.mean(v)) for k, v in sorted(vals.items())}
    return out


def rango(series, nombre):
    hs = [h for s in series.values() for h in s if conjunto_de(h) == nombre]
    f = lambda h: datetime.fromtimestamp(h * 3600, tz=timezone.utc).isoformat()  # noqa: E731
    return {"meses_utc": list(CONJUNTOS[nombre]), "primera_hora_utc": f(min(hs)) if hs else None,
            "ultima_hora_utc": f(max(hs)) if hs else None, "horas_con_datos": len(hs)}


def predecir(Z, P, b, coef):
    return np.maximum(0.0, (P + C) * np.exp(b + Z @ coef) - C)


# ---------- Sesgo con series constantes (con shared/prediccion.py, como en vivo) ----------

def sesgo_constantes(modelo):
    """{horizonte: {dia: {hora: {nivel: pred - nivel}}}} con una serie por minuto constante de 24 h."""
    out = {}
    for h in pr.horizontes_de(modelo):
        out[str(h)] = {}
        for dia, fecha in DIAS_SESGO.items():
            out[str(h)][dia] = {}
            for hc in HORAS_COLOMBIA:
                ahora = datetime(fecha.year, fecha.month, fecha.day, hc, tzinfo=pr.COLOMBIA).astimezone(timezone.utc)
                fila = {}
                for nivel in NIVELES:
                    s = pr.serie_por_minuto([(ahora - timedelta(minutes=i), nivel) for i in range(24 * 60)])
                    x = pr.variables_para(modelo, s, pr.minuto(ahora))
                    fila[str(nivel)] = round(pr.predecir_horizonte(modelo, x, h) - nivel, 3)
                out[str(h)][dia][str(hc)] = fila
    return out


def cumple_sesgo(sesgo, horizonte):
    tol = TOLERANCIA[horizonte]
    return all(abs(v[str(n)]) <= tol for dia in sesgo[str(horizonte)].values()
               for v in dia.values() for n in NIVELES_CRITERIO)


# ---------- Entrenamiento y evaluacion ----------

def evaluar(te, Zte, b, coef, tabla, nombre):
    """Metricas (redondeadas) en una prueba + (mae_modelo, mae_persistencia) sin redondear."""
    pred = predecir(Zte, te[3], b, coef)
    rival = np.array([tabla[e].get(int(hc), np.nan) for e, hc in zip(te[5], te[4])])
    mae_m, rmse_m = errores(te[2], pred)
    mae_p, rmse_p = errores(te[2], te[3])
    mae_h, rmse_h = errores(te[2], rival)
    por_est = {}
    for st in sorted(set(te[5])):
        idx = np.array([e == st for e in te[5]])
        em, rm = errores(te[2][idx], pred[idx])
        ep, rp = errores(te[2][idx], te[3][idx])
        eh, rh = errores(te[2][idx], rival[idx])
        por_est[st] = {"n": int(idx.sum()), "mae_modelo": round(em, 3), "mae_persistencia": round(ep, 3),
                       "mae_media_horaria": round(eh, 3), "rmse_modelo": round(rm, 3),
                       "rmse_persistencia": round(rp, 3), "rmse_media_horaria": round(rh, 3)}
    met = {"conjunto": nombre, "meses_utc": list(CONJUNTOS[nombre]), "n_prueba": int(len(te[2])),
           "mae_modelo": round(mae_m, 3), "rmse_modelo": round(rmse_m, 3),
           "mae_persistencia": round(mae_p, 3), "rmse_persistencia": round(rmse_p, 3),
           "mae_media_horaria": round(mae_h, 3), "rmse_media_horaria": round(rmse_h, 3),
           "mejora_mae_pct": round(100 * (mae_p - mae_m) / mae_p, 1) if mae_p else None,
           "mejora_rmse_pct": round(100 * (rmse_p - rmse_m) / rmse_p, 1) if rmse_p else None,
           "por_estacion": por_est}
    return met, mae_m, mae_p


def entrenar_y_evaluar(series):
    base = [entrada_en(s, t) for s in series.values() for t in sorted(s)
            if conjunto_de(t - (pr.V4_HORAS_MEDIA - 1)) == conjunto_de(t) == "entrenamiento"]
    Xb = np.array([[x[v] for v in pr.VARIABLES_V4] for x in base if x is not None])
    if len(Xb) < 500:
        raise ValueError(f"datos de entrenamiento insuficientes ({len(Xb)} ventanas)")
    media, escala = Xb.mean(axis=0), Xb.std(axis=0)
    escala[escala == 0] = 1.0
    tabla = media_por_hora(series)
    horizontes, n_muestras, crudas = {}, {}, {}
    for hh in HORIZONTES_H:
        tr, va = (muestras(series, c, hh) for c in ("entrenamiento", "validacion"))
        pruebas = {c: muestras(series, c, hh) for c in PRUEBAS}
        n_muestras[str(hh * 60)] = {"entrenamiento": len(tr[2]), "validacion": len(va[2]),
                                    **{c: len(t[2]) for c, t in pruebas.items()}}
        if len(tr[2]) < 500 or len(va[2]) < 100 or any(len(t[2]) < 100 for t in pruebas.values()):
            raise ValueError(f"muestras insuficientes a {hh} h: {n_muestras[str(hh * 60)]}")
        Ztr, Zva = ((X - media) / escala for X in (tr[0], va[0]))
        mae_val = {}
        for a in ALPHAS:
            b, coef = ajustar_ridge(Ztr, tr[1], a)
            mae_val[a] = errores(va[2], predecir(Zva, va[3], b, coef))[0]
        mejor = min(ALPHAS, key=lambda a: mae_val[a])
        b, coef = ajustar_ridge(Ztr, tr[1], mejor)           # modelo FIJADO antes de mirar las pruebas
        evaluaciones = {c: evaluar(t, (t[0] - media) / escala, b, coef, tabla, c) for c, t in pruebas.items()}
        horizontes[str(hh * 60)] = {
            "intercepto": float(b), "coef": [float(v) for v in coef], "alpha": mejor,
            "mae_validacion_por_alpha": {str(a): round(v, 4) for a, v in mae_val.items()},
            "metricas": evaluaciones["prueba_a"][0],
            "metricas_prueba_b": evaluaciones["prueba_b"][0],
            "supera_persistencia": bool(evaluaciones["prueba_a"][1] < evaluaciones["prueba_a"][2]),
        }
        crudas[hh * 60] = evaluaciones["prueba_a"][1:]
    return media, escala, horizontes, n_muestras, crudas


def construir_modelo(series, info):
    media, escala, horizontes, n_muestras, crudas = entrenar_y_evaluar(series)
    modelo = {
        "nombre": NOMBRE,
        "tipo": "ridge_sobre_log_razon_a_la_persistencia",
        "resolucion": "hora",
        "transformacion": "relativa",
        "variables_familia": "v4",
        "c": C,
        "persistencia": "pm25_h1",
        "experimental": True,
        "creado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "advertencia": "Prediccion experimental. " + AVISO,
        "aviso_publico": AVISO,
        "texto_fuentes": TEXTO_FUENTES,
        "variables": pr.VARIABLES_V4,
        "media": [float(v) for v in media],
        "escala": [float(v) for v in escala],
        "horizontes": horizontes,
        "linea_base": "persistencia: media de PM2.5 de la ultima hora (pm25_h1)",
        "rival_adicional": "media por hora del dia (hora de Colombia) de cada estacion, solo con el entrenamiento",
        "objetivo": "log((y + c)/(pm25_h1 + c)), y = media horaria que termina en ahora + horizonte",
        "ventana_entrada": {"horas_lags": pr.V4_HORAS_LAGS, "horas_media": pr.V4_HORAS_MEDIA,
                            "min_horas_media": pr.V4_MIN_HORAS_MEDIA, "min_minutos_por_hora_en_vivo": 40},
        "fuentes": [{"tipo": "externos", "fuente": info["fuente"], "licencia": info["licencia"],
                     "estaciones": sorted(series), "codigos_ideam": list(ESTACIONES),
                     "estaciones_excluidas": EXCLUIDAS, "api": info["api"],
                     "filtro_soql": info["filtro_soql"], "sha256_datos": info["sha256_datos"],
                     "horas_con_datos": {st: len(s) for st, s in sorted(series.items())},
                     "conteos_importador": info["conteos_importador"],
                     "conteos_limpieza": info["conteos_limpieza"],
                     "detalle": "API Socrata leida en memoria con tools/importar_ideam.py; ninguna base de datos"}],
        "conjuntos": {c: rango(series, c) for c in CONJUNTOS},
        "muestras_por_horizonte": n_muestras,
        "hiperparametros": {"c": C, "alphas": ALPHAS, "horizontes_h": list(HORIZONTES_H),
                            "seleccion_alpha": "menor MAE (ug/m3) en validacion", "ajuste_final": "solo entrenamiento",
                            "regla_disponible": "MAE del modelo < MAE de la persistencia en la prueba",
                            "regla_visible": (f"mejora del MAE >= {MEJORA_MINIMA_PCT} % sobre la persistencia en la "
                                              "prueba A (2024) Y criterio de sesgo cumplido; la prueba B (2025) se "
                                              "reporta aparte"),
                            "pruebas": "A = 2024 y B = 2025, evaluadas una sola vez con el modelo fijado"},
        "criterio_sesgo": {"niveles": list(NIVELES_CRITERIO), "horas_colombia": list(HORAS_COLOMBIA),
                           "dias": {k: v.isoformat() for k, v in DIAS_SESGO.items()},
                           "tolerancia_ug_m3": {str(k): v for k, v in TOLERANCIA.items()}},
        "limitaciones": LIMITACIONES,
    }
    sesgo = sesgo_constantes(modelo)
    visibles = []
    for h, d in horizontes.items():
        hm = int(h)
        mae_m, mae_p = crudas[hm]
        mejora = 100 * (mae_p - mae_m) / mae_p if mae_p else float("-inf")
        d["sesgo_series_constantes"] = sesgo[h]
        d["cumple_sesgo"] = cumple_sesgo(sesgo, hm)
        if mejora >= MEJORA_MINIMA_PCT and d["cumple_sesgo"]:
            visibles.append(hm)
    modelo["horizontes_visibles"] = visibles
    return modelo


def imprimir(modelo, series):
    print(f"Estaciones: {', '.join(f'{st} ({len(s)} h)' for st, s in sorted(series.items()))}")
    cl = modelo["fuentes"][0]["conteos_limpieza"]
    print(f"Limpieza: {cl.get('ceros', 0)} ceros · {cl.get('rachas', 0)} rachas ({cl.get('horas_en_rachas', 0)} h)")
    for c, r in modelo["conjuntos"].items():
        print(f"  {c:<14} {r['primera_hora_utc']} -> {r['ultima_hora_utc']} · {r['horas_con_datos']} horas")
    for clave, titulo in (("metricas", "PRUEBA A (2024)"), ("metricas_prueba_b", "PRUEBA B (2025)")):
        print(f"\n{titulo}\n{'horiz.':<7}{'n':>7}{'MAE mod':>9}{'MAE pers':>10}{'MAE hora':>10}{'RMSE mod':>10}"
              f"{'RMSE pers':>11}{'RMSE hora':>11}{'mejora':>8}  alpha   sesgo")
        for h, d in modelo["horizontes"].items():
            m = d[clave]
            print(f"{int(h) // 60} h{'':<4}{m['n_prueba']:>7}{m['mae_modelo']:>9.3f}{m['mae_persistencia']:>10.3f}"
                  f"{m['mae_media_horaria']:>10.3f}{m['rmse_modelo']:>10.3f}{m['rmse_persistencia']:>11.3f}"
                  f"{m['rmse_media_horaria']:>11.3f}{str(m['mejora_mae_pct']) + '%':>8}  {d['alpha']:<7g} "
                  f"{'cumple' if d['cumple_sesgo'] else 'NO'}")
    print(f"horizontes_visibles: {modelo['horizontes_visibles']}")


def main(argv=None, series=None, info=None):
    p = argparse.ArgumentParser(description="Entrena prediccion_pm25_v4 (IDEAM, 3/4/5 h)")
    p.add_argument("--salida", type=Path, default=SALIDA)
    args = p.parse_args(argv)
    if any(args.salida.resolve() == x.resolve() for x in PROTEGIDOS):
        print("ERROR: no se sobrescriben prediccion_pm25_v1, v2 ni v3")
        return 2
    try:
        if series is None:
            series, info = cargar_series()
        modelo = construir_modelo(series, info)
    except (ValueError, OSError, KeyError) as e:
        print(f"ERROR: {e}")
        return 2
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_text(json.dumps(modelo, ensure_ascii=False, indent=2), encoding="utf-8")
    imprimir(modelo, series)
    print(f"\n{AVISO}\nModelo escrito en {args.salida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
