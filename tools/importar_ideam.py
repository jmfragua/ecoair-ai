"""Importa PM2.5 HORARIO del IDEAM (datos.gov.co, conjunto g4t8-zkc3) a external_measurements.

Solo estaciones del AMVA (Valle de Aburra), msfl_code = 'PM2.5', duracion 60 min. Se lee la API
Socrata con filtros SoQL y paginacion, todo en memoria: no se escribe ningun archivo de datos.

- Tabla: reutiliza external_measurements (migracion 006); no crea ni aplica migraciones. Si la
  tabla no existe, se detiene.
- Fuente 'IDEAM datos.gov.co', licencia 'CC BY-SA 4.0'. Estacion "NOMBRE (IDEAM <id>)" y ciudad =
  municipio. Nunca se mezcla con OpenAQ (otra fuente y otros nombres de estacion).
- Hora: el conjunto trae horas SIN zona; se toman como hora de Colombia (UTC-5) y se guarda en UTC
  el FIN de la hora (med_fecha_final), igual que con OpenAQ: el valor de T es la media de la hora
  que termina en T. Evidencia: el pico medio de PM2.5 cae a las 08:00 de la hora etiquetada.
- Descarta: valor nulo, negativo, > 1000 (CHECK de la tabla), duracion distinta de 60 min o
  unidad distinta de ug/m3. Cuenta cada motivo.
- Limpieza (tools/limpieza_pm25.py) antes de insertar: descarta el 0 exacto (falla del equipo) y las
  rachas de 6+ horas seguidas con el mismo valor; ambos se cuentan aparte en el resumen.
- Formato del IDEAM (cambio del 2026-10-24): las fechas llegan como TEXTO 'AAAA-MM-DD HH:MM:SS' y la
  duracion como '60' o '60.0'. El filtro SoQL usa ese mismo formato de texto y, ademas, el rango de
  fechas se vuelve a comprobar en Python (por si el formato cambia otra vez).
- Idempotente: ON CONFLICT (source, station, measured_at) DO NOTHING (db/load_external.py).
- Base: exige DATABASE_URL (si falta, se detiene) salvo con --local, que usa SOLO DB_* (localhost:5433
  por defecto) aunque DATABASE_URL exista. Muestra el destino (sin usuario ni clave) antes de escribir;
  los errores de conexion salen sin URL ni clave. --dry-run y --listar no se conectan a ninguna base.

- --listar: solo consulta conteos (sin descargar mediciones) y estima el tamano en la base.
- --estaciones 29509,29493 y/o --min-horas N: limitan que estaciones se importan.

  python tools/importar_ideam.py --listar
  python tools/importar_ideam.py --estacion 29509 --desde 2024-03-01 --hasta 2024-03-31 --dry-run
  python tools/importar_ideam.py --min-horas 30000 --dry-run
  python tools/importar_ideam.py --estaciones 29509,29493 --desde 2020-01-01 --hasta 2024-12-31
"""

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
for p in (RAIZ, RAIZ / "db", Path(__file__).resolve().parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import limpieza_pm25  # noqa: E402
from load_external import RANGOS, insertar  # noqa: E402
from shared.settings import destino_importacion, error_conexion_seguro  # noqa: E402

API = "https://www.datos.gov.co/resource/g4t8-zkc3.json"
SOURCE = "IDEAM datos.gov.co"
LICENCIA = "CC BY-SA 4.0"
ENTIDAD = "AMVA"
COLOMBIA = timezone(timedelta(hours=-5))
UNIDAD = "ug/m3"
DURACION_MIN = 60
CAMPOS = ("estacion_id", "nombre_est", "municipio", "med_concentracion_estandar",
          "med_fecha_inicio", "med_fecha_final", "duraci_n", "sigla_unidad", "tipo_estacion")


# ---------- SoQL ----------

def codigos_estacion(texto):
    """'29509, 29493' -> ['29509', '29493']. Solo digitos (evita inyeccion en SoQL)."""
    codigos = [c.strip() for c in str(texto).split(",") if c.strip()]
    malos = [c for c in codigos if not c.isdigit()]
    if malos:
        raise ValueError(f"codigos de estacion no numericos: {', '.join(malos)}")
    return codigos


def filtro_soql(desde, hasta, estaciones=None, solo_horarias=False):
    """WHERE de SoQL: AMVA, PM2.5 y med_fecha_inicio en [desde, hasta] (fechas inclusive)."""
    fin = hasta + timedelta(days=1)
    w = (f"msfl_code='PM2.5' AND nombre_fgda='{ENTIDAD}' "
         f"AND med_fecha_inicio >= '{desde.isoformat()} 00:00:00' AND med_fecha_inicio < '{fin.isoformat()} 00:00:00'")
    if solo_horarias:
        w += f" AND duraci_n={DURACION_MIN}"
    if estaciones:
        if isinstance(estaciones, str):
            estaciones = [estaciones]
        codigos = codigos_estacion(",".join(estaciones))
        w += " AND estacion_id='" + codigos[0] + "'" if len(codigos) == 1 else \
            " AND estacion_id in (" + ", ".join(f"'{c}'" for c in codigos) + ")"
    return w


def url_pagina(where, limite, offset):
    q = {"$select": ",".join(CAMPOS), "$where": where,
         "$order": "estacion_id, med_fecha_inicio, :id", "$limit": limite, "$offset": offset}
    return API + "?" + urllib.parse.urlencode(q)


def descargar(where, limite=50000, abrir=None, reintentos=3):
    """Itera las filas de todas las paginas (orden estable con :id)."""
    abrir = abrir or (lambda u: urllib.request.urlopen(
        urllib.request.Request(u, headers={"Accept": "application/json"}), timeout=180))
    offset = 0
    while True:
        for intento in range(reintentos):
            try:
                with abrir(url_pagina(where, limite, offset)) as r:
                    pagina = json.load(r)
                break
            except OSError:
                if intento == reintentos - 1:
                    raise
                time.sleep(2 ** intento)
        yield from pagina
        if len(pagina) < limite:
            return
        offset += limite


# ---------- Listado y tamano estimado ----------

def contar_por_estacion(desde, hasta, abrir=None):
    """Horas PM2.5 (duracion 60) por estacion AMVA, de mayor a menor. Solo agregados: no descarga datos."""
    abrir = abrir or (lambda u: urllib.request.urlopen(
        urllib.request.Request(u, headers={"Accept": "application/json"}), timeout=180))
    q = {"$select": "estacion_id, nombre_est, municipio, tipo_estacion, count(*) as horas, "
                    "min(med_fecha_inicio) as primera, max(med_fecha_inicio) as ultima",
         "$where": filtro_soql(desde, hasta, solo_horarias=True),
         "$group": "estacion_id, nombre_est, municipio, tipo_estacion",
         "$order": "horas DESC", "$limit": 1000}
    with abrir(API + "?" + urllib.parse.urlencode(q)) as r:
        filas = json.load(r)
    return [{"estacion_id": f["estacion_id"], "nombre": f["nombre_est"].strip(), "ciudad": f.get("municipio") or "",
             "tipo": f.get("tipo_estacion"), "horas": int(f["horas"]),
             "primera": f["primera"][:10], "ultima": f["ultima"][:10]} for f in filas]


def _alinear(n, a=8):
    return (n + a - 1) // a * a


def _texto(s):
    """Bytes de un TEXT corto en Postgres: cabecera de 1 byte + UTF-8."""
    return 1 + len(s.encode("utf-8"))


# Estimacion (aproximada) del espacio en Postgres por fila de external_measurements:
# tupla del heap + sus 4 indices (PK id, UNIQUE source/station/measured_at, city/measured_at,
# source/loaded_at). Paginas de indice B-tree llenas al ~90 %. No incluye TOAST ni bloat.
LLENADO_INDICE = 0.9
PUNTERO = 4          # line pointer por tupla (heap e indices)


def bytes_por_fila(station, city, source=SOURCE, licencia=LICENCIA):
    cabecera = _alinear(23 + 2)   # cabecera de tupla + bitmap de nulos (11 columnas)
    datos = 8                      # id
    datos += _texto(source) + _texto(station) + _texto(city)
    datos = _alinear(datos) + 8    # measured_at
    datos += 8                     # pm25 NUMERIC(6,1) (aprox.)
    datos += _texto(licencia)
    datos = _alinear(datos) + 8    # loaded_at (pm10, temperature y humidity son NULL)
    heap = cabecera + _alinear(datos) + PUNTERO
    idx = lambda clave: (_alinear(8 + clave) + PUNTERO) / LLENADO_INDICE  # noqa: E731
    pk = idx(8)
    unico = idx(_alinear(_texto(source) + _texto(station)) + 8)
    ciudad = idx(_alinear(_texto(city)) + 8)
    fuente = idx(_alinear(_texto(source)) + 8)
    return {"heap": heap, "pk": pk, "unico": unico, "otros": ciudad + fuente,
            "total": heap + pk + unico + ciudad + fuente}


def imprimir_listado(estaciones, desde, hasta):
    print(f"Estaciones AMVA con PM2.5 horario entre {desde} y {hasta} (fuente: {API})")
    print("Tamano estimado en Postgres: heap + 4 indices (aprox.; sin TOAST ni bloat)\n")
    print(f"{'#':>3} {'codigo':>7}  {'estacion':<44} {'ciudad':<12} {'tipo':<10} {'horas':>7} "
          f"{'periodo':<23} {'MB':>6} {'MB acum.':>8}")
    acum = 0.0
    for i, e in enumerate(estaciones, 1):
        st = f"{e['nombre']} (IDEAM {e['estacion_id']})"
        b = bytes_por_fila(st, e["ciudad"])
        mb = e["horas"] * b["total"] / 1e6
        acum += mb
        e["mb"], e["mb_acum"] = mb, acum
        print(f"{i:>3} {e['estacion_id']:>7}  {e['nombre'][:44]:<44} {e['ciudad'][:12]:<12} {str(e['tipo'])[:10]:<10} "
              f"{e['horas']:>7} {e['primera']} -> {e['ultima']} {mb:>6.1f} {acum:>8.1f}")
    total_h = sum(e["horas"] for e in estaciones)
    if estaciones:
        b = bytes_por_fila(f"{estaciones[0]['nombre']} (IDEAM {estaciones[0]['estacion_id']})", estaciones[0]["ciudad"])
        print(f"\nTotal: {len(estaciones)} estaciones, {total_h} horas, ~{acum:.1f} MB estimados.")
        print(f"Por fila (ej. {estaciones[0]['nombre']}): heap {b['heap']} B + PK {b['pk']:.0f} B + "
              f"indice unico {b['unico']:.0f} B + otros 2 indices {b['otros']:.0f} B = {b['total']:.0f} B")
        print("Para N estaciones, mira 'MB acum.' en la fila N (importando de mayor a menor).")


def seleccionar(estaciones, codigos=None, min_horas=None):
    """Filtra el listado por --estaciones y --min-horas (ambos se combinan)."""
    sel = estaciones
    if codigos:
        pedidos = set(codigos)
        sel = [e for e in sel if e["estacion_id"] in pedidos]
        faltan = pedidos - {e["estacion_id"] for e in sel}
        if faltan:
            raise ValueError(f"estaciones sin PM2.5 horario AMVA en el rango: {', '.join(sorted(faltan))}")
    if min_horas is not None:
        sel = [e for e in sel if e["horas"] >= min_horas]
    return sel


# ---------- Conversion ----------

def hora_utc(texto):
    """'2024-03-01T01:00:00.000' (hora de Colombia, sin zona) -> datetime UTC."""
    return datetime.fromisoformat(texto[:19]).replace(tzinfo=COLOMBIA).astimezone(timezone.utc)


def es_horaria(duracion):
    """'60', '60.0' o 60 -> True (el IDEAM cambio el formato de la duracion en 2026-10)."""
    try:
        return float(duracion) == DURACION_MIN
    except (TypeError, ValueError):
        return False


def convertir(filas, desde=None, hasta=None):
    """-> (filas para load_external.insertar, resumen con conteos por motivo y por estacion).

    desde/hasta (date, inclusive): descarta lo que caiga fuera segun med_fecha_inicio ("fuera_de_periodo").
    """
    r = Counter()
    vistas, salida = set(), []
    por_est = defaultdict(lambda: {"n": 0, "primera": None, "ultima": None, "tipo": None, "ciudad": None})
    lo, hi = RANGOS["pm25"]
    for f in filas:
        r["leidas"] += 1
        if not es_horaria(f.get("duraci_n")):
            r["no_horaria"] += 1
            continue
        if desde or hasta:
            try:
                dia = datetime.fromisoformat(str(f["med_fecha_inicio"])[:19]).date()
            except (KeyError, TypeError, ValueError):
                r["hora_invalida"] += 1
                continue
            if (desde and dia < desde) or (hasta and dia > hasta):
                r["fuera_de_periodo"] += 1
                continue
        if f.get("sigla_unidad") != UNIDAD:
            r["unidad"] += 1
            continue
        v = f.get("med_concentracion_estandar")
        if v is None or str(v).strip() == "":
            r["nulos"] += 1
            continue
        try:
            v = float(v)
        except ValueError:
            r["no_numericos"] += 1
            continue
        if v < 0:
            r["negativos"] += 1
            continue
        if not lo <= v <= hi:
            r["fuera_de_rango"] += 1
            continue
        try:
            t = hora_utc(f["med_fecha_final"])
        except (KeyError, TypeError, ValueError):
            r["hora_invalida"] += 1
            continue
        station = f"{f['nombre_est'].strip()} (IDEAM {f['estacion_id']})"
        if (station, t) in vistas:
            r["duplicadas"] += 1
            continue
        vistas.add((station, t))
        city = (f.get("municipio") or "").strip() or "Valle de Aburrá"
        salida.append({"source": SOURCE, "station": station, "city": city, "measured_at": t,
                       "pm25": round(v, 1), "pm10": None, "temperature": None, "humidity": None,
                       "license": LICENCIA})
        e = por_est[station]
        e["n"] += 1
        e["tipo"], e["ciudad"] = f.get("tipo_estacion"), city
        e["primera"] = t if e["primera"] is None else min(e["primera"], t)
        e["ultima"] = t if e["ultima"] is None else max(e["ultima"], t)
    r["validas"] = len(salida)
    return salida, {"conteos": dict(r), "estaciones": dict(por_est)}


def aplicar_limpieza(filas, res):
    """Ceros exactos y rachas (tools/limpieza_pm25.py). Recalcula validas y el resumen por estacion."""
    limpias, rl = limpieza_pm25.limpiar(filas)
    antes = res["estaciones"]
    por_est = {}
    for f in limpias:
        e = por_est.setdefault(f["station"], {"n": 0, "primera": f["measured_at"], "ultima": f["measured_at"],
                                             "tipo": antes.get(f["station"], {}).get("tipo"), "ciudad": f["city"]})
        e["n"] += 1
        e["primera"] = min(e["primera"], f["measured_at"])
        e["ultima"] = max(e["ultima"], f["measured_at"])
    res["conteos"]["validas"] = len(limpias)
    res["estaciones"] = por_est
    res["limpieza"] = rl
    return limpias, res


def imprimir_resumen(res, where):
    c = res["conteos"]
    print(f"Fuente: {SOURCE} · licencia: {LICENCIA} · filtro: {where}")
    print(f"Filas leidas: {c.get('leidas', 0)} · validas: {c.get('validas', 0)}")
    for k, txt in (("nulos", "nulos"), ("negativos", "negativos"), ("fuera_de_rango", "> 1000 ug/m3"),
                   ("no_numericos", "no numericos"), ("no_horaria", "duracion distinta de 60 min"),
                   ("unidad", "unidad distinta de ug/m3"), ("hora_invalida", "hora invalida"),
                   ("fuera_de_periodo", "fuera del periodo pedido"),
                   ("duplicadas", "duplicadas (misma estacion y hora)")):
        print(f"  descartadas por {txt}: {c.get(k, 0)}")
    if "limpieza" in res:
        cl = res["limpieza"]["conteos"]
        print(f"  descartadas por cero exacto (falla del equipo): {cl.get('ceros', 0)}")
        print(f"  rachas de {limpieza_pm25.MIN_RACHA_HORAS}+ horas con el mismo valor: {cl.get('rachas', 0)} "
              f"({cl.get('horas_en_rachas', 0)} horas descartadas)")
        for r in res["limpieza"]["rachas"][:20]:
            print(f"    {r['station']} · {r['inicio']:%Y-%m-%d %H:%M} -> {r['fin']:%Y-%m-%d %H:%M} UTC · "
                  f"{r['horas']} h con {r['valor']:g} ug/m3")
        if len(res["limpieza"]["rachas"]) > 20:
            print(f"    ... y {len(res['limpieza']['rachas']) - 20} rachas mas")
    print(f"Estaciones: {len(res['estaciones'])}")
    for st, e in sorted(res["estaciones"].items()):
        print(f"  {st} · {e['ciudad']} · {e['tipo']} · {e['n']} horas · "
              f"{e['primera']:%Y-%m-%d %H:%M} -> {e['ultima']:%Y-%m-%d %H:%M} UTC")


def main(argv=None, conectar=None, abrir=None):
    p = argparse.ArgumentParser(description="Importa PM2.5 horario del IDEAM (AMVA) a external_measurements")
    p.add_argument("--desde", type=date.fromisoformat, default=date(2020, 1, 1))
    p.add_argument("--hasta", type=date.fromisoformat, default=date(2024, 12, 31))
    p.add_argument("--estacion", help="estacion_id del IDEAM (p. ej. 29509) para probar con una sola")
    p.add_argument("--estaciones", help="lista de codigos separados por coma (p. ej. 29509,29493)")
    p.add_argument("--min-horas", type=int, help="solo estaciones con al menos N horas en el rango")
    p.add_argument("--listar", action="store_true",
                   help="lista estaciones con sus horas y el tamano estimado; no descarga mediciones ni toca la base")
    p.add_argument("--pagina", type=int, default=50000)
    p.add_argument("--batch-size", type=int, default=1000)
    p.add_argument("--dry-run", action="store_true", help="descarga y valida, sin tocar la base")
    p.add_argument("--local", action="store_true", help="usa la base local (DB_*), ignorando DATABASE_URL")
    args = p.parse_args(argv)
    if args.hasta < args.desde:
        print("ERROR: --hasta es anterior a --desde")
        return 2
    try:
        codigos = codigos_estacion(",".join(x for x in (args.estacion, args.estaciones) if x)) or None
        if args.listar or args.min_horas is not None:
            listado = seleccionar(contar_por_estacion(args.desde, args.hasta, abrir), codigos, args.min_horas)
            if args.listar:
                imprimir_listado(listado, args.desde, args.hasta)
                return 0
            codigos = [e["estacion_id"] for e in listado]
            if not codigos:
                print(f"Ninguna estacion tiene al menos {args.min_horas} horas en el rango: nada que importar.")
                return 0
            print(f"Estaciones con >= {args.min_horas} horas: {len(codigos)} ({', '.join(codigos)})")
        where = filtro_soql(args.desde, args.hasta, codigos)
        filas, res = aplicar_limpieza(*convertir(descargar(where, args.pagina, abrir), args.desde, args.hasta))
    except (ValueError, OSError) as e:
        print(f"ERROR: {e}")
        return 2
    imprimir_resumen(res, where)
    if args.dry_run or not filas:
        print("Modo --dry-run: no se escribio nada." if args.dry_run else "Nada que cargar.")
        return 0

    try:
        dsn, destino = destino_importacion(args.local)
    except ValueError as e:
        print(f"ERROR: {e}")
        return 2
    print(f"Base de destino: {destino} ({'LOCAL, DB_*' if args.local else 'DATABASE_URL'})")
    if conectar is None:
        import psycopg
        conectar = lambda: psycopg.connect(dsn, connect_timeout=10, autocommit=True)  # noqa: E731
    try:
        with conectar() as conn:
            if not conn.execute("SELECT to_regclass('external_measurements') IS NOT NULL").fetchone()[0]:
                print("ERROR: falta la tabla external_measurements (migracion 006). No se aplica aqui.")
                return 2
            insertadas, existian = insertar(conn, filas, args.batch_size)
    except Exception as e:   # sin URL ni clave
        print(f"ERROR de base de datos: {error_conexion_seguro(e)}")
        return 2
    print(f"Insertadas: {insertadas} · ya existian (omitidas): {existian}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
