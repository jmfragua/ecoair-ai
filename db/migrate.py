"""Aplica el esquema y las migraciones SQL en orden y las registra en schema_migrations.

Uso (lee DATABASE_URL o DB_HOST/DB_PORT/DB_NAME/DB_USER/POSTGRES_PASSWORD; ver shared/settings.py):
  python db/migrate.py --dry-run     # muestra que se aplicaria, sin cambiar nada
  python db/migrate.py               # aplica lo pendiente
  python db/migrate.py --baseline    # SOLO para una base que ya tiene todo aplicado (la local):
                                     # registra los archivos como aplicados SIN ejecutarlos

Orden: db/schema.sql y despues db/migrations/*.sql por nombre. Se EXCLUYEN los borradores
(EXCLUIDAS). Cada archivo se aplica en su propia transaccion junto con su registro: si falla,
no queda a medias. Si un archivo ya registrado cambio (otro SHA-256), se detiene sin tocar nada.
Las migraciones se corren a mano; no se ejecutan en el despliegue.
"""

import argparse
import hashlib
import sys
from pathlib import Path

DB_DIR = Path(__file__).resolve().parent
RAIZ = DB_DIR.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from shared.settings import descripcion_db, dsn_desde_entorno  # noqa: E402

EXCLUIDAS = {"003_air_levels_borrador.sql"}

SQL_TABLA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   TEXT PRIMARY KEY,
    sha256     TEXT NOT NULL,
    modo       TEXT NOT NULL CHECK (modo IN ('aplicada', 'baseline')),
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def sha256(texto):
    return hashlib.sha256(texto.encode("utf-8")).hexdigest()


def archivos(db_dir=DB_DIR):
    """[(nombre, ruta)] en orden de aplicacion, sin los excluidos."""
    out = [("schema.sql", db_dir / "schema.sql")]
    for ruta in sorted((db_dir / "migrations").glob("*.sql")):
        if ruta.name not in EXCLUIDAS:
            out.append((ruta.name, ruta))
    return out


def planificar(locales, registradas):
    """locales: [(nombre, sha)] en orden; registradas: {nombre: sha}.

    Devuelve (pendientes, conflictos). conflictos = registradas con otro contenido.
    """
    pendientes = [(n, h) for n, h in locales if n not in registradas]
    conflictos = [n for n, h in locales if n in registradas and registradas[n] != h]
    return pendientes, conflictos


def _registradas(conn):
    existe = conn.execute("SELECT to_regclass('schema_migrations') IS NOT NULL").fetchone()[0]
    if not existe:
        return {}
    return dict(conn.execute("SELECT filename, sha256 FROM schema_migrations").fetchall())


def main(argv=None):
    parser = argparse.ArgumentParser(description="Migraciones SQL de EcoAir AI")
    modo = parser.add_mutually_exclusive_group()
    modo.add_argument("--dry-run", action="store_true", help="solo muestra lo pendiente")
    modo.add_argument("--baseline", action="store_true",
                      help="registra todo como aplicado sin ejecutarlo (base que ya lo tiene)")
    args = parser.parse_args(argv)

    import psycopg  # solo hace falta al conectar; los tests usan las funciones puras

    lista = archivos()
    contenido = {n: r.read_text(encoding="utf-8") for n, r in lista}
    locales = [(n, sha256(contenido[n])) for n, _ in lista]
    print(f"Base de datos: {descripcion_db()}")
    print(f"Excluidas (borradores): {', '.join(sorted(EXCLUIDAS))}")

    with psycopg.connect(dsn_desde_entorno(connect_timeout=10), autocommit=True) as conn:
        registradas = _registradas(conn)
        pendientes, conflictos = planificar(locales, registradas)
        if conflictos:
            print("ERROR: estos archivos ya aplicados cambiaron de contenido (otro SHA-256): "
                  + ", ".join(conflictos) + ". No se aplica nada. Crea una migracion nueva en su lugar.")
            return 2
        print(f"Ya registradas: {len(registradas)}. Pendientes: {len(pendientes)}"
              + (" -> " + ", ".join(n for n, _ in pendientes) if pendientes else ""))
        if args.dry_run or not pendientes:
            print("Modo --dry-run: no se cambió nada." if args.dry_run else "Nada que hacer.")
            return 0

        conn.execute(SQL_TABLA)
        for nombre, h in pendientes:
            with conn.transaction():
                if not args.baseline:
                    conn.execute(contenido[nombre])  # sin parametros: admite varias sentencias
                conn.execute(
                    "INSERT INTO schema_migrations (filename, sha256, modo) VALUES (%s, %s, %s)",
                    (nombre, h, "baseline" if args.baseline else "aplicada"),
                )
            print(f"  {'registrada (baseline)' if args.baseline else 'aplicada'}: {nombre}")
    print("Listo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
