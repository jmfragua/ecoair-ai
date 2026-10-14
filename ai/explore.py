import os

import pandas as pd
import psycopg

dsn = (
    f"host=localhost port={os.getenv('DB_PORT', '5433')} dbname=ecoair "
    f"user=ecoair password={os.environ['POSTGRES_PASSWORD']}"
)
cols = ["received_at", "temperature", "humidity", "pm25", "pm10"]

with psycopg.connect(dsn) as conn:
    rows = conn.execute(
        "SELECT received_at, temperature, humidity, pm25, pm10 "
        "FROM measurements WHERE device_id = 'ecoair-node-01' ORDER BY received_at"
    ).fetchall()

df = pd.DataFrame(rows, columns=cols)
df[cols[1:]] = df[cols[1:]].astype(float)

print(f"Filas: {len(df)}")
print(f"Desde {df.received_at.min()} hasta {df.received_at.max()} (UTC)")
print("\nResumen estadistico:")
print(df[cols[1:]].describe().round(2))

gaps = df.received_at.diff().dt.total_seconds().dropna()
print(f"\nIntervalo entre lecturas (s): mediana={gaps.median():.1f}, maximo={gaps.max():.1f}")
print(f"Huecos de mas de 15 s: {(gaps > 15).sum()}")
print(f"Valores nulos: {int(df.isna().sum().sum())}")

df.to_csv("data/real_measurements.csv", index=False)
print("\nGuardado en data/real_measurements.csv")
