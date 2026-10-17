"""Estacion SIMULADA para la demo: carga mediciones inventadas en ecoair-sim-01 (is_simulated=true).

TODO LO QUE CARGA ESTE SCRIPT ES SIMULADO. Nunca toca dispositivos con datos reales: si el
dispositivo tiene alguna medicion con is_simulated=false, se detiene sin escribir nada.

Usa la configuracion de base de datos del proyecto (DATABASE_URL o DB_*; ver shared/settings.py).

Que hace:
- Genera una medicion por minuto desde hace --horas hasta el final del episodio + --margen-min
  (o hasta ahora si no hay episodio): ciclo diario suave de PM, temperatura y humedad, con ruido.
- Episodio de contaminacion opcional (--episodio-inicio, --episodio-duracion-min,
  --episodio-pico-pm25) con subida y bajada SUAVES (coseno), para que se vea como un episodio y
  no como un salto artificial.
- "Programar" el episodio: si el inicio es futuro, las filas se cargan YA con hora futura. La API,
  el dashboard y el alerter solo leen received_at <= ahora, asi que el episodio aparece cuando
  llega su hora. Ojo: cuando se acaban las filas futuras, la estacion "deja de enviar" y, con el
  alerter evaluando simulados, se abriria dispositivo_caido (tambien marcada como simulacion).
- Antes de cargar, borra SOLO las filas simuladas de ese dispositivo en el mismo intervalo
  (repetirlo no duplica).
- Calcula y muestra si el episodio disparara ica_1h (media de 1 h >= 38 ug/m3 -> ICA >= 101).

Ejemplos:
  python tools/simular_estacion.py --horas 24 --dry-run
  python tools/simular_estacion.py --horas 24 --episodio-inicio +20m --episodio-duracion-min 90 \\
      --episodio-pico-pm25 120
  python tools/simular_estacion.py --episodio-inicio 2026-11-05T10:30-05:00 --episodio-pico-pm25 150
"""

import argparse
import math
import random
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
for _p in (RAIZ, RAIZ / "api"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from shared.settings import descripcion_db, dsn_desde_entorno  # noqa: E402
from ica import calcular_ica  # noqa: E402

DEVICE_POR_DEFECTO = "ecoair-sim-01"
COLOMBIA = timezone(timedelta(hours=-5))
UMBRAL_ICA_1H_PM25 = 38          # PM2.5 >= 38 -> ICA >= 101 (danina_sensibles): regla ica_1h
SUBIDA = BAJADA = 0.25           # fraccion del episodio que dura la subida y la bajada
LOTE = 1000

SQL_REALES = "SELECT count(*) FROM measurements WHERE device_id = %s AND NOT is_simulated"
SQL_DEVICE = ("INSERT INTO devices (device_id, name, location) VALUES (%s, 'Estación simulada', 'Simulado') "
              "ON CONFLICT (device_id) DO NOTHING")
SQL_BORRAR = ("DELETE FROM measurements WHERE device_id = %s AND is_simulated "
              "AND received_at BETWEEN %s AND %s")
SQL_INSERT = ("INSERT INTO measurements (device_id, received_at, temperature, humidity, pm25, pm10, "
              "is_simulated) VALUES (%s, %s, %s, %s, %s, %s, true)")


# ---------- Tiempo ----------

def parsear_momento(texto, ahora):
    """'+20m', '-1h', '+90s' (relativo a ahora) o ISO 8601 con zona. Devuelve datetime UTC."""
    m = re.fullmatch(r"([+-])(\d+)([smh])", texto.strip())
    if m:
        n = int(m.group(2)) * {"s": 1, "m": 60, "h": 3600}[m.group(3)]
        return ahora + timedelta(seconds=n if m.group(1) == "+" else -n)
    dt = datetime.fromisoformat(texto.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("la hora del episodio debe llevar zona (p. ej. 2026-11-05T10:30-05:00) o ser relativa (+20m)")
    return dt.astimezone(timezone.utc)


# ---------- Generacion ----------

def perfil_episodio(t, inicio, duracion):
    """Intensidad 0..1 del episodio en el instante t: subida y bajada en coseno, meseta en medio."""
    if duracion <= timedelta(0) or t < inicio or t > inicio + duracion:
        return 0.0
    f = (t - inicio) / duracion
    if f < SUBIDA:
        return 0.5 - 0.5 * math.cos(math.pi * f / SUBIDA)
    if f > 1 - BAJADA:
        return 0.5 - 0.5 * math.cos(math.pi * (1 - f) / BAJADA)
    return 1.0


def generar(desde, hasta, semilla=7, episodio=None):
    """Una fila por minuto en [desde, hasta]. episodio: (inicio, duracion, pico_pm25) o None.

    Devuelve dicts: received_at (UTC), temperature, humidity, pm25, pm10. Todo simulado.
    """
    rng = random.Random(semilla)
    t = desde.replace(second=0, microsecond=0)
    ruido_pm = 0.0
    filas = []
    while t <= hasta:
        hora = (t.astimezone(COLOMBIA).hour + t.minute / 60)
        # PM base: mas alto en horas pico (7-9 h y 17-19 h), ruido con memoria (no salta minuto a minuto)
        base = 9 + 4 * math.exp(-((hora - 8) / 1.5) ** 2) + 5 * math.exp(-((hora - 18) / 1.5) ** 2)
        ruido_pm = 0.9 * ruido_pm + rng.gauss(0, 0.6)
        pm25 = base + ruido_pm
        if episodio:
            inicio, duracion, pico = episodio
            pm25 += max(0.0, pico - base) * perfil_episodio(t, inicio, duracion)
        pm25 = min(1000.0, max(1.0, pm25))
        pm10 = min(1000.0, pm25 * (1.6 + rng.gauss(0, 0.05)))
        temp = 22 + 4 * math.cos(2 * math.pi * (hora - 15) / 24) + rng.gauss(0, 0.2)
        hum = min(100.0, max(0.0, 70 - 3 * (temp - 22) + rng.gauss(0, 1.0)))
        filas.append({"received_at": t, "temperature": round(temp, 1), "humidity": round(hum, 1),
                      "pm25": round(pm25, 1), "pm10": round(max(pm10, pm25), 1)})
        t += timedelta(minutes=1)
    return filas


def prever_disparo(filas, umbral=UMBRAL_ICA_1H_PM25):
    """Media movil de 1 h de PM2.5 (como la regla ica_1h). Devuelve (max_media, ica, categoria, primer_cruce).

    primer_cruce: primer minuto en que la media de 1 h llega al umbral, o None.
    """
    if not filas:
        return None
    suma, ventana, max_media, cruce = 0.0, [], 0.0, None
    for f in filas:
        ventana.append(f)
        suma += f["pm25"]
        while f["received_at"] - ventana[0]["received_at"] >= timedelta(hours=1):
            suma -= ventana.pop(0)["pm25"]
        media = suma / len(ventana)
        if len(ventana) >= 30:   # al menos media hora de datos para que la media tenga sentido
            max_media = max(max_media, media)
            if cruce is None and media >= umbral:
                cruce = f["received_at"]
    r = calcular_ica("pm25", max_media)
    return round(max_media, 1), r["ica"], r["category_name"], cruce


def verificar_dispositivo(n_reales, device_id):
    """Nunca se escriben simulaciones en un dispositivo con datos reales."""
    if n_reales:
        raise ValueError(f"{device_id} tiene {n_reales} mediciones REALES (is_simulated=false): "
                         "no se cargan simulaciones en un dispositivo real.")


# ---------- CLI ----------

def main(argv=None, conectar=None, ahora=None):
    p = argparse.ArgumentParser(description="Carga una estacion SIMULADA (is_simulated=true)")
    p.add_argument("--device", default=DEVICE_POR_DEFECTO)
    p.add_argument("--horas", type=float, default=24, help="horas de historia hacia atras (por defecto 24)")
    p.add_argument("--episodio-inicio", help="'+20m', '-1h' o ISO 8601 con zona")
    p.add_argument("--episodio-duracion-min", type=int, default=90)
    p.add_argument("--episodio-pico-pm25", type=float, default=120.0)
    p.add_argument("--margen-min", type=int, default=60, help="minutos de datos tras el episodio")
    p.add_argument("--semilla", type=int, default=7)
    p.add_argument("--dry-run", action="store_true", help="genera y resume, sin escribir en la base")
    args = p.parse_args(argv)

    ahora = (ahora or datetime.now(timezone.utc)).replace(second=0, microsecond=0)
    desde = ahora - timedelta(hours=args.horas)
    episodio = None
    try:
        if args.episodio_inicio:
            if not 0 < args.episodio_pico_pm25 <= 1000 or args.episodio_duracion_min <= 0:
                raise ValueError("pico entre 0 y 1000 ug/m3 y duracion > 0")
            inicio = parsear_momento(args.episodio_inicio, ahora)
            episodio = (inicio, timedelta(minutes=args.episodio_duracion_min), args.episodio_pico_pm25)
            hasta = max(ahora, inicio + episodio[1] + timedelta(minutes=args.margen_min))
        else:
            hasta = ahora
    except ValueError as e:
        print(f"ERROR: {e}")
        return 2

    filas = generar(desde, hasta, args.semilla, episodio)
    col = lambda dt: dt.astimezone(COLOMBIA).strftime("%Y-%m-%d %H:%M")  # noqa: E731
    print("*** DATOS SIMULADOS (is_simulated=true) — no son mediciones de un sensor real ***")
    print(f"Dispositivo: {args.device} · {len(filas)} filas · {col(desde)} -> {col(hasta)} (hora de Colombia)")
    futuras = sum(1 for f in filas if f["received_at"] > ahora)
    if futuras:
        print(f"  {futuras} filas tienen hora futura: aparecen en el panel a medida que llega su hora.")
    if episodio:
        inicio, dur, pico = episodio
        media, ica, cat, cruce = prever_disparo(filas)
        print(f"Episodio: {col(inicio)} -> {col(inicio + dur)} · pico PM2.5 {pico:g} ug/m3")
        print(f"  Media de 1 h maxima prevista: {media} ug/m3 -> ICA {ica} ({cat})")
        if cruce:
            print(f"  ica_1h deberia abrirse poco despues de {col(cruce)} (el alerter necesita 2 revisiones "
                  "de 60 s y evaluar simulados: ALERTER_INCLUIR_SIMULADOS=true).")
        else:
            print(f"  Con estos parametros la media de 1 h NO llega a {UMBRAL_ICA_1H_PM25} ug/m3: ica_1h no se abrira.")
    if args.dry_run:
        print("Modo --dry-run: no se escribio nada.")
        return 0

    if conectar is None:
        import psycopg
        conectar = lambda: psycopg.connect(dsn_desde_entorno(connect_timeout=10), autocommit=True)  # noqa: E731
    print(f"Base de datos: {descripcion_db()}")
    with conectar() as conn:
        try:
            verificar_dispositivo(conn.execute(SQL_REALES, (args.device,)).fetchone()[0], args.device)
        except ValueError as e:
            print(f"ERROR: {e}")
            return 2
        with conn.transaction():
            conn.execute(SQL_DEVICE, (args.device,))
            borradas = conn.execute(SQL_BORRAR, (args.device, filas[0]["received_at"],
                                                 filas[-1]["received_at"])).rowcount
            with conn.cursor() as cur:
                for i in range(0, len(filas), LOTE):
                    cur.executemany(SQL_INSERT, [
                        (args.device, f["received_at"], f["temperature"], f["humidity"], f["pm25"], f["pm10"])
                        for f in filas[i:i + LOTE]])
    print(f"Listo: {borradas} filas simuladas anteriores reemplazadas, {len(filas)} insertadas (is_simulated=true).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
