import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from calidad_aire import SQL_ULTIMA, SQL_VENTANAS, construir_respuesta, ventanas_desde_fila

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
    params = {"device_id": device_id, "ahora": ahora}
    ventanas = await _con_timeout(query(SQL_VENTANAS, params))
    ultima = await _con_timeout(query(SQL_ULTIMA, params))
    ventana_1h, ventana_24h = ventanas_desde_fila(ventanas[0])
    return construir_respuesta(device_id, ventana_1h, ventana_24h, ultima[0] if ultima else None, ahora)


class AlertDetalle(BaseModel):
    id: int
    device_id: str
    kind: str
    rule_code: Optional[str] = None
    severity: Optional[str] = None
    message: str
    detected_at: datetime
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    abierta: bool
    details: Optional[dict] = None
    is_simulated: bool


@app.get("/devices/{device_id}/alerts", response_model=list[AlertDetalle])
async def device_alerts(
    device_id: str,
    estado: Literal["abiertas", "todas"] = "todas",
    limit: int = Query(50, ge=1, le=500),
):
    """Alertas del servicio alerter (requiere db/migrations/004_alerts.sql).

    abiertas = ended_at IS NULL y started_at no nulo (las filas anteriores a la 004 no cuentan).
    """
    await _con_timeout(require_device(device_id))
    try:
        return await _con_timeout(query(
            "SELECT id, device_id, kind, rule_code, severity, message, detected_at, started_at, "
            "ended_at, last_seen_at, (ended_at IS NULL AND started_at IS NOT NULL) AS abierta, "
            "details, is_simulated "
            "FROM alerts WHERE device_id = %(device_id)s "
            "AND (%(estado)s = 'todas' OR (ended_at IS NULL AND started_at IS NOT NULL)) "
            "ORDER BY COALESCE(started_at, detected_at) DESC LIMIT %(limit)s",
            {"device_id": device_id, "estado": estado, "limit": limit},
        ))
    except psycopg.errors.UndefinedColumn:
        raise HTTPException(
            status_code=503,
            detail="Falta aplicar db/migrations/004_alerts.sql (columnas nuevas de alerts)",
        )
