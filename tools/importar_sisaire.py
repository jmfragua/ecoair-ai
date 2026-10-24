"""Importa PM2.5 HORARIO de los CSV del SISAIRE (IDEAM) a external_measurements.

Formato del reporte del SISAIRE: columnas "Estacion","Fecha inicial","Fecha final","PM2.5", una fila
por hora ("2026-01-01 22:00","2026-01-01 22:59"), orden descendente, UTF-8. Los archivos viven FUERA
del repositorio (p. ej. ~/Documents/datos_sisaire/); este script no copia ni guarda datos.

- Tabla: reutiliza external_measurements (migracion 006); no crea ni aplica migraciones.
- source = 'IDEAM SISAIRE'; license = LICENCIA (condiciones del IDEAM; NO es CC BY-SA). La estacion se
  guarda con el nombre del SISAIRE. Nunca se mezcla con 'IDEAM datos.gov.co' ni con OpenAQ.
- Hora (SUPUESTO, igual que tools/importar_ideam.py): las fechas vienen sin zona y se toman como hora
  de Colombia (UTC-5). Se guarda en UTC el FIN de la hora: "Fecha inicial" + 1 h (22:00-22:59 -> 23:00
  local). Se exige que "Fecha final" sea la inicial + 59 min; si no, la fila se descarta.
- Limpieza (tools/limpieza_pm25.py): nulos, negativos, 0 exacto (falla del equipo), > 1000 y rachas de
  6+ horas con el mismo valor; resumen por estacion y % de ceros por año.
- --desde / --hasta (AAAA-MM-DD, inclusive, por el dia LOCAL de "Fecha inicial"; por defecto sin limite):
  p. ej. --desde 2026-01-01 para no duplicar 2025, que ya cubre el IDEAM de datos.gov.co.
- Idempotente: duplicados dentro de la corrida (p. ej. el 2026-01-01 en dos archivos) se cuentan y se
  omiten; en la base, ON CONFLICT (source, station, measured_at) DO NOTHING.
- Base: exige DATABASE_URL (si falta, se detiene) salvo con --local, que usa SOLO DB_* (localhost:5433
  por defecto) aunque DATABASE_URL exista. Muestra el destino antes de escribir; errores de conexion
  sin URL ni clave. --dry-run no se conecta a ninguna base.

  python tools/importar_sisaire.py --archivo ~/Documents/datos_sisaire/reporte_sisaire_2.csv \\
      --archivo ~/Documents/datos_sisaire/reporte_sisaire_3.csv --dry-run
"""

import argparse
import csv
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
for p in (RAIZ, RAIZ / "db", Path(__file__).resolve().parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import limpieza_pm25  # noqa: E402
from load_external import insertar  # noqa: E402
from shared.settings import destino_importacion, error_conexion_seguro  # noqa: E402

SOURCE = "IDEAM SISAIRE"
LICENCIA = "IDEAM SISAIRE – citar la fuente, sin alterar, sin uso comercial"
COLOMBIA = timezone(timedelta(hours=-5))
COLUMNAS = ("Estacion", "Fecha inicial", "Fecha final", "PM2.5")
FORMATO = "%Y-%m-%d %H:%M"


def ciudad_de(estacion):
    return "Medellín" if "MEDELL" in estacion.upper() else "Valle de Aburrá"


def leer_csv(lineas, nombre="csv"):
    """-> (filas {station, city, measured_at UTC, pm25|None, anio_local}, conteos de formato)."""
    lector = csv.DictReader(lineas)
    faltan = [c for c in COLUMNAS if c not in (lector.fieldnames or [])]
    if faltan:
        raise ValueError(f"{nombre}: faltan columnas {', '.join(faltan)} (tiene: {', '.join(lector.fieldnames or [])})")
    filas, c = [], Counter()
    for r in lector:
        c["leidas"] += 1
        est = (r["Estacion"] or "").strip()
        try:
            ini = datetime.strptime(r["Fecha inicial"].strip(), FORMATO)
            fin = datetime.strptime(r["Fecha final"].strip(), FORMATO)
        except (AttributeError, ValueError):
            c["hora_invalida"] += 1
            continue
        if not est:
            c["sin_estacion"] += 1
            continue
        if fin - ini != timedelta(minutes=59):
            c["hora_inconsistente"] += 1
            continue
        texto = (r["PM2.5"] or "").strip()
        try:
            v = float(texto) if texto else None
        except ValueError:
            c["no_numericos"] += 1
            continue
        t = (ini + timedelta(hours=1)).replace(tzinfo=COLOMBIA).astimezone(timezone.utc)
        filas.append({"station": est, "city": ciudad_de(est), "measured_at": t, "pm25": v, "anio": ini.year,
                      "dia": ini.date()})
    return filas, c


def filtrar_periodo(filas, desde=None, hasta=None):
    """Solo las filas cuyo dia local de inicio esta en [desde, hasta]. -> (filas, descartadas)."""
    dentro = [f for f in filas if (desde is None or f["dia"] >= desde) and (hasta is None or f["dia"] <= hasta)]
    return dentro, len(filas) - len(dentro)


def quitar_duplicados(filas):
    """Primera aparicion de cada (estacion, hora); el resto se cuenta (p. ej. el mismo dia en dos archivos).

    Se hace ANTES de la limpieza para que ceros y porcentajes no cuenten dos veces la misma hora.
    """
    vistas, unicas = set(), []
    for f in filas:
        clave = (f["station"], f["measured_at"])
        if clave not in vistas:
            vistas.add(clave)
            unicas.append(f)
    return unicas, len(filas) - len(unicas)


def resumen_por_estacion(filas):
    """Pasa la limpieza estacion por estacion para contar ceros y rachas de cada una."""
    out = {}
    por_est = defaultdict(list)
    for f in filas:
        por_est[f["station"]].append(f)
    for st in sorted(por_est):
        lista = por_est[st]
        validas, res = limpieza_pm25.limpiar(lista)
        ceros_anio = Counter(f["anio"] for f in lista if f["pm25"] == 0)
        horas_anio = Counter(f["anio"] for f in lista if f["pm25"] is not None)
        out[st] = {"conteos": res["conteos"], "rachas": res["rachas"],
                   "pct_ceros_por_anio": {a: round(100 * ceros_anio[a] / n, 1) for a, n in sorted(horas_anio.items())},
                   "primera": min((f["measured_at"] for f in validas), default=None),
                   "ultima": max((f["measured_at"] for f in validas), default=None)}
    return out


def a_filas_tabla(limpias):
    return [{"source": SOURCE, "station": f["station"], "city": f["city"], "measured_at": f["measured_at"],
             "pm25": round(f["pm25"], 1), "pm10": None, "temperature": None, "humidity": None, "license": LICENCIA}
            for f in limpias]


def imprimir_resumen(formato, limpieza, por_est):
    c = limpieza["conteos"]
    print(f"Fuente: {SOURCE} · licencia: {LICENCIA}")
    print(f"Filas leidas: {formato.get('leidas', 0)} · validas tras limpieza: {c.get('validas', 0)}")
    for k, txt in (("hora_invalida", "hora invalida"), ("hora_inconsistente", "fecha final != inicial + 59 min"),
                   ("sin_estacion", "sin estacion"), ("no_numericos", "valor no numerico"),
                   ("fuera_de_periodo", "fuera de --desde/--hasta"),
                   ("duplicadas_entre_archivos", "duplicadas (misma estacion y hora; p. ej. el mismo dia en dos archivos)")):
        print(f"  descartadas por {txt}: {formato.get(k, 0)}")
    for k, txt in (("nulos", "vacios"), ("negativos", "negativos"), ("ceros", "cero exacto (falla del equipo)"),
                   ("mayores_1000", "> 1000 ug/m3")):
        print(f"  descartadas por {txt}: {c.get(k, 0)}")
    print(f"  rachas de {limpieza_pm25.MIN_RACHA_HORAS}+ horas con el mismo valor: {c.get('rachas', 0)} "
          f"({c.get('horas_en_rachas', 0)} horas descartadas)")
    print(f"\nPor estacion (cada una pasada por la limpieza por separado):")
    for st, e in por_est.items():
        ce = e["conteos"]
        pct = " · ".join(f"{a}: {p} %" for a, p in e["pct_ceros_por_anio"].items())
        periodo = (f"{e['primera']:%Y-%m-%d %H:%M} -> {e['ultima']:%Y-%m-%d %H:%M} UTC" if e["primera"] else "sin datos")
        print(f"  {st}\n    validas {ce.get('validas', 0)} · ceros {ce.get('ceros', 0)} · rachas {ce.get('rachas', 0)} "
              f"({ce.get('horas_en_rachas', 0)} h) · {periodo}\n"
              f"    % de ceros por año (hora local): {pct}")
        for r in e["rachas"][:5]:
            print(f"    racha: {r['inicio']:%Y-%m-%d %H:%M} -> {r['fin']:%Y-%m-%d %H:%M} UTC · {r['horas']} h con {r['valor']:g}")


conectar_destino = destino_importacion   # sin --local exige DATABASE_URL; con --local usa solo DB_*


def main(argv=None, conectar=None):
    p = argparse.ArgumentParser(description="Importa CSV horarios del SISAIRE (IDEAM) a external_measurements")
    p.add_argument("--archivo", action="append", type=Path, required=True, help="CSV del SISAIRE (repetible)")
    p.add_argument("--dry-run", action="store_true", help="lee, limpia y resume; no se conecta a ninguna base")
    p.add_argument("--local", action="store_true", help="usa la base local (DB_*), ignorando DATABASE_URL")
    p.add_argument("--desde", type=date.fromisoformat, help="primer dia (hora local) a importar, inclusive")
    p.add_argument("--hasta", type=date.fromisoformat, help="ultimo dia (hora local) a importar, inclusive")
    p.add_argument("--batch-size", type=int, default=1000)
    args = p.parse_args(argv)
    if args.desde and args.hasta and args.hasta < args.desde:
        print("ERROR: --hasta es anterior a --desde")
        return 2
    filas, formato = [], Counter()
    try:
        for ruta in args.archivo:
            ruta = ruta.expanduser()
            with open(ruta, encoding="utf-8-sig", newline="") as fh:
                fs, c = leer_csv(fh, ruta.name)
            print(f"Archivo: {ruta.name} · {c.get('leidas', 0)} filas")
            filas += fs
            formato.update(c)
    except (OSError, ValueError) as e:
        print(f"ERROR: {e}")
        return 2
    filas, formato["fuera_de_periodo"] = filtrar_periodo(filas, args.desde, args.hasta)
    if args.desde or args.hasta:
        print(f"Periodo: {args.desde or 'sin limite'} -> {args.hasta or 'sin limite'} (dia local de la fecha inicial)")
    filas, formato["duplicadas_entre_archivos"] = quitar_duplicados(filas)
    limpias, limpieza = limpieza_pm25.limpiar(filas)
    imprimir_resumen(formato, limpieza, resumen_por_estacion(filas))
    tabla = a_filas_tabla(limpias)
    if args.dry_run or not tabla:
        print("\nModo --dry-run: no se conecto a ninguna base ni se escribio nada." if args.dry_run else "\nNada que cargar.")
        return 0
    try:
        dsn, destino = conectar_destino(args.local)
    except ValueError as e:
        print(f"ERROR: {e}")
        return 2
    print(f"\nBase de destino: {destino} ({'LOCAL, DB_*' if args.local else 'DATABASE_URL'})")
    if conectar is None:
        import psycopg
        conectar = lambda: psycopg.connect(dsn, connect_timeout=10, autocommit=True)  # noqa: E731
    try:
        with conectar() as conn:
            if not conn.execute("SELECT to_regclass('external_measurements') IS NOT NULL").fetchone()[0]:
                print("ERROR: falta la tabla external_measurements (migracion 006). No se aplica aqui.")
                return 2
            insertadas, existian = insertar(conn, tabla, args.batch_size)
    except Exception as e:   # sin URL ni clave
        print(f"ERROR de base de datos: {error_conexion_seguro(e)}")
        return 2
    print(f"Insertadas: {insertadas} · ya existian (omitidas): {existian}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
