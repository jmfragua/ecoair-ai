"""Generador de datos SIMULADOS para EcoAir AI (dispositivo ecoair-sim-01).

ESTOS DATOS SON SIMULADOS. Sirven para probar el software y entrenar el modelo de
anomalias solo como prueba. No son mediciones reales ni evidencia de precision.

Supuestos:
- 30 dias que terminan en --end (por defecto, ahora en UTC truncado al minuto), una fila
  por minuto. Con la misma --seed y el mismo --end el resultado es identico.
- Las horas del dia se calculan en hora Colombia (UTC-5, sin horario de verano).
- Temperatura (Medellin): ciclo diario con minimo hacia las 3 h y maximo hacia las 15 h;
  cada dia tiene su propia media y amplitud (aprox. 17 a 29 C) mas ruido pequeno.
- Humedad: inversa a la temperatura, aprox. 45 a 95 %, con ruido; mas alta en dias de lluvia.
- PM2.5: base diaria de 6 a 18 ug/m3, picos suaves en horas pico (7-9 h y 17-19 h),
  30 % menos en fin de semana y 40 % menos en dias de lluvia.
- PM10 = PM2.5 x una relacion entre 1.3 y 2.2, asi que PM10 >= PM2.5 siempre.
- Todo se recorta a los rangos del contrato (temperatura -40..80, humedad 0..100,
  PM 0..1000) y se redondea a 1 decimal.
- Eventos anomalos inyectados sin solaparse (etiquetas en sim_events.csv, end_utc exclusivo):
  pico_corto, episodio_largo, sensor_congelado, temperatura_imposible, hueco_de_datos.
  Un hueco_de_datos es un intervalo sin filas.

Uso:
  python simular_datos.py                      # genera ai/data/sim_*.csv
  python simular_datos.py --to-db              # ademas inserta en PostgreSQL (requiere
                                               # la migracion db/migrations/002_simulated.sql)
"""

import argparse
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

DEVICE_ID = "ecoair-sim-01"
DAYS = 30
COLOMBIA_OFFSET = pd.Timedelta(hours=5)
VARIABLES = ["temperature", "humidity", "pm25", "pm10"]
RANGES = {"temperature": (-40, 80), "humidity": (0, 100), "pm25": (0, 1000), "pm10": (0, 1000)}
EVENT_TYPES = ["pico_corto", "episodio_largo", "sensor_congelado", "temperatura_imposible", "hueco_de_datos"]
DATA_DIR = Path(__file__).resolve().parent / "data"
BATCH_SIZE = 1000


def _parse_end(end):
    if end is None:
        return pd.Timestamp.now(tz="UTC").floor("min")
    ts = pd.Timestamp(end)
    ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
    return ts.floor("min")


def _place(rng, n, duration, taken, margin=30, edge=60):
    """Elige el inicio (indice de minuto) de un evento sin solaparse con los ya colocados."""
    for _ in range(1000):
        start = int(rng.integers(edge, n - edge - duration))
        end = start + duration
        if all(end + margin <= s or start >= e + margin for s, e in taken):
            taken.append((start, end))
            return start
    raise RuntimeError("no se pudo colocar el evento sin solapamiento")


def generar(seed=42, end=None, days=DAYS):
    """Devuelve (mediciones, eventos) como DataFrames. Datos SIMULADOS."""
    rng = np.random.default_rng(seed)
    end = _parse_end(end)
    idx = pd.date_range(end=end, periods=days * 24 * 60 + 1, freq="min", tz="UTC")[:-1]
    n = len(idx)

    local = idx - COLOMBIA_OFFSET
    hour = local.hour.to_numpy() + local.minute.to_numpy() / 60
    day, _ = pd.factorize(local.normalize())
    n_days = day.max() + 1
    weekend = local.dayofweek.to_numpy() >= 5

    # Parametros por dia
    temp_mean = rng.uniform(22, 24, n_days)
    temp_amp = rng.uniform(3.5, 5, n_days)
    pm_base = rng.uniform(6, 18, n_days)
    pm_ratio = rng.uniform(1.4, 2.0, n_days)
    rain = np.zeros(n_days, dtype=bool)
    rain[rng.choice(n_days, size=int(rng.integers(4, 7)), replace=False)] = True

    def smooth_noise(scale, window=15):
        noise = pd.Series(rng.normal(0, 1, n)).rolling(window, min_periods=1).mean()
        return noise.to_numpy() * scale

    temperature = (
        temp_mean[day] + temp_amp[day] * np.cos(2 * np.pi * (hour - 15) / 24) + rng.normal(0, 0.2, n)
    )
    humidity = 70 - 3.2 * (temperature - 23) + 8 * rain[day] + smooth_noise(4)
    humidity = np.clip(humidity, 45, 95)

    rush = np.exp(-0.5 * ((hour - 8) / 0.8) ** 2) + np.exp(-0.5 * ((hour - 18) / 0.8) ** 2)
    pm25 = (
        pm_base[day]
        * np.where(weekend, 0.7, 1.0)
        * np.where(rain[day], 0.6, 1.0)
        * (1 + 0.6 * rush)
        + smooth_noise(2.5)
    )
    pm25 = np.clip(pm25, 1.0, None)

    # Eventos anomalos (indices de minuto, end exclusivo)
    events = []
    taken = []

    def add(event_type, start, duration, description):
        events.append({
            "event_type": event_type,
            "start_utc": idx[start],
            "end_utc": idx[start] + pd.Timedelta(minutes=duration),
            "description": description,
            "_start": start,
            "_end": start + duration,
        })

    for _ in range(2):
        dur = int(rng.integers(3 * 60, 6 * 60 + 1))
        s = _place(rng, n, dur, taken)
        level = rng.uniform(50, 110)
        pm25[s:s + dur] = np.clip(level + rng.normal(0, level * 0.05, dur), 50, 110)
        add("episodio_largo", s, dur, f"PM2.5 alrededor de {level:.1f} ug/m3 durante {dur} min")

    for _ in range(2):
        dur = int(rng.integers(20, 91))
        s = _place(rng, n, dur, taken)
        add("hueco_de_datos", s, dur, f"Sin filas durante {dur} min")

    for _ in range(2):
        dur = int(rng.integers(30, 61))
        s = _place(rng, n, dur, taken)
        add("sensor_congelado", s, dur, f"Todos los valores repetidos durante {dur} min")

    for _ in range(int(rng.integers(5, 9))):
        dur = int(rng.integers(5, 21))
        s = _place(rng, n, dur, taken)
        peak = rng.uniform(80, 250)
        pm25[s:s + dur] = np.clip(peak + rng.normal(0, peak * 0.05, dur), 80, 250)
        add("pico_corto", s, dur, f"PM2.5 alrededor de {peak:.1f} ug/m3 durante {dur} min")

    temp_events = []
    for _ in range(int(rng.integers(2, 4))):
        s = _place(rng, n, 1, taken)
        temp_events.append((s, rng.uniform(55, 65)))

    ratio = np.clip(pm_ratio[day] + rng.normal(0, 0.05, n), 1.3, 2.2)
    df = pd.DataFrame({
        "device_id": DEVICE_ID,
        "received_at": idx,
        "temperature": temperature,
        "humidity": humidity,
        "pm25": pm25,
        "pm10": pm25 * ratio,
        "is_simulated": True,
    })

    for s, value in temp_events:
        normal = df.at[s, "temperature"]
        df.at[s, "temperature"] = value
        add("temperatura_imposible", s, 1, f"Temperatura {value:.1f} C (la normal era {normal:.1f} C)")

    for col in VARIABLES:
        lo, hi = RANGES[col]
        df[col] = df[col].clip(lo, hi).round(1)

    # Congelado despues de redondear, para que los valores sean identicos
    for e in events:
        if e["event_type"] == "sensor_congelado":
            df.loc[e["_start"]:e["_end"] - 1, VARIABLES] = df.loc[e["_start"], VARIABLES].to_numpy()

    gap = np.zeros(n, dtype=bool)
    for e in events:
        if e["event_type"] == "hueco_de_datos":
            gap[e["_start"]:e["_end"]] = True
    df = df[~gap].reset_index(drop=True)

    events_df = (
        pd.DataFrame(events)
        .sort_values("start_utc")
        .drop(columns=["_start", "_end"])
        .reset_index(drop=True)
    )
    return df, events_df


def guardar_csv(df, events_df, out_dir=DATA_DIR):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_dir / "sim_measurements.csv", index=False)
    events_df.to_csv(out_dir / "sim_events.csv", index=False)
    return out_dir / "sim_measurements.csv", out_dir / "sim_events.csv"


def insertar_db(df):
    import psycopg

    dsn = (
        f"host={os.getenv('DB_HOST', 'localhost')} port={os.getenv('DB_PORT', '5433')} "
        f"dbname=ecoair user=ecoair password={os.environ['POSTGRES_PASSWORD']}"
    )
    rows = [
        (r.device_id, r.received_at.to_pydatetime(), r.temperature, r.humidity, r.pm25, r.pm10)
        for r in df.itertuples(index=False)
    ]
    with psycopg.connect(dsn) as conn:
        has_column = conn.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'measurements' AND column_name = 'is_simulated'"
        ).fetchone()
        has_device = conn.execute("SELECT 1 FROM devices WHERE device_id = %s", (DEVICE_ID,)).fetchone()
        if not has_column or not has_device:
            sys.exit("Falta aplicar db/migrations/002_simulated.sql (columna is_simulated "
                     f"o dispositivo {DEVICE_ID}). No se inserto nada.")
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "DELETE FROM measurements WHERE device_id = %s AND is_simulated = true",
                (DEVICE_ID,),
            )
            print(f"Borradas {cur.rowcount} filas simuladas anteriores de {DEVICE_ID}")
            for i in range(0, len(rows), BATCH_SIZE):
                cur.executemany(
                    "INSERT INTO measurements "
                    "(device_id, received_at, temperature, humidity, pm25, pm10, is_simulated) "
                    "VALUES (%s, %s, %s, %s, %s, %s, true)",
                    rows[i:i + BATCH_SIZE],
                )
    print(f"Insertadas {len(rows)} filas simuladas en measurements")


def resumen(df, events_df):
    print("=== DATOS SIMULADOS (ecoair-sim-01) ===")
    print(f"Filas generadas: {len(df)}")
    print(f"Desde {df.received_at.min()} hasta {df.received_at.max()} (UTC)")
    print("\nVariable       min      max    media")
    for col in VARIABLES:
        print(f"{col:<12} {df[col].min():>6.1f} {df[col].max():>8.1f} {df[col].mean():>8.1f}")
    print("\nEventos por tipo:")
    counts = events_df.event_type.value_counts()
    for t in EVENT_TYPES:
        print(f"  {t:<22} {counts.get(t, 0)}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Genera datos SIMULADOS para ecoair-sim-01")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--end", help="fin del periodo en UTC (ISO 8601); por defecto, ahora")
    parser.add_argument("--out-dir", default=str(DATA_DIR))
    parser.add_argument("--to-db", action="store_true", help="insertar en PostgreSQL")
    args = parser.parse_args(argv)

    df, events_df = generar(seed=args.seed, end=args.end)
    paths = guardar_csv(df, events_df, args.out_dir)
    resumen(df, events_df)
    print(f"\nGuardado en {paths[0]} y {paths[1]}")
    if args.to_db:
        insertar_db(df)


if __name__ == "__main__":
    main()
