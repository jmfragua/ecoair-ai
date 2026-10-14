import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

DB_DSN = (
    f"host={os.getenv('DB_HOST', 'localhost')} port={os.getenv('DB_PORT', '5433')} "
    f"dbname=ecoair user=ecoair password={os.environ['POSTGRES_PASSWORD']}"
)

pool = AsyncConnectionPool(DB_DSN, open=False, kwargs={"row_factory": dict_row})


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
        await query("SELECT 1")
    except Exception:
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
