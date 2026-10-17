"""Entrena y evalua prediccion_pm25_v2 (EXPERIMENTAL) con PM2.5 HORARIO externo de OpenAQ.

Entrada: CSV de tools/convertir_openaq.py (source,station,city,measured_at,pm25,license), solo el
archivo local: no lee ninguna base de datos. NO sobrescribe prediccion_pm25_v1.json.

Diseno DECLARADO ANTES de ver resultados (no se amplia despues):
- Variables (solo PM2.5 y tiempo; NO temperatura ni humedad, igual que v1): pm25_h1, pm25_h2,
  pm25_h3 (las 3 ultimas medias horarias), media_3h, pendiente_h1_h3, hora_sin/hora_cos (hora de
  Colombia, UTC-5). Ver shared/prediccion.py: en vivo se calculan de la serie por minuto de 3 h.
- Modelo: Ridge sobre el residuo de la persistencia (persistencia = pm25_h1), como v1.
- Supuesto: el valor de OpenAQ en la hora T es la media de la hora que TERMINA en T.
- Muestras: solo si existen T, T-1, T-2 (entrada) y T+h (objetivo). Sin imputar horas.
- Division cronologica por mes UTC; entrada y objetivo de cada muestra en el MISMO conjunto:
  entrenamiento 2018-11..2019-09 · validacion 2019-10..2019-11 (solo para elegir alpha) ·
  prueba 2019-12..2020-02 (se evalua una sola vez, al final).
- Horizontes: 1, 3 y 6 h. Alphas (lista fija): ALPHAS. Ajuste final solo con entrenamiento.
- Rivales en la prueba: persistencia y media por hora del dia (hora de Colombia del objetivo),
  calculada solo con el entrenamiento. Metricas: MAE y RMSE.
- Un horizonte es "disponible para mostrar" solo si su MAE en la prueba es MENOR que el de la
  persistencia (comparacion sin redondear).

  python ai/entrenar_prediccion_v2.py
"""

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from entrenar_prediccion import ajustar_ridge, errores  # noqa: E402
from shared.prediccion import COLOMBIA, VARIABLES_HORARIAS, variables_horarias_desde  # noqa: E402

NOMBRE = "prediccion_pm25_v2"
ENTRADA = RAIZ / "datos_externos" / "openaq_10641_pm25_utc.csv"
SALIDA = RAIZ / "modelos" / f"{NOMBRE}.json"
V1 = RAIZ / "modelos" / "prediccion_pm25_v1.json"

HORIZONTES_H = (1, 3, 6)
ALPHAS = [0.01, 0.1, 1.0, 10.0, 100.0, 1000.0, 10000.0]
CONJUNTOS = {   # meses UTC (inclusive)
    "entrenamiento": ("2018-11", "2019-09"),
    "validacion": ("2019-10", "2019-11"),
    "prueba": ("2019-12", "2020-02"),
}
AVISO = ("Entrenado con datos externos históricos de Medellín (2018-2020); "
         "no representa la precisión con el sensor propio.")
SUPUESTOS = [
    "El valor de OpenAQ en la hora T es la media de PM2.5 de la hora que termina en T.",
    "Hora del dia en hora de Colombia (UTC-5 fijo).",
    "Las horas del CSV ya estan en UTC (tools/convertir_openaq.py respeto el desfase -01:00 del archivo).",
    "La estacion de referencia (MED-UNNV, equipo oficial) y el sensor propio (SDS011, bajo costo) miden distinto.",
]


# ---------- Datos ----------

def sha256(ruta):
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def leer_csv(lineas):
    """-> ({hora_utc (horas desde 1970): pm25}, info). Descarta horas que no son en punto."""
    serie, info = {}, {"fuentes": Counter(), "estaciones": Counter(), "ciudades": Counter(),
                       "licencias": Counter(), "filas": 0, "no_en_punto": 0, "duplicadas": 0}
    for r in csv.DictReader(lineas):
        info["filas"] += 1
        t = datetime.fromisoformat(r["measured_at"])
        if t.tzinfo is None:
            raise ValueError(f"hora sin zona en el CSV: {r['measured_at']!r}")
        t = t.astimezone(timezone.utc)
        if t.minute or t.second or t.microsecond:
            info["no_en_punto"] += 1
            continue
        h = int(t.timestamp() // 3600)
        if h in serie:
            info["duplicadas"] += 1
            continue
        serie[h] = float(r["pm25"])
        for k, c in (("fuentes", "source"), ("estaciones", "station"), ("ciudades", "city"), ("licencias", "license")):
            info[k][r[c]] += 1
    return serie, info


def mes(h):
    return datetime.fromtimestamp(h * 3600, tz=timezone.utc).strftime("%Y-%m")


def conjunto_de(h):
    m = mes(h)
    for nombre, (ini, fin) in CONJUNTOS.items():
        if ini <= m <= fin:
            return nombre
    return None


def hora_colombia(h):
    return datetime.fromtimestamp(h * 3600, tz=COLOMBIA).hour


def entrada_en(serie, t):
    """Variables en la hora t (fin de la hora t = 'ahora'), o None si falta t, t-1 o t-2."""
    if not all(k in serie for k in (t, t - 1, t - 2)):
        return None
    return variables_horarias_desde(serie[t], serie[t - 1], serie[t - 2], t * 60)


def muestras(serie, nombre, horizonte):
    """X, y, persistencia, hora de Colombia del objetivo; entrada y objetivo dentro del conjunto."""
    X, y, P, H = [], [], [], []
    for t in sorted(serie):
        obj = t + horizonte
        if obj not in serie or conjunto_de(t - 2) != nombre or conjunto_de(obj) != nombre:
            continue
        x = entrada_en(serie, t)
        if x is None:
            continue
        X.append([x[v] for v in VARIABLES_HORARIAS])
        y.append(serie[obj])
        P.append(serie[t])
        H.append(hora_colombia(obj))
    return np.array(X).reshape(-1, len(VARIABLES_HORARIAS)), np.array(y), np.array(P), np.array(H, dtype=int)


def media_por_hora(serie):
    """Media de PM2.5 por hora del dia (Colombia) SOLO con horas del entrenamiento."""
    vals = defaultdict(list)
    for h, v in serie.items():
        if conjunto_de(h) == "entrenamiento":
            vals[hora_colombia(h)].append(v)
    return {k: float(np.mean(v)) for k, v in sorted(vals.items())}


def rango(serie, nombre):
    hs = [h for h in serie if conjunto_de(h) == nombre]
    f = lambda h: datetime.fromtimestamp(h * 3600, tz=timezone.utc).isoformat()  # noqa: E731
    return {"meses_utc": list(CONJUNTOS[nombre]), "primera_hora_utc": f(min(hs)) if hs else None,
            "ultima_hora_utc": f(max(hs)) if hs else None, "horas_con_datos": len(hs)}


# ---------- Entrenamiento y evaluacion ----------

def entrenar_y_evaluar(serie):
    """Devuelve (media, escala, horizontes, tabla_media_horaria, muestras_por_conjunto)."""
    # Estandarizacion con todas las ventanas de entrada completas del entrenamiento
    base = [entrada_en(serie, t) for t in sorted(serie) if conjunto_de(t - 2) == conjunto_de(t) == "entrenamiento"]
    Xb = np.array([[x[v] for v in VARIABLES_HORARIAS] for x in base if x is not None])
    if len(Xb) < 200:
        raise ValueError(f"datos de entrenamiento insuficientes ({len(Xb)} ventanas)")
    media, escala = Xb.mean(axis=0), Xb.std(axis=0)
    escala[escala == 0] = 1.0
    tabla = media_por_hora(serie)
    horizontes, n_muestras = {}, {}
    for hh in HORIZONTES_H:
        tr, va, te = (muestras(serie, c, hh) for c in ("entrenamiento", "validacion", "prueba"))
        n_muestras[str(hh * 60)] = {"entrenamiento": len(tr[1]), "validacion": len(va[1]), "prueba": len(te[1])}
        if len(tr[1]) < 200 or len(va[1]) < 50 or len(te[1]) < 50:
            raise ValueError(f"muestras insuficientes a {hh} h: {n_muestras[str(hh * 60)]}")
        Ztr, Zva, Zte = ((X - media) / escala for X in (tr[0], va[0], te[0]))
        # 1) alpha con la validacion (MAE); 2) ajuste final solo con entrenamiento
        mae_val = {}
        for a in ALPHAS:
            b, c = ajustar_ridge(Ztr, tr[1] - tr[2], a)
            mae_val[a] = errores(va[1], np.maximum(0.0, va[2] + b + Zva @ c))[0]
        mejor = min(ALPHAS, key=lambda a: mae_val[a])
        b, c = ajustar_ridge(Ztr, tr[1] - tr[2], mejor)
        # 3) prueba: una sola evaluacion
        pred = np.maximum(0.0, te[2] + b + Zte @ c)
        mae_m, rmse_m = errores(te[1], pred)
        mae_p, rmse_p = errores(te[1], te[2])
        mae_h, rmse_h = errores(te[1], np.array([tabla[k] for k in te[3]]))
        horizontes[str(hh * 60)] = {
            "intercepto": float(b), "coef": [float(v) for v in c], "alpha": mejor,
            "mae_validacion_por_alpha": {str(a): round(v, 4) for a, v in mae_val.items()},
            "metricas": {"conjunto": "prueba", "n_prueba": int(len(te[1])),
                         "mae_modelo": round(mae_m, 3), "rmse_modelo": round(rmse_m, 3),
                         "mae_persistencia": round(mae_p, 3), "rmse_persistencia": round(rmse_p, 3),
                         "mae_media_horaria": round(mae_h, 3), "rmse_media_horaria": round(rmse_h, 3),
                         "mejora_mae_pct": round(100 * (mae_p - mae_m) / mae_p, 1) if mae_p else None,
                         "mejora_rmse_pct": round(100 * (rmse_p - rmse_m) / rmse_p, 1) if rmse_p else None},
            "supera_persistencia": bool(mae_m < mae_p),
        }
    return media, escala, horizontes, tabla, n_muestras


def construir_modelo(serie, info, ruta_entrada, huella):
    media, escala, horizontes, tabla, n_muestras = entrenar_y_evaluar(serie)
    unico = lambda c: next(iter(c)) if len(c) == 1 else sorted(c)  # noqa: E731
    return {
        "nombre": NOMBRE,
        "tipo": "ridge_sobre_residuo_de_persistencia",
        "resolucion": "hora",
        "persistencia": "pm25_h1",
        "experimental": True,
        "creado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "advertencia": "Prediccion experimental. " + AVISO,
        "aviso_publico": AVISO,
        "variables": VARIABLES_HORARIAS,
        "media": [float(v) for v in media],
        "escala": [float(v) for v in escala],
        "horizontes": horizontes,
        "linea_base": "persistencia: media de PM2.5 de la ultima hora (pm25_h1)",
        "rival_adicional": "media por hora del dia (hora de Colombia), solo con el entrenamiento",
        "media_por_hora_entrenamiento": {str(k): round(v, 3) for k, v in tabla.items()},
        "objetivo": "media horaria de PM2.5 que termina en ahora + horizonte",
        "fuentes": [{
            "tipo": "externos",
            "fuente": unico(info["fuentes"]), "estacion": unico(info["estaciones"]),
            "ciudad": unico(info["ciudades"]), "licencia": unico(info["licencias"]),
            "proveedor_archivo": "OpenAQ (location_id 10641, MED-UNNV)",
            "archivo": ruta_entrada.name, "sha256": huella,
            "filas_csv": info["filas"], "horas_con_datos": len(serie),
            "detalle": "CSV local de tools/convertir_openaq.py; ninguna base de datos",
        }],
        "conjuntos": {c: rango(serie, c) for c in CONJUNTOS},
        "muestras_por_horizonte": n_muestras,
        "hiperparametros": {"alphas": ALPHAS, "horizontes_h": list(HORIZONTES_H),
                            "seleccion_alpha": "menor MAE en validacion", "ajuste_final": "solo entrenamiento",
                            "regla_disponible": "MAE del modelo < MAE de la persistencia en la prueba"},
        "supuestos": SUPUESTOS,
        "sin_variables": "No usa temperatura ni humedad (la estacion externa solo trae PM2.5).",
    }


def main(argv=None):
    p = argparse.ArgumentParser(description="Entrena prediccion_pm25_v2 con PM2.5 horario externo (CSV local)")
    p.add_argument("--entrada", type=Path, default=ENTRADA)
    p.add_argument("--salida", type=Path, default=SALIDA)
    args = p.parse_args(argv)
    if args.salida.resolve() == V1.resolve():
        print("ERROR: no se sobrescribe prediccion_pm25_v1.json")
        return 2
    try:
        with open(args.entrada, encoding="utf-8", newline="") as f:
            serie, info = leer_csv(f)
        modelo = construir_modelo(serie, info, args.entrada, sha256(args.entrada))
    except (ValueError, OSError, KeyError) as e:
        print(f"ERROR: {e}")
        return 2
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_text(json.dumps(modelo, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Entrada: {args.entrada.name} · {info['filas']} filas · {len(serie)} horas · sha256 {modelo['fuentes'][0]['sha256'][:12]}…")
    for c, r in modelo["conjuntos"].items():
        print(f"  {c:<14} {r['primera_hora_utc']} -> {r['ultima_hora_utc']} · {r['horas_con_datos']} horas")
    print(f"\n{'horiz.':<7}{'n prueba':>9}{'MAE mod':>9}{'MAE pers':>10}{'MAE hora':>10}"
          f"{'RMSE mod':>10}{'RMSE pers':>11}{'RMSE hora':>11}{'mejora MAE':>12}  alpha  supera")
    for h, d in modelo["horizontes"].items():
        m = d["metricas"]
        print(f"{int(h) // 60} h{'':<4}{m['n_prueba']:>9}{m['mae_modelo']:>9.3f}{m['mae_persistencia']:>10.3f}"
              f"{m['mae_media_horaria']:>10.3f}{m['rmse_modelo']:>10.3f}{m['rmse_persistencia']:>11.3f}"
              f"{m['rmse_media_horaria']:>11.3f}{str(m['mejora_mae_pct']) + ' %':>12}  {d['alpha']:<6g} "
              f"{'si' if d['supera_persistencia'] else 'NO'}")
    print(f"\n{AVISO}\nModelo escrito en {args.salida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
