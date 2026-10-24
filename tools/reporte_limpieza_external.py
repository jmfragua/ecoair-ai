"""Reporte de SOLO LECTURA de external_measurements: ceros exactos y rachas, por fuente y estacion.

Aplica las mismas reglas de tools/limpieza_pm25.py (0 exacto = falla del equipo; rachas de 6+ horas
seguidas con exactamente el mismo valor), pero NO borra ni modifica nada: la sesion es read-only y
termina con rollback. Sirve para decidir si la limpieza se aplica solo al armar el entrenamiento.

Base: DATABASE_URL o DB_* (shared/settings.py). Nunca imprime la URL ni credenciales. Antes de
conectar dice el origen (DATABASE_URL o DB_*) y el destino host:puerto/base; si falla, muestra el
tipo, el codigo y el mensaje de error con la URL y la clave ocultas (error_conexion_seguro).

  python tools/reporte_limpieza_external.py
  python tools/reporte_limpieza_external.py --detalle 30      # ademas lista las primeras 30 rachas
"""

import argparse
import sys
from itertools import groupby
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
for p in (RAIZ, Path(__file__).resolve().parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import limpieza_pm25  # noqa: E402
from shared.settings import descripcion_db, dsn_desde_entorno, error_conexion_seguro, origen_db  # noqa: E402

SQL = ("SELECT source, station, measured_at, pm25 FROM external_measurements "
       "ORDER BY source, station, measured_at")


def leer_filas(conn):
    """Filas (source, station, measured_at, pm25) con cursor del servidor (no carga todo en memoria)."""
    with conn.cursor(name="reporte_limpieza") as cur:
        cur.itersize = 20000
        cur.execute(SQL)
        yield from cur


def resumir(filas):
    """filas ordenadas por fuente, estacion y hora -> (lista por estacion, rachas)."""
    grupos, rachas = [], []
    for (source, station), g in groupby(filas, key=lambda f: (f[0], f[1])):
        lista = [{"station": station, "measured_at": t, "pm25": None if v is None else float(v)} for _, _, t, v in g]
        _, res = limpieza_pm25.limpiar(lista)
        c = res["conteos"]
        grupos.append({"source": source, "station": station, "filas": len(lista),
                       "primera": lista[0]["measured_at"], "ultima": lista[-1]["measured_at"],
                       "ceros": c.get("ceros", 0), "rachas": c.get("rachas", 0),
                       "horas_en_rachas": c.get("horas_en_rachas", 0)})
        rachas += [{"source": source, **r} for r in res["rachas"]]
    return grupos, rachas


def _pct(a, b):
    return f"{100 * a / b:.2f} %" if b else "—"


def imprimir_reporte(grupos, rachas, detalle=0):
    print("| fuente | estacion | filas | periodo (UTC) | ceros exactos | rachas 6+ h | horas en rachas | % afectado |")
    print("|---|---|---|---|---|---|---|---|")
    for g in grupos:
        afect = g["ceros"] + g["horas_en_rachas"]
        print(f"| {g['source']} | {g['station']} | {g['filas']} | {g['primera']:%Y-%m-%d} -> {g['ultima']:%Y-%m-%d} | "
              f"{g['ceros']} | {g['rachas']} | {g['horas_en_rachas']} | {_pct(afect, g['filas'])} |")
    print("\nTotales por fuente:")
    for source, gs in groupby(grupos, key=lambda g: g["source"]):
        gs = list(gs)
        n = sum(g["filas"] for g in gs)
        ce = sum(g["ceros"] for g in gs)
        ra = sum(g["rachas"] for g in gs)
        hr = sum(g["horas_en_rachas"] for g in gs)
        print(f"  {source}: {len(gs)} estaciones · {n} filas · {ce} ceros · {ra} rachas ({hr} horas) · "
              f"afectado {_pct(ce + hr, n)}")
    if detalle and rachas:
        print(f"\nPrimeras {min(detalle, len(rachas))} rachas (de {len(rachas)}):")
        for r in rachas[:detalle]:
            print(f"  {r['source']} · {r['station']} · {r['inicio']:%Y-%m-%d %H:%M} -> {r['fin']:%Y-%m-%d %H:%M} UTC · "
                  f"{r['horas']} h con {r['valor']:g} ug/m3")
    print("\nSolo lectura: no se borro ni modifico ninguna fila.")


def main(argv=None, conectar=None, leer=leer_filas):
    p = argparse.ArgumentParser(description="Reporte read-only de ceros y rachas en external_measurements")
    p.add_argument("--detalle", type=int, default=0, help="lista las primeras N rachas")
    args = p.parse_args(argv)
    origen = origen_db()
    print(f"Base de datos: {descripcion_db()} · origen: {origen} · solo lectura")
    if origen != "DATABASE_URL":
        print("  Aviso: DATABASE_URL no esta definida en este proceso; se usan DB_* o sus valores por defecto "
              "(localhost:5433/ecoair). Exportala en esta misma terminal si querias otra base.")
    if conectar is None:
        import psycopg
        # Igual que resumen_db_solo_lectura.py: el timeout va como argumento, sin tocar la URL.
        conectar = lambda: psycopg.connect(dsn_desde_entorno(), connect_timeout=10, autocommit=False)  # noqa: E731
    try:
        conn = conectar()
    except Exception as e:   # tipo, codigo y mensaje, sin URL ni clave
        print(f"No se pudo conectar: {error_conexion_seguro(e)}")
        return 2
    try:
        conn.read_only = True
        if not conn.execute("SELECT to_regclass('external_measurements') IS NOT NULL").fetchone()[0]:
            print("No existe la tabla external_measurements (migracion 006 sin aplicar).")
            return 0
        conn.execute("SET LOCAL statement_timeout = '120s'")
        grupos, rachas = resumir(leer(conn))
        if not grupos:
            print("external_measurements esta vacia.")
            return 0
        imprimir_reporte(grupos, rachas, args.detalle)
        return 0
    except Exception as e:   # p. ej. statement_timeout: mismo formato seguro
        print(f"Error durante la consulta: {error_conexion_seguro(e)}")
        return 2
    finally:
        conn.rollback()
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
