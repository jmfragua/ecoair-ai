"""Estacion SIMULADA continua para la demostracion: ecoair-sim-01 recibe datos INVENTADOS en tiempo real.

TODO LO QUE ESCRIBE ESTE SCRIPT ES SIMULADO (is_simulated=true). Reutiliza tools/simular_estacion.py
(SQL, comprobacion de dispositivo real, perfil suave de los episodios) pero, a diferencia de ese
script, NUNCA inserta filas con hora futura: cada fila tiene received_at <= ahora.

Garantias:
- Solo dispositivos "ecoair-sim-NN". Si el dispositivo tiene alguna medicion real (is_simulated=false)
  se detiene sin escribir nada. Nunca escribe en ecoair-node-01.
- Una fila por minuto (como lo cuentan la API y el predictor: minutos con datos). Solo inserta los
  minutos que faltan: repetirlo o reiniciarlo no duplica.
- Reproducible: el valor de cada minuto depende solo de la semilla y de la hora (no de cuando se
  ejecute ni de en cuantos pasos), asi que el relleno y el modo continuo dan la misma serie.
- Patron diario plausible: PM mas alto en las horas pico (7-9 h y 17-20 h de Colombia), temperatura y
  humedad con ciclo diario, ruido pequeno y suave, y algun episodio de PM alto (~1 de cada 3 dias,
  decidido por la semilla) con subida y bajada en coseno.
- Base de datos: con --local usa SOLO DB_* (localhost:5433 por defecto) aunque exista DATABASE_URL.
  Sin --local exige DATABASE_URL (p. ej. Railway): muestra el destino (host:puerto/base, nunca usuario
  ni clave) y pide escribir el host para confirmar (o --confirmar-destino HOST para dejarlo corriendo).

Modo servicio (Railway, simulador/Dockerfile): --servicio. No hace nada salvo con SIM_ACTIVO=true.
Entonces lee DATABASE_URL (obligatoria; nunca se imprime, solo host:puerto/base), rellena SIM_BACKFILL_HORAS
(48) al arrancar y sigue cada SIM_CADA_MIN (5) hasta SIM_HASTA (viernes 6 nov 17:00 de Colombia) y termina.
Sin confirmacion interactiva (SIM_ACTIVO=true es la confirmacion), con las mismas protecciones:
solo ecoair-sim-NN, se detiene si el dispositivo tiene datos reales y nunca escribe horas futuras.
Opcionales: SIM_DEVICE (ecoair-sim-01), SIM_SEMILLA (2026).

Ejemplos:
  python tools/simular_continuo.py --local --backfill-horas 48 --dry-run
  python tools/simular_continuo.py --local --backfill-horas 48              # una pasada y termina
  python tools/simular_continuo.py --local --backfill-horas 48 --continuo   # cada 5 min hasta el viernes
"""

import argparse
import hashlib
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

RAIZ = Path(__file__).resolve().parent.parent
for _p in (RAIZ, RAIZ / "api", RAIZ / "tools"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from shared.settings import destino_importacion, error_conexion_seguro  # noqa: E402
from simular_estacion import (  # noqa: E402
    COLOMBIA,
    SQL_DEVICE,
    SQL_INSERT,
    SQL_REALES,
    perfil_episodio,
    verificar_dispositivo,
)

DEVICE_POR_DEFECTO = "ecoair-sim-01"
PATRON_DEVICE = re.compile(r"ecoair-sim-\d+")
HASTA_POR_DEFECTO = "2026-11-06T17:00-05:00"     # viernes 6 de noviembre, 17:00 de Colombia
SEMILLA_POR_DEFECTO = 2026
NODO_MIN = 20                                    # nudos del ruido suave cada 20 min
PROB_EPISODIO = 0.35                             # ~1 de cada 3 dias
LOTE = 1000
VERDADERO = ("1", "true", "si", "sí", "yes")
AVISO = "*** DATOS SIMULADOS (is_simulated=true) — inventados para demostración, no son de un sensor real ***"

SQL_EXISTENTES = ("SELECT date_trunc('minute', received_at) AS t FROM measurements "
                  "WHERE device_id = %s AND received_at >= %s AND received_at <= %s")
SQL_FUTURAS = "SELECT count(*) FROM measurements WHERE device_id = %s AND received_at > %s"


# ---------- Generacion determinista (semilla + hora) ----------

def _u(semilla, *clave):
    """Uniforme en [0, 1) que depende solo de (semilla, clave)."""
    h = hashlib.blake2b(f"{semilla}|{'|'.join(map(str, clave))}".encode(), digest_size=8).digest()
    return int.from_bytes(h, "big") / 2 ** 64


def _g(semilla, *clave):
    """Normal estandar (Box-Muller) que depende solo de (semilla, clave)."""
    u1 = max(_u(semilla, *clave, "a"), 1e-12)
    return math.sqrt(-2 * math.log(u1)) * math.cos(2 * math.pi * _u(semilla, *clave, "b"))


def _suave(semilla, nombre, minuto, paso=NODO_MIN):
    """Ruido suave ~N(0, 1): nudos aleatorios cada 'paso' minutos unidos en coseno (sin saltos)."""
    k, r = divmod(minuto, paso)
    w = 0.5 - 0.5 * math.cos(math.pi * r / paso)
    return (1 - w) * _g(semilla, nombre, k) + w * _g(semilla, nombre, k + 1)


def episodio_del_dia(dia, semilla):
    """(inicio UTC, duracion, pico PM2.5) del episodio de ese dia de Colombia, o None."""
    if _u(semilla, "ep", dia.isoformat()) >= PROB_EPISODIO:
        return None
    hora = 6 + 14 * _u(semilla, "ep-hora", dia.isoformat())           # empieza entre 6:00 y 20:00
    dur = timedelta(minutes=int(60 + 120 * _u(semilla, "ep-dur", dia.isoformat())))
    pico = round(45 + 65 * _u(semilla, "ep-pico", dia.isoformat()), 1)
    inicio = datetime(dia.year, dia.month, dia.day, tzinfo=COLOMBIA) + timedelta(hours=hora)
    return inicio.astimezone(timezone.utc).replace(second=0, microsecond=0), dur, pico


def valores(t, semilla=SEMILLA_POR_DEFECTO):
    """Medicion simulada del minuto t (UTC). Mismo t y semilla -> mismo resultado, siempre."""
    t = t.replace(second=0, microsecond=0)
    minuto = int(t.timestamp() // 60)
    local = t.astimezone(COLOMBIA)
    hora = local.hour + local.minute / 60
    # PM2.5: fondo + horas pico, un factor por dia (suave) y ruido suave pequeno
    pico = 5 * math.exp(-((hora - 8) / 1.4) ** 2) + 7 * math.exp(-((hora - 18.5) / 1.7) ** 2)
    factor_dia = 1 + 0.15 * _suave(semilla, "dia", minuto, paso=24 * 60)
    pm25 = (8 + pico) * factor_dia + 1.2 * _suave(semilla, "pm", minuto) + 0.3 * _g(semilla, "pm-b", minuto)
    for d in (local.date() - timedelta(days=1), local.date()):          # un episodio puede cruzar la medianoche
        ep = episodio_del_dia(d, semilla)
        if ep:
            pm25 += max(0.0, ep[2] - pm25) * perfil_episodio(t, ep[0], ep[1])
    pm25 = min(1000.0, max(1.0, pm25))
    pm10 = min(1000.0, max(pm25, pm25 * (1.55 + 0.08 * _suave(semilla, "pm10", minuto)) + 0.4 * _g(semilla, "pm10-b", minuto)))
    temp = 21 + 5 * math.cos(2 * math.pi * (hora - 14.5) / 24) + 0.8 * _suave(semilla, "temp", minuto, paso=180) \
        + 0.1 * _g(semilla, "temp-b", minuto)
    hum = min(98.0, max(25.0, 72 - 3.2 * (temp - 21) + 2 * _suave(semilla, "hum", minuto, paso=60)
                        + 0.4 * _g(semilla, "hum-b", minuto)))
    return {"received_at": t, "temperature": round(temp, 1), "humidity": round(hum, 1),
            "pm25": round(pm25, 1), "pm10": round(pm10, 1)}


def generar(desde, hasta, ahora, semilla=SEMILLA_POR_DEFECTO):
    """Una fila por minuto en [desde, min(hasta, ahora)]. NUNCA devuelve minutos posteriores a 'ahora'."""
    fin = min(hasta, ahora)
    t = desde.replace(second=0, microsecond=0)
    if t < desde:
        t += timedelta(minutes=1)
    filas = []
    while t <= fin:
        filas.append(valores(t, semilla))
        t += timedelta(minutes=1)
    return filas


def faltantes(filas, existentes):
    """Filas cuyo minuto aun no esta en la base (existentes: minutos ya guardados)."""
    ya = {e.replace(second=0, microsecond=0) for e in existentes}
    return [f for f in filas if f["received_at"] not in ya]


def episodios_entre(desde, hasta, semilla):
    """Episodios programados por la semilla que tocan [desde, hasta] (para avisar en la salida)."""
    out, d = [], desde.astimezone(COLOMBIA).date() - timedelta(days=1)
    while d <= hasta.astimezone(COLOMBIA).date():
        ep = episodio_del_dia(d, semilla)
        if ep and ep[0] + ep[1] >= desde and ep[0] <= hasta:
            out.append(ep)
        d += timedelta(days=1)
    return out


# ---------- Comprobaciones de seguridad ----------

def validar_device(device_id):
    """Solo dispositivos de simulacion. Lanza ValueError con cualquier otro (ecoair-node-01 incluido)."""
    if not PATRON_DEVICE.fullmatch(device_id or ""):
        raise ValueError(f"'{device_id}' no es un dispositivo de simulación (se espera ecoair-sim-NN); "
                         "este script nunca escribe en un dispositivo real.")


def host_destino(env):
    """Host de DATABASE_URL (sin usuario ni clave), para confirmar el destino."""
    try:
        return urlsplit((env.get("DATABASE_URL") or "").strip()).hostname or ""
    except ValueError:
        return ""


def confirmar_destino(local, destino, host, confirmado, preguntar):
    """True si se puede escribir. --local no pregunta. Remoto: escribir el host exacto (o --confirmar-destino)."""
    if local:
        return True
    print(f"ATENCIÓN: destino tomado de DATABASE_URL (puede ser Railway): {destino}")
    if confirmado is not None:
        return confirmado == host
    try:
        respuesta = preguntar(f"Escribe el host ({host}) para confirmar que quieres escribir datos SIMULADOS ahí: ")
    except EOFError:
        return False
    return respuesta.strip() == host


def argumentos_servicio(env):
    """argv equivalente para --servicio, desde las variables SIM_*; None si SIM_ACTIVO no es true."""
    if (env.get("SIM_ACTIVO") or "").strip().lower() not in VERDADERO:
        return None
    return ["--device", env.get("SIM_DEVICE") or DEVICE_POR_DEFECTO,
            "--backfill-horas", env.get("SIM_BACKFILL_HORAS") or "48",
            "--continuo", "--cada-min", env.get("SIM_CADA_MIN") or "5",
            "--hasta", env.get("SIM_HASTA") or HASTA_POR_DEFECTO,
            "--semilla", env.get("SIM_SEMILLA") or str(SEMILLA_POR_DEFECTO)]


# ---------- Escritura ----------

def pasada(conn, device_id, desde, hasta, ahora, semilla):
    """Inserta los minutos que faltan en [desde, min(hasta, ahora)]. Devuelve cuantas filas inserto."""
    filas = generar(desde, hasta, ahora, semilla)
    if not filas:
        return 0
    assert all(f["received_at"] <= ahora for f in filas), "nunca filas con hora futura"
    existentes = [r[0] for r in conn.execute(SQL_EXISTENTES, (device_id, filas[0]["received_at"],
                                                              filas[-1]["received_at"])).fetchall()]
    nuevas = faltantes(filas, existentes)
    with conn.transaction():
        verificar_dispositivo(conn.execute(SQL_REALES, (device_id,)).fetchone()[0], device_id)
        with conn.cursor() as cur:
            for i in range(0, len(nuevas), LOTE):
                cur.executemany(SQL_INSERT, [(device_id, f["received_at"], f["temperature"], f["humidity"],
                                              f["pm25"], f["pm10"]) for f in nuevas[i:i + LOTE]])
    return len(nuevas)


def _col(dt):
    return dt.astimezone(COLOMBIA).strftime("%Y-%m-%d %H:%M")


def main(argv=None, conectar=None, reloj=None, dormir=time.sleep, preguntar=input, env=None):
    env = dict(os.environ if env is None else env)
    reloj = reloj or (lambda: datetime.now(timezone.utc))
    p = argparse.ArgumentParser(description="Estación SIMULADA continua (is_simulated=true, sin horas futuras)")
    p.add_argument("--device", default=DEVICE_POR_DEFECTO)
    p.add_argument("--backfill-horas", type=float, default=0, help="rellena las últimas N horas (p. ej. 48)")
    p.add_argument("--continuo", action="store_true", help="repite cada --cada-min hasta --hasta")
    p.add_argument("--cada-min", type=float, default=5)
    p.add_argument("--hasta", default=HASTA_POR_DEFECTO, help="ISO 8601 con zona (por defecto viernes 6 nov 17:00 de Colombia)")
    p.add_argument("--semilla", type=int, default=SEMILLA_POR_DEFECTO)
    p.add_argument("--local", action="store_true", help="base local con DB_* (ignora DATABASE_URL)")
    p.add_argument("--confirmar-destino", metavar="HOST", help="host de DATABASE_URL, para correr sin preguntar")
    p.add_argument("--dry-run", action="store_true", help="genera y resume, sin conectar a la base")
    p.add_argument("--servicio", action="store_true",
                   help="Railway: configuracion por SIM_* y DATABASE_URL; no hace nada sin SIM_ACTIVO=true")
    args = p.parse_args(argv)
    servicio = args.servicio
    if servicio:
        if args.local:
            print("ERROR: --servicio usa DATABASE_URL; no se combina con --local")
            return 2
        argv_servicio = argumentos_servicio(env)
        if argv_servicio is None:
            print("SIM_ACTIVO no es true: el simulador no hace nada.")
            return 0
        args = p.parse_args(argv_servicio)

    try:
        validar_device(args.device)
        hasta = datetime.fromisoformat(args.hasta)
        if hasta.tzinfo is None:
            raise ValueError("--hasta debe llevar zona (p. ej. 2026-11-06T17:00-05:00)")
        if args.backfill_horas < 0 or args.cada_min <= 0:
            raise ValueError("--backfill-horas >= 0 y --cada-min > 0")
    except ValueError as e:
        print(f"ERROR: {e}")
        return 2
    hasta = hasta.astimezone(timezone.utc)
    ahora = reloj()
    desde = ahora - timedelta(hours=args.backfill_horas) if args.backfill_horas else ahora - timedelta(minutes=args.cada_min)

    print(AVISO)
    print(f"Dispositivo: {args.device} · semilla {args.semilla} · hasta {_col(hasta)} (hora de Colombia)")
    for ini, dur, pico in episodios_entre(desde, hasta, args.semilla):
        print(f"  Episodio simulado: {_col(ini)} -> {_col(ini + dur)} · pico PM2.5 {pico:g} µg/m³")
    if args.dry_run:
        filas = generar(desde, hasta, ahora, args.semilla)
        if filas:
            pm = [f["pm25"] for f in filas]
            print(f"Modo --dry-run: {len(filas)} filas {_col(filas[0]['received_at'])} -> "
                  f"{_col(filas[-1]['received_at'])} · PM2.5 {min(pm)}–{max(pm)} µg/m³ · ninguna futura. "
                  "No se escribió nada.")
        else:
            print("Modo --dry-run: nada que generar (--hasta ya pasó). No se escribió nada.")
        return 0

    try:
        dsn, destino = destino_importacion(args.local, env)
    except ValueError as e:
        print(f"ERROR: {e}")
        return 2
    print(f"Base de datos: {destino} ({'local, DB_*' if args.local else 'DATABASE_URL'})")
    if servicio:
        print("Modo servicio (SIM_ACTIVO=true): sin confirmación interactiva.")
    elif not confirmar_destino(args.local, destino, host_destino(env), args.confirmar_destino, preguntar):
        print("Cancelado: el destino no se confirmó. No se escribió nada.")
        return 3
    if conectar is None:
        import psycopg
        conectar = lambda: psycopg.connect(dsn, autocommit=True, connect_timeout=10)  # noqa: E731

    conn = None
    try:
        while True:
            ahora = reloj()
            try:
                if conn is None:
                    conn = conectar()
                    conn.execute(SQL_DEVICE, (args.device,))
                    futuras = conn.execute(SQL_FUTURAS, (args.device, ahora)).fetchone()[0]
                    if futuras:
                        print(f"  AVISO: {args.device} ya tiene {futuras} filas con hora futura (de "
                              "tools/simular_estacion.py); este script no las crea ni las borra.")
                n = pasada(conn, args.device, desde, hasta, ahora, args.semilla)
                print(f"{_col(ahora)} · {n} filas simuladas nuevas (hasta {_col(min(ahora, hasta))})", flush=True)
                desde = ahora - timedelta(minutes=10)      # la siguiente pasada cubre tambien los ultimos minutos
            except ValueError as e:                        # dispositivo con datos reales: se detiene
                print(f"ERROR: {e}")
                return 2
            except Exception as e:                         # base caida u otro fallo: se reintenta en la siguiente
                if type(e).__module__.split(".")[0] != "psycopg":
                    raise
                print(f"{_col(ahora)} · error de base de datos, se reintenta: {error_conexion_seguro(e, env)}", flush=True)
                try:
                    conn and conn.close()
                except Exception:
                    pass
                conn = None
            if not args.continuo or ahora >= hasta:
                break
            dormir(args.cada_min * 60)
    except KeyboardInterrupt:
        print("Detenido con Ctrl+C.")
    finally:
        if conn is not None:
            conn.close()
    if args.continuo:
        print(f"Fin de la simulación ({_col(hasta)}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
