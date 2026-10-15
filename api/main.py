import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from calidad_aire import construir_respuesta

DB_DSN = (
    f"host={os.getenv('DB_HOST', 'localhost')} port={os.getenv('DB_PORT', '5433')} "
    f"dbname=ecoair user=ecoair password={os.environ['POSTGRES_PASSWORD']}"
)

HEALTH_TIMEOUT_SECONDS = 3
QUERY_TIMEOUT_SECONDS = 5

# check: verifica cada conexion antes de entregarla y descarta las rotas
# (p. ej. tras reiniciar Postgres). connect_timeout (libpq) acota cada intento de conexion.
pool = AsyncConnectionPool(
    DB_DSN + " connect_timeout=3",
    open=False,
    kwargs={"row_factory": dict_row},
    check=AsyncConnectionPool.check_connection,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await pool.open()
    yield
    await pool.close()


app = FastAPI(title="EcoAir AI API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:4200", "http://localhost:3000"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


class Device(BaseModel):
    device_id: str
    name: Optional[str] = None
    location: Optional[str] = None
    created_at: datetime


class Measurement(BaseModel):
    id: int
    device_id: str
    received_at: datetime
    temperature: float
    humidity: float
    pm25: float
    pm10: float


class Alert(BaseModel):
    id: int
    device_id: str
    measurement_id: Optional[int] = None
    detected_at: datetime
    kind: str
    score: Optional[float] = None
    message: str


async def query(sql, params=()):
    async with pool.connection() as conn:
        cur = await conn.execute(sql, params)
        return await cur.fetchall()


async def require_device(device_id: str):
    rows = await query("SELECT 1 FROM devices WHERE device_id = %s", (device_id,))
    if not rows:
        raise HTTPException(status_code=404, detail=f"Dispositivo '{device_id}' no existe")


@app.get("/health")
async def health():
    try:
        await asyncio.wait_for(query("SELECT 1"), timeout=HEALTH_TIMEOUT_SECONDS)
    except Exception:  # incluye TimeoutError de wait_for
        raise HTTPException(status_code=503, detail="Base de datos no disponible")
    return {"status": "ok"}


@app.get("/devices", response_model=list[Device])
async def list_devices():
    return await query("SELECT device_id, name, location, created_at FROM devices ORDER BY device_id")


@app.get("/devices/{device_id}/measurements/latest", response_model=Measurement)
async def latest_measurement(device_id: str):
    await require_device(device_id)
    rows = await query(
        "SELECT id, device_id, received_at, temperature, humidity, pm25, pm10 "
        "FROM measurements WHERE device_id = %s ORDER BY received_at DESC LIMIT 1",
        (device_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Aun no hay mediciones para este dispositivo")
    return rows[0]


@app.get("/devices/{device_id}/measurements", response_model=list[Measurement])
async def list_measurements(
    device_id: str,
    since: Optional[datetime] = None,
    limit: int = Query(100, ge=1, le=1000),
):
    await require_device(device_id)
    return await query(
        "SELECT id, device_id, received_at, temperature, humidity, pm25, pm10 "
        "FROM measurements WHERE device_id = %s AND (%s::timestamptz IS NULL OR received_at >= %s) "
        "ORDER BY received_at DESC LIMIT %s",
        (device_id, since, since, limit),
    )


@app.get("/alerts", response_model=list[Alert])
async def list_alerts(
    device_id: Optional[str] = None,
    limit: int = Query(50, ge=1, le=500),
):
    if device_id is not None:
        await require_device(device_id)
    return await query(
        "SELECT id, device_id, measurement_id, detected_at, kind, score, message "
        "FROM alerts WHERE (%s::text IS NULL OR device_id = %s) "
        "ORDER BY detected_at DESC LIMIT %s",
        (device_id, device_id, limit),
    )


async def _con_timeout(coro):
    try:
        return await asyncio.wait_for(coro, timeout=QUERY_TIMEOUT_SECONDS)
    except TimeoutError:
        raise HTTPException(status_code=503, detail="La base de datos no respondió a tiempo")


@app.get("/devices/{device_id}/air-quality/current")
async def air_quality_current(device_id: str):
    await _con_timeout(require_device(device_id))
    ahora = datetime.now(timezone.utc)
    ventanas = await _con_timeout(query(
        "SELECT "
        "avg(pm25) FILTER (WHERE received_at > %(ahora)s - interval '1 hour') AS pm25_1h, "
        "avg(pm10) FILTER (WHERE received_at > %(ahora)s - interval '1 hour') AS pm10_1h, "
        "count(*) FILTER (WHERE received_at > %(ahora)s - interval '1 hour') AS muestras_1h, "
        "count(DISTINCT date_trunc('minute', received_at)) "
        "  FILTER (WHERE received_at > %(ahora)s - interval '1 hour') AS minutos_1h, "
        "avg(pm25) AS pm25_24h, avg(pm10) AS pm10_24h, count(*) AS muestras_24h, "
        "count(DISTINCT date_trunc('minute', received_at)) AS minutos_24h "
        "FROM measurements "
        "WHERE device_id = %(device_id)s "
        "AND received_at > %(ahora)s - interval '24 hours' AND received_at <= %(ahora)s",
        {"device_id": device_id, "ahora": ahora},
    ))
    ultima = await _con_timeout(query(
        "SELECT received_at, temperature, humidity FROM measurements "
        "WHERE device_id = %s AND received_at <= %s ORDER BY received_at DESC LIMIT 1",
        (device_id, ahora),
    ))
    v = ventanas[0]
    return construir_respuesta(
        device_id,
        {"pm25_media": v["pm25_1h"], "pm10_media": v["pm10_1h"],
         "muestras": v["muestras_1h"], "minutos_con_datos": v["minutos_1h"]},
        {"pm25_media": v["pm25_24h"], "pm10_media": v["pm10_24h"],
         "muestras": v["muestras_24h"], "minutos_con_datos": v["minutos_24h"]},
        ultima[0] if ultima else None,
        ahora,
    )
