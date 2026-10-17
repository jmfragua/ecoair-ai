"""Convierte el archivo de OpenAQ (CSV.gz diarios) al formato de db/load_external.py. NO toca la base.

Solo biblioteca estandar. Lee todos los .csv.gz / .csv de un directorio (recursivo, en orden
alfabetico), con columnas: location_id,sensors_id,location,datetime,lat,lon,parameter,units,value.

Que hace:
- Usa solo parameter == "pm25" (el archivo trae tambien o3 y no2).
- value <= -999 es "sin dato" en OpenAQ (-9999.0): se descarta y se cuenta aparte.
- Rango de la migracion 006: pm25 entre 0 y 1000 (CHECK + NUMERIC(6,1)); lo demas se descarta.
  Unidades distintas de ug/m3, valores no numericos u horas sin zona se descartan como invalidas.
- datetime viene en ISO con desfase (p. ej. 2020-03-16T01:00:00-01:00): se respeta ese desfase y
  se convierte a UTC. Una hora sin desfase se descarta (no se adivina la zona).
- Duplicados: misma hora UTC; se conserva la primera fila (archivos en orden alfabetico).
- Salida: CSV con source,station,city,measured_at,pm25,license (measured_at en UTC, ISO 8601),
  ordenado por hora. Se escribe en datos_externos/ (ignorado por git), nunca en otra parte del repo.

Ejemplo:
  python tools/convertir_openaq.py datos_externos/openaq_10641
  python db/load_external.py datos_externos/openaq_10641_pm25_utc.csv --source "OpenAQ archive" \\
      --license "PENDIENTE DE VERIFICAR" --col-time measured_at --col-pm25 pm25 \\
      --col-station station --col-city city --dry-run
"""

import argparse
import csv
import gzip
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SALIDA_PERMITIDA = RAIZ / "datos_externos"
SALIDA_POR_DEFECTO = SALIDA_PERMITIDA / "openaq_10641_pm25_utc.csv"

SOURCE = "OpenAQ archive"
STATION = "MED-UNNV El Volador (OpenAQ 10641)"
CITY = "Medellín"
LICENCIA_POR_DEFECTO = "PENDIENTE DE VERIFICAR"
PARAMETRO = "pm25"
UNIDADES = "µg/m³"
SIN_DATO_MAX = -999          # value <= -999 -> sin dato (OpenAQ usa -9999.0)
RANGO_PM25 = (0, 1000)       # CHECK de db/migrations/006_external_measurements.sql
COLUMNAS_SALIDA = ("source", "station", "city", "measured_at", "pm25", "license")


def hora_utc(texto):
    """'2020-03-16T01:00:00-01:00' -> datetime UTC. None si no es ISO o no trae desfase."""
    try:
        dt = datetime.fromisoformat(texto.strip().replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def convertir(filas, licencia=LICENCIA_POR_DEFECTO):
    """filas: iterable de dicts del CSV de OpenAQ. Devuelve (salida ordenada por hora, resumen)."""
    licencia = (licencia or "").strip()
    if not licencia:
        raise ValueError("la licencia no puede estar vacia (CHECK de la migracion 006)")
    r = Counter()
    vistas, salida = {}, []
    for f in filas:
        r["leidas"] += 1
        if f.get("parameter") != PARAMETRO:
            r["otros_parametros"] += 1
            continue
        r["leidas_pm25"] += 1
        try:
            v = float(f.get("value", ""))
        except ValueError:
            r["invalidas"] += 1
            continue
        if v != v or v in (float("inf"), float("-inf")):
            r["invalidas"] += 1
            continue
        if v <= SIN_DATO_MAX:
            r["sin_dato"] += 1
            continue
        if f.get("units") != UNIDADES:
            r["invalidas"] += 1
            continue
        if not RANGO_PM25[0] <= v <= RANGO_PM25[1]:
            r["fuera_de_rango"] += 1
            continue
        t = hora_utc(f.get("datetime", ""))
        if t is None:
            r["invalidas"] += 1
            continue
        pm25 = round(v, 1)  # NUMERIC(6, 1) en la tabla
        if t in vistas:
            r["duplicados"] += 1
            if vistas[t] != pm25:
                r["duplicados_con_otro_valor"] += 1
            continue
        vistas[t] = pm25
        salida.append({"source": SOURCE, "station": STATION, "city": CITY,
                       "measured_at": t, "pm25": pm25, "license": licencia})
    salida.sort(key=lambda x: x["measured_at"])
    resumen = {k: r[k] for k in ("leidas", "otros_parametros", "leidas_pm25", "sin_dato", "fuera_de_rango",
                                 "invalidas", "duplicados", "duplicados_con_otro_valor")}
    resumen["finales"] = len(salida)
    resumen["primera"] = salida[0]["measured_at"] if salida else None
    resumen["ultima"] = salida[-1]["measured_at"] if salida else None
    resumen["por_mes"] = dict(sorted(Counter(x["measured_at"].strftime("%Y-%m") for x in salida).items()))
    return salida, resumen


def archivos_de_entrada(directorio):
    return sorted(p for p in Path(directorio).rglob("*") if p.is_file() and p.name.endswith((".csv.gz", ".csv")))


def leer_filas(archivos):
    for p in archivos:
        abrir = gzip.open if p.name.endswith(".gz") else open
        with abrir(p, "rt", encoding="utf-8-sig", newline="") as h:
            yield from csv.DictReader(h)


def comprobar_salida(ruta):
    """La salida va en datos_externos/ (ignorado por git) o fuera del repo; nunca en otra parte del repo."""
    ruta = Path(ruta).resolve()
    if ruta.is_relative_to(RAIZ) and not ruta.is_relative_to(SALIDA_PERMITIDA.resolve()):
        raise ValueError(f"la salida debe ir en {SALIDA_PERMITIDA.relative_to(RAIZ)}/ o fuera del repo: {ruta}")
    return ruta


def escribir_csv(salida, ruta):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with open(ruta, "w", encoding="utf-8", newline="") as h:
        w = csv.DictWriter(h, fieldnames=COLUMNAS_SALIDA)
        w.writeheader()
        for x in salida:
            w.writerow({**x, "measured_at": x["measured_at"].isoformat(), "pm25": f"{x['pm25']:.1f}"})


def imprimir_resumen(r, archivos, ruta, licencia):
    print(f"Archivos leidos: {archivos}")
    print(f"Filas leidas: {r['leidas']} (pm25: {r['leidas_pm25']}; otros parametros ignorados: {r['otros_parametros']})")
    print(f"Descartadas por -9999 (sin dato): {r['sin_dato']}")
    print(f"Descartadas por rango (0..1000): {r['fuera_de_rango']}")
    if r["invalidas"]:
        print(f"Descartadas por invalidas (no numericas, unidades u hora sin desfase): {r['invalidas']}")
    print(f"Duplicados (misma hora UTC): {r['duplicados']}"
          + (f" ({r['duplicados_con_otro_valor']} con otro valor)" if r["duplicados_con_otro_valor"] else ""))
    print(f"Filas finales: {r['finales']}")
    if r["finales"]:
        print(f"Primera: {r['primera']:%Y-%m-%d %H:%M} UTC · ultima: {r['ultima']:%Y-%m-%d %H:%M} UTC")
        print("Filas por mes (UTC):")
        for mes, n in r["por_mes"].items():
            print(f"  {mes}: {n}")
    print(f"Licencia: {licencia}")
    print(f"Salida: {ruta}")


def main(argv=None):
    p = argparse.ArgumentParser(description="Convierte el archivo de OpenAQ al formato de db/load_external.py")
    p.add_argument("entrada", type=Path, help="directorio con los CSV.gz diarios de OpenAQ")
    p.add_argument("--salida", type=Path, default=SALIDA_POR_DEFECTO)
    p.add_argument("--licencia", default=LICENCIA_POR_DEFECTO)
    args = p.parse_args(argv)
    try:
        ruta = comprobar_salida(args.salida)
        archivos = archivos_de_entrada(args.entrada)
        if not archivos:
            raise ValueError(f"no hay .csv.gz ni .csv en {args.entrada}")
        salida, r = convertir(leer_filas(archivos), args.licencia)
    except (ValueError, OSError) as e:
        print(f"ERROR: {e}")
        return 2
    escribir_csv(salida, ruta)
    imprimir_resumen(r, len(archivos), ruta, args.licencia.strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
