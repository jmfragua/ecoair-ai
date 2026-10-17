"""Resumen de los datos de la base. SOLO LECTURA.

Muestra por dispositivo (measurements): mediciones, primera y ultima fecha, horas con datos
(minutos distintos con al menos una medicion / 60) y si son reales o simuladas; y el conteo de
external_measurements por fuente.

Usa la misma configuracion del proyecto: DATABASE_URL o DB_HOST/DB_PORT/DB_NAME/DB_USER/
POSTGRES_PASSWORD (ver shared/settings.py). Nunca imprime usuario ni clave.

Solo lectura en dos capas: solo ejecuta SELECT, y la sesion se abre en modo read-only
(la base rechazaria cualquier escritura).

  python db/resumen_datos.py
"""

import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from shared.settings import descripcion_db, dsn_desde_entorno  # noqa: E402

TZ = "America/Bogota"

SQL_EXISTE = "SELECT to_regclass(%s) IS NOT NULL"
SQL_TIENE_IS_SIMULATED = (
    "SELECT EXISTS (SELECT 1 FROM information_schema.columns "
    "WHERE table_name = 'measurements' AND column_name = 'is_simulated')"
)
SQL_DISPOSITIVOS = """
SELECT d.device_id,
       count(m.device_id)                                              AS mediciones,
       min(m.received_at)                                              AS primera,
       max(m.received_at)                                              AS ultima,
       round(count(DISTINCT date_trunc('minute', m.received_at)) / 60.0, 1) AS horas_con_datos,
       {simulado}
FROM devices d LEFT JOIN measurements m ON m.device_id = d.device_id
GROUP BY d.device_id
ORDER BY d.device_id
"""
SIMULADO_SI = "bool_and(m.is_simulated) AS todas_simuladas, bool_or(m.is_simulated) AS alguna_simulada"
SIMULADO_NO = "NULL::boolean AS todas_simuladas, NULL::boolean AS alguna_simulada"
SQL_EXTERNOS = """
SELECT source, count(*) AS mediciones, count(DISTINCT station) AS estaciones,
       string_agg(DISTINCT city, ', ' ORDER BY city) AS ciudades,
       min(measured_at) AS primera, max(measured_at) AS ultima,
       count(pm10) AS con_pm10,
       string_agg(DISTINCT license, ' | ' ORDER BY license) AS licencias
FROM external_measurements
GROUP BY source
ORDER BY source
"""
CONSULTAS = (SQL_EXISTE, SQL_TIENE_IS_SIMULATED, SQL_DISPOSITIVOS, SQL_EXTERNOS)


def tipo_datos(mediciones, todas_simuladas, alguna_simulada):
    """'reales', 'simuladas', 'MIXTAS (revisar)', 'sin datos' o 'desconocido (falta 002)'."""
    if not mediciones:
        return "sin datos"
    if todas_simuladas is None and alguna_simulada is None:
        return "desconocido (falta migracion 002)"
    if todas_simuladas:
        return "simuladas"
    if alguna_simulada:
        return "MIXTAS (revisar)"
    return "reales"


def fecha(dt):
    """datetime -> 'AAAA-MM-DD HH:MM' en hora de Colombia (UTC-5 fijo); None -> '-'."""
    if dt is None:
        return "-"
    from datetime import timedelta, timezone
    return dt.astimezone(timezone(timedelta(hours=-5))).strftime("%Y-%m-%d %H:%M")


def tabla(columnas, filas):
    """Tabla de texto alineada. filas: listas de valores ya convertidos a str."""
    anchos = [max(len(c), *(len(f[i]) for f in filas)) if filas else len(c) for i, c in enumerate(columnas)]
    linea = lambda vals: "  ".join(v.ljust(a) for v, a in zip(vals, anchos))  # noqa: E731
    return "\n".join([linea(columnas), linea(["-" * a for a in anchos]), *(linea(f) for f in filas)])


def resumen(conn):
    """Devuelve el texto del resumen. Solo ejecuta SELECT."""
    def uno(sql, params=()):
        return conn.execute(sql, params).fetchone()[0]

    partes = []
    if not uno(SQL_EXISTE, ("measurements",)):
        return "La tabla measurements no existe (aplique db/schema.sql con db/migrate.py)."
    simulado = SIMULADO_SI if uno(SQL_TIENE_IS_SIMULATED) else SIMULADO_NO
    filas = conn.execute(SQL_DISPOSITIVOS.format(simulado=simulado)).fetchall()
    partes.append("Dispositivos (measurements) · fechas en hora de Colombia")
    partes.append(tabla(
        ["dispositivo", "tipo", "mediciones", "primera", "ultima", "horas con datos"],
        [[r[0], tipo_datos(r[1], r[5], r[6]), str(r[1]), fecha(r[2]), fecha(r[3]), f"{r[4]:.1f}"]
         for r in filas],
    ))

    partes.append("")
    if not uno(SQL_EXISTE, ("external_measurements",)):
        partes.append("Datos externos: la tabla external_measurements no existe (migracion 006 sin aplicar).")
    else:
        ext = conn.execute(SQL_EXTERNOS).fetchall()
        partes.append("Datos externos (external_measurements) por fuente · fechas en hora de Colombia")
        partes.append(tabla(
            ["fuente", "mediciones", "estaciones", "ciudades", "primera", "ultima", "con PM10", "licencia"],
            [[r[0], str(r[1]), str(r[2]), r[3] or "-", fecha(r[4]), fecha(r[5]), str(r[6]), r[7] or "-"]
             for r in ext],
        ) if ext else "(sin datos externos cargados)")
    return "\n".join(partes)


def main(conectar=None):
    if conectar is None:
        import psycopg

        def conectar():
            conn = psycopg.connect(dsn_desde_entorno(connect_timeout=10))
            conn.read_only = True   # la sesion rechaza INSERT/UPDATE/DELETE/DDL
            return conn
    print(f"Base de datos: {descripcion_db()} (solo lectura)\n")
    conn = conectar()
    try:
        print(resumen(conn))
    finally:
        conn.rollback()             # no hay nada que confirmar: es solo lectura
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
