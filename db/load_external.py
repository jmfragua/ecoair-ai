"""Carga datos EXTERNOS de calidad del aire desde un CSV a external_measurements.

Requiere db/migrations/006_external_measurements.sql. Usa la misma configuracion de base de
datos del proyecto (DATABASE_URL o DB_HOST/DB_PORT/DB_NAME/DB_USER/POSTGRES_PASSWORD; ver
shared/settings.py). Nunca escribe en measurements.

Ejemplo:
  python db/load_external.py datos.csv --source "Red de monitoreo X" --license "CC BY 4.0" \\
      --col-time fecha --col-pm25 pm25 --col-pm10 pm10 --col-station estacion --city Medellin \\
      --utc-offset -05:00 --dry-run

- Columnas: se indican con --col-*. PM2.5 es obligatorio; PM10, temperatura y humedad son
  opcionales (sin --col-* o con la celda vacia = NULL). Estacion y ciudad pueden venir de una
  columna o ser fijas (--station / --city).
- Hora: ISO 8601 o --time-format (formato de strptime). Si la hora no trae zona, --utc-offset es
  obligatorio (p. ej. -05:00 para Colombia); se guarda en UTC.
- Validacion: rangos fisicos del contrato (PM 0..1000, temperatura -40..80, humedad 0..100).
  Las filas invalidas se descartan y se cuentan, con el numero de linea.
- Duplicados: dentro del CSV se conserva la primera fila de cada (fuente, estacion, hora); en la
  base, ON CONFLICT DO NOTHING con la restriccion UNIQUE de la tabla.
- Carga por lotes (--batch-size), cada lote en su propia transaccion.
"""

import argparse
import csv
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from shared.settings import descripcion_db, dsn_desde_entorno  # noqa: E402

RANGOS = {"pm25": (0, 1000), "pm10": (0, 1000), "temperature": (-40, 80), "humidity": (0, 100)}
OBLIGATORIAS = ("pm25",)
OPCIONALES = ("pm10", "temperature", "humidity")
MAX_ERRORES_MOSTRADOS = 10

SQL_INSERT = (
    "INSERT INTO external_measurements "
    "(source, station, city, measured_at, pm25, pm10, temperature, humidity, license) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
    "ON CONFLICT (source, station, measured_at) DO NOTHING"
)


class FilaInvalida(ValueError):
    pass


# ---------- Conversiones ----------

def parsear_offset(texto):
    """'-05:00' / '+0100' / 'Z' -> timezone. None si texto es None."""
    if texto is None:
        return None
    if texto.upper() == "Z":
        return timezone.utc
    m = re.fullmatch(r"([+-])(\d{2}):?(\d{2})", texto.strip())
    if not m:
        raise ValueError(f"--utc-offset no valido: {texto!r} (use p. ej. -05:00)")
    signo = -1 if m.group(1) == "-" else 1
    return timezone(signo * timedelta(hours=int(m.group(2)), minutes=int(m.group(3))))


def parsear_numero(texto, decimal="."):
    """'12,5' (con decimal=',') -> 12.5; vacio -> None; texto no numerico -> FilaInvalida."""
    if texto is None or texto.strip() == "":
        return None
    t = texto.strip()
    if decimal != ".":
        t = t.replace(".", "").replace(decimal, ".") if decimal == "," else t.replace(decimal, ".")
    try:
        v = float(t)
    except ValueError:
        raise FilaInvalida(f"valor no numerico: {texto!r}") from None
    if v != v or v in (float("inf"), float("-inf")):
        raise FilaInvalida(f"valor no finito: {texto!r}")
    return v


def parsear_hora(texto, formato=None, offset=None):
    """Devuelve datetime en UTC. Hora sin zona: usa offset; si no hay offset -> FilaInvalida."""
    if texto is None or texto.strip() == "":
        raise FilaInvalida("hora vacia")
    t = texto.strip()
    try:
        dt = datetime.strptime(t, formato) if formato else datetime.fromisoformat(t.replace("Z", "+00:00"))
    except ValueError:
        raise FilaInvalida(f"hora no valida: {texto!r}") from None
    if dt.tzinfo is None:
        if offset is None:
            raise FilaInvalida("la hora no trae zona horaria: indique --utc-offset (p. ej. -05:00)")
        dt = dt.replace(tzinfo=offset)
    return dt.astimezone(timezone.utc)


# ---------- Validacion de una fila ----------

def validar_fila(raw, cfg):
    """raw: dict del CSV. cfg: dict con source, license, columnas y opciones.

    Devuelve la tupla lista para insertar (sin license/source repetidos aparte) o lanza FilaInvalida.
    """
    def col(nombre):
        c = cfg["cols"].get(nombre)
        if c is None:
            return None
        if c not in raw:
            raise FilaInvalida(f"falta la columna {c!r}")
        return raw[c]

    station = (cfg.get("station") or col("station") or "").strip()
    city = (cfg.get("city") or col("city") or "").strip()
    if not station:
        raise FilaInvalida("estacion vacia")
    if not city:
        raise FilaInvalida("ciudad vacia")
    valores = {}
    for campo in OBLIGATORIAS + OPCIONALES:
        v = parsear_numero(col(campo), cfg.get("decimal", "."))
        if v is None:
            if campo in OBLIGATORIAS:
                raise FilaInvalida(f"{campo} vacio")
            valores[campo] = None
            continue
        lo, hi = RANGOS[campo]
        if not lo <= v <= hi:
            raise FilaInvalida(f"{campo}={v:g} fuera de rango [{lo}, {hi}]")
        valores[campo] = round(v, 1)
    measured_at = parsear_hora(col("time"), cfg.get("time_format"), cfg.get("offset"))
    return {"source": cfg["source"], "station": station, "city": city, "measured_at": measured_at,
            **valores, "license": cfg["license"]}


def procesar_csv(lineas, cfg):
    """Valida y deduplica. Devuelve (filas_validas, resumen).

    resumen: leidas, validas, invalidas, duplicadas_en_csv, errores (lista de (linea, motivo)).
    """
    lector = csv.DictReader(lineas, delimiter=cfg.get("delimiter", ","))
    faltan = [c for c in cfg["cols"].values() if c is not None and c not in (lector.fieldnames or [])]
    if faltan:
        raise ValueError(f"el CSV no tiene las columnas indicadas: {', '.join(faltan)}. "
                         f"Columnas del CSV: {', '.join(lector.fieldnames or [])}")
    vistas, filas, errores = set(), [], []
    motivos = Counter()
    leidas = duplicadas = 0
    for raw in lector:
        leidas += 1
        linea = lector.line_num
        try:
            fila = validar_fila(raw, cfg)
        except FilaInvalida as e:
            errores.append((linea, str(e)))
            motivos[str(e).split(":")[0].split("=")[0]] += 1
            continue
        clave = (fila["source"], fila["station"], fila["measured_at"])
        if clave in vistas:
            duplicadas += 1
            continue
        vistas.add(clave)
        filas.append(fila)
    return filas, {"leidas": leidas, "validas": len(filas), "invalidas": len(errores),
                   "duplicadas_en_csv": duplicadas, "errores": errores, "motivos": dict(motivos)}


def lotes(items, tamano):
    for i in range(0, len(items), tamano):
        yield items[i:i + tamano]


def insertar(conn, filas, tamano_lote):
    """Inserta por lotes; cada lote en su transaccion. Devuelve (insertadas, ya_existian)."""
    insertadas = 0
    for lote in lotes(filas, tamano_lote):
        with conn.transaction():
            with conn.cursor() as cur:
                # psycopg 3: tras executemany, rowcount es la suma de filas afectadas del lote
                # (las que chocan con UNIQUE no cuentan por ON CONFLICT DO NOTHING).
                cur.executemany(SQL_INSERT, [
                    (f["source"], f["station"], f["city"], f["measured_at"], f["pm25"], f["pm10"],
                     f["temperature"], f["humidity"], f["license"]) for f in lote])
                insertadas += max(cur.rowcount, 0)
    return insertadas, len(filas) - insertadas


# ---------- CLI ----------

def construir_cfg(args):
    if not args.station and not args.col_station:
        raise ValueError("indique --col-station o --station")
    if not args.city and not args.col_city:
        raise ValueError("indique --col-city o --city")
    return {
        "source": args.source.strip(),
        "license": args.license.strip(),
        "station": args.station,
        "city": args.city,
        "cols": {"time": args.col_time, "pm25": args.col_pm25, "pm10": args.col_pm10,
                 "temperature": args.col_temperature, "humidity": args.col_humidity,
                 "station": None if args.station else args.col_station,
                 "city": None if args.city else args.col_city},
        "time_format": args.time_format,
        "offset": parsear_offset(args.utc_offset),
        "decimal": args.decimal,
        "delimiter": args.delimiter,
    }


def main(argv=None, conectar=None):
    p = argparse.ArgumentParser(description="Carga datos externos de calidad del aire (CSV)")
    p.add_argument("csv", type=Path)
    p.add_argument("--source", required=True, help="fuente de los datos (quien los publica)")
    p.add_argument("--license", required=True, help="licencia de uso de la fuente")
    p.add_argument("--col-time", required=True)
    p.add_argument("--col-pm25", required=True)
    p.add_argument("--col-pm10", help="opcional: muchas estaciones solo miden PM2.5")
    p.add_argument("--col-temperature")
    p.add_argument("--col-humidity")
    p.add_argument("--col-station")
    p.add_argument("--col-city")
    p.add_argument("--station", help="estacion fija para todo el archivo")
    p.add_argument("--city", help="ciudad fija para todo el archivo")
    p.add_argument("--time-format", help="formato strptime, p. ej. '%%Y-%%m-%%d %%H:%%M' (por defecto ISO 8601)")
    p.add_argument("--utc-offset", help="zona de las horas sin zona, p. ej. -05:00")
    p.add_argument("--decimal", default=".", choices=[".", ","])
    p.add_argument("--delimiter", default=",")
    p.add_argument("--encoding", default="utf-8-sig")
    p.add_argument("--batch-size", type=int, default=1000)
    p.add_argument("--dry-run", action="store_true", help="valida y resume, sin escribir en la base")
    args = p.parse_args(argv)

    try:
        cfg = construir_cfg(args)
        with args.csv.open(encoding=args.encoding, newline="") as f:
            filas, r = procesar_csv(f, cfg)
    except (ValueError, OSError) as e:
        print(f"ERROR: {e}")
        return 2

    print(f"Archivo: {args.csv.name} · fuente: {cfg['source']} · licencia: {cfg['license']}")
    print(f"Filas leidas: {r['leidas']} · validas: {r['validas']} · invalidas: {r['invalidas']} · "
          f"duplicadas en el CSV: {r['duplicadas_en_csv']}")
    for linea, motivo in r["errores"][:MAX_ERRORES_MOSTRADOS]:
        print(f"  linea {linea}: {motivo}")
    if r["invalidas"] > MAX_ERRORES_MOSTRADOS:
        print(f"  ... y {r['invalidas'] - MAX_ERRORES_MOSTRADOS} errores mas. Por motivo: {r['motivos']}")
    if filas:
        print(f"Rango: {min(f['measured_at'] for f in filas):%Y-%m-%d %H:%M} -> "
              f"{max(f['measured_at'] for f in filas):%Y-%m-%d %H:%M} UTC · "
              f"estaciones: {len({f['station'] for f in filas})}")
    if args.dry_run or not filas:
        print("Modo --dry-run: no se escribio nada." if args.dry_run else "Nada que cargar.")
        return 0

    if conectar is None:
        import psycopg
        conectar = lambda: psycopg.connect(dsn_desde_entorno(connect_timeout=10), autocommit=True)  # noqa: E731
    print(f"Base de datos: {descripcion_db()}")
    with conectar() as conn:
        existe = conn.execute("SELECT to_regclass('external_measurements') IS NOT NULL").fetchone()[0]
        if not existe:
            print("ERROR: falta aplicar db/migrations/006_external_measurements.sql (python db/migrate.py)")
            return 2
        insertadas, existian = insertar(conn, filas, args.batch_size)
    print(f"Insertadas: {insertadas} · ya existian en la base (omitidas): {existian}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
