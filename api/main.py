import asyncio
import os
import sys
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

# shared/ esta en la raiz del repo en desarrollo; en Docker se copia junto al codigo (/app/shared).
_RAIZ = Path(__file__).resolve().parent.parent
if (_RAIZ / "shared").is_dir() and str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from shared.settings import dsn_desde_entorno  # noqa: E402
from shared.prediccion import SQL_SERIE_PM25, cargar_modelo, prediccion, serie_por_minuto  # noqa: E402
from calidad_aire import SQL_ULTIMA, SQL_VENTANAS, construir_respuesta, ventanas_desde_fila
from subscriptions import (
    LIMITE_IP,
    MENSAJE_GENERICO,
    MENSAJE_LIMITE,
    VENTANA_IP_S,
    CONSENT_VERSION,
    LimitadorVentana,
    decidir_registro,
    hash_token,
    ip_cliente,
    normalizar_email,
    texto_consentimiento,
)
import replay  # noqa: E402
from predicciones import (  # noqa: E402
    SQL_METRICAS,
    SQL_ULTIMAS,
    construir_latest,
    construir_metricas,
)
from historial import (
    BUCKET_MAX,
    BUCKET_MIN,
    HORAS_MAX,
    HORAS_MIN,
    SQL_ES_SIMULADO,
    SQL_HISTORIAL,
    construir_historial,
)

# DATABASE_URL (Railway) o DB_HOST/DB_PORT/DB_NAME/DB_USER/POSTGRES_PASSWORD (compose local).
DB_DSN = dsn_desde_entorno(connect_timeout=3)

HEALTH_TIMEOUT_SECONDS = 3
QUERY_TIMEOUT_SECONDS = 5
# Ruta absoluta: funciona igual en Docker (/app/static) y en local (api/static).
STATIC_DIR = Path(__file__).resolve().parent / "static"

# check: verifica cada conexion antes de entregarla y descarta las rotas
# (p. ej. tras reiniciar Postgres). connect_timeout (libpq) acota cada intento de conexion.
pool = AsyncConnectionPool(
    DB_DSN,
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
    # True si sus datos son simulados (segun la medicion mas reciente). El dashboard muestra SIMULACION.
    is_simulated: bool = False


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


# Filas con hora futura (estacion simulada con episodio programado, tools/simular_estacion.py)
# no se muestran hasta que llega su hora: todas las lecturas filtran received_at <= now().
SQL_DEVICES = (
    "SELECT d.device_id, d.name, d.location, d.created_at, "
    "COALESCE((SELECT m.is_simulated FROM measurements m WHERE m.device_id = d.device_id "
    "          AND m.received_at <= now() ORDER BY m.received_at DESC LIMIT 1), "
    "         d.location = 'Simulado') AS is_simulated "
    "FROM devices d ORDER BY d.device_id"
)


@app.get("/devices", response_model=list[Device])
async def list_devices():
    return await query(SQL_DEVICES)


@app.get("/devices/{device_id}/measurements/latest", response_model=Measurement)
async def latest_measurement(device_id: str):
    await require_device(device_id)
    rows = await query(
        "SELECT id, device_id, received_at, temperature, humidity, pm25, pm10 "
        "FROM measurements WHERE device_id = %s AND received_at <= now() "
        "ORDER BY received_at DESC LIMIT 1",
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
        "AND received_at <= now() ORDER BY received_at DESC LIMIT %s",
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


@app.get("/devices/{device_id}/air-quality/history")
async def air_quality_history(
    device_id: str,
    hours: int = Query(24, ge=HORAS_MIN, le=HORAS_MAX),
    bucket_min: int = Query(5, ge=BUCKET_MIN, le=BUCKET_MAX),
):
    """Promedios por intervalo (date_bin) de PM2.5, PM10, temperatura y humedad."""
    await _con_timeout(require_device(device_id))
    ahora = datetime.now(timezone.utc)
    params = {"device_id": device_id, "bucket_min": bucket_min,
              "desde": ahora - timedelta(hours=hours), "ahora": ahora}
    filas = await _con_timeout(query(SQL_HISTORIAL, params))
    sim = await _con_timeout(query(SQL_ES_SIMULADO, params))
    return construir_historial(device_id, filas, ahora, hours, bucket_min,
                               bool(sim and sim[0]["is_simulated"]))


# ---------- Suscripciones a alertas por correo (requiere db/migrations/005) ----------
# El correo de confirmacion lo envia el servicio notifier/: la API nunca usa SMTP ni guarda
# tokens en claro. Respuestas genericas: nunca se revela si un correo ya existe.

limitador_ip = LimitadorVentana(LIMITE_IP, VENTANA_IP_S)
FALTA_005 = "Falta aplicar db/migrations/005_subscriptions.sql"


def _confiar_proxy():
    return os.getenv("TRUST_PROXY_HEADERS", "false").strip().lower() in ("1", "true", "si", "sí", "yes")


class SuscripcionIn(BaseModel):
    email: str
    device_id: str
    accepted_terms: bool
    min_severity: Literal["aviso", "critica"] = "aviso"


@app.get("/subscriptions/consent")
async def subscriptions_consent():
    """Texto de autorizacion (Ley 1581). El contacto sale de CONTACT_EMAIL, sin valor inventado."""
    return texto_consentimiento(os.getenv("CONTACT_EMAIL"))


async def _registrar(email, device_id, min_severity, ahora):
    async with pool.connection() as conn:
        async with conn.transaction():
            envios = await (await conn.execute(
                "SELECT confirm_sent_count, last_confirm_sent_at FROM subscribers WHERE email = %s",
                (email,),
            )).fetchall()
            fila = await (await conn.execute(
                "SELECT id, confirmed_at, unsubscribed_at FROM subscribers "
                "WHERE email = %s AND device_id = %s FOR UPDATE",
                (email, device_id),
            )).fetchone()
            accion = decidir_registro(
                fila, [(e["confirm_sent_count"], e["last_confirm_sent_at"]) for e in envios], ahora)
            if accion == "insertar":
                await conn.execute(
                    "INSERT INTO subscribers (email, device_id, min_severity, consent_at, "
                    "consent_text_version, confirm_requested_at) VALUES (%s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (email, device_id) DO NOTHING",
                    (email, device_id, min_severity, ahora, CONSENT_VERSION, ahora),
                )
            elif accion == "reactivar":
                await conn.execute(
                    "UPDATE subscribers SET min_severity = %s, consent_at = %s, consent_text_version = %s, "
                    "confirm_requested_at = %s, confirmed_at = NULL, unsubscribed_at = NULL, "
                    "confirm_token_hash = NULL, confirm_expires_at = NULL, "
                    "confirm_attempts = 0, confirm_last_attempt_at = NULL WHERE id = %s",
                    (min_severity, ahora, CONSENT_VERSION, ahora, fila["id"]),
                )
    return accion


@app.post("/subscriptions", status_code=202)
async def create_subscription(datos: SuscripcionIn, request: Request):
    ip = ip_cliente(request.client.host if request.client else None, request.headers, _confiar_proxy())
    if not limitador_ip.permitir(ip):
        raise HTTPException(status_code=429, detail=MENSAJE_LIMITE)
    if not datos.accepted_terms:
        raise HTTPException(status_code=422, detail="Debes aceptar el tratamiento de datos para suscribirte")
    email = normalizar_email(datos.email)
    if email is None:
        raise HTTPException(status_code=422, detail="El correo no tiene un formato válido")
    await _con_timeout(require_device(datos.device_id))
    try:
        await _con_timeout(_registrar(email, datos.device_id, datos.min_severity, datetime.now(timezone.utc)))
    except psycopg.errors.UndefinedTable:
        raise HTTPException(status_code=503, detail=FALTA_005)
    return {"mensaje": MENSAJE_GENERICO}


def _pagina(titulo, texto, extra=""):
    """Pagina HTML minima y autonoma (sin recursos externos)."""
    return HTMLResponse(f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{titulo} · EcoAir AI</title>
<style>body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#0d0d0d;color:#f0efec;
font-family:system-ui,-apple-system,"Segoe UI",sans-serif;padding:16px}}main{{max-width:440px;background:#1a1a19;
border:1px solid rgba(255,255,255,.08);border-radius:18px;padding:24px}}h1{{font-size:1.25rem;margin:0 0 8px}}
p{{color:#c3c2b7;line-height:1.5}}a{{color:#f0efec}}button{{font:inherit;font-weight:600;padding:10px 16px;
border-radius:999px;border:0;background:#f0efec;color:#0d0d0d;cursor:pointer}}</style></head>
<body><main><h1>{titulo}</h1><p>{texto}</p>{extra}<p><a href="/panel">Volver al panel</a></p></main></body></html>""")


@app.get("/subscriptions/confirm", response_class=HTMLResponse)
async def confirm_subscription(token: str = ""):
    try:
        filas = await _con_timeout(query(
            "UPDATE subscribers SET confirmed_at = now(), confirm_token_hash = NULL, confirm_expires_at = NULL "
            "WHERE confirm_token_hash = %s AND confirm_expires_at > now() "
            "AND confirmed_at IS NULL AND unsubscribed_at IS NULL RETURNING id",
            (hash_token(token),),
        )) if 0 < len(token) <= 200 else []
    except psycopg.errors.UndefinedTable:
        raise HTTPException(status_code=503, detail=FALTA_005)
    if filas:
        return _pagina("Suscripción confirmada", "Recibirás un correo cuando se abra una alerta en esta estación.")
    return _pagina("Enlace no válido", "El enlace no es válido o ya caducó (dura 24 h). Puedes suscribirte de nuevo desde el panel.")


@app.get("/subscriptions/unsubscribe", response_class=HTMLResponse)
async def unsubscribe_form(token: str = ""):
    """Solo muestra un boton: abrir el enlace (p. ej. un antivirus) NO da de baja."""
    accion = f"/subscriptions/unsubscribe?token={quote(token[:200], safe='')}"
    boton = f'<form method="post" action="{accion}"><button type="submit">Darme de baja</button></form>'
    return _pagina("Darse de baja", "Confirma que ya no quieres recibir alertas por correo de esta estación.", boton)


@app.post("/subscriptions/unsubscribe", response_class=HTMLResponse)
async def unsubscribe(token: str = ""):
    """Da de baja. Tambien sirve para la baja con un clic (cabecera List-Unsubscribe-Post)."""
    try:
        if 0 < len(token) <= 200:
            await _con_timeout(query(
                "UPDATE subscribers SET unsubscribed_at = now() "
                "WHERE unsubscribe_token_hash = %s AND unsubscribed_at IS NULL RETURNING id",
                (hash_token(token),),
            ))
    except psycopg.errors.UndefinedTable:
        raise HTTPException(status_code=503, detail=FALTA_005)
    return _pagina("Baja procesada", "Si el enlace era válido, ya no recibirás más alertas por correo de esta estación.")


# ---------- Prediccion EXPERIMENTAL de PM2.5 (shared/prediccion.py, modelo en modelos/) ----------
# Solo se muestran los horizontes en que el modelo supero a la persistencia en la evaluacion.
PREDICCION_CACHE_S = 60
_cache_prediccion = {}


@app.get("/devices/{device_id}/forecast")
async def forecast(device_id: str):
    await _con_timeout(require_device(device_id))
    ahora = datetime.now(timezone.utc)
    guardado = _cache_prediccion.get(device_id)
    if guardado and (ahora - guardado[0]).total_seconds() < PREDICCION_CACHE_S:
        return guardado[1]
    params = {"device_id": device_id, "ahora": ahora}
    filas = await _con_timeout(query(SQL_SERIE_PM25, params))
    ultima = await _con_timeout(query(SQL_ULTIMA, params))
    serie = serie_por_minuto([(f["t"], f["pm25"]) for f in filas])
    r = prediccion(cargar_modelo(os.getenv("MODELO_PREDICCION_PATH")), serie, ahora)
    # Antiguedad real de la ultima medicion (None si el dispositivo nunca envio datos)
    ult = ultima[0]["received_at"] if ultima else None
    r = {**r, "device_id": device_id, "calculado_utc": ahora.isoformat(timespec="seconds"),
         "ultima_medicion_utc": ult.isoformat(timespec="seconds") if ult else None,
         "minutos_desde_ultima_medicion": int((ahora - ult).total_seconds() // 60) if ult else None}
    _cache_prediccion[device_id] = (ahora, r)
    return r


# ---------- Predicciones guardadas (modelo v4, las escribe el alerter; migracion 008) ----------
# /forecast se mantiene (calculo en vivo). Estas rutas leen la tabla predictions y prediction_metrics.
FALTA_008 = "Falta aplicar db/migrations/008_predictions.sql"
_MODELO_V4 = "prediccion_pm25_v4.json"
PREDICCION_MODELO_PATH = os.getenv("PREDICCION_MODELO_PATH") or str(next(
    (c for c in (Path(__file__).resolve().parent / "modelos" / _MODELO_V4, _RAIZ / "modelos" / _MODELO_V4)
     if c.is_file()), Path(__file__).resolve().parent / "modelos" / _MODELO_V4))


@app.get("/devices/{device_id}/predictions/latest")
async def predictions_latest(device_id: str, incluir_ocultos: bool = False):
    await _con_timeout(require_device(device_id))
    try:
        filas = await _con_timeout(query(SQL_ULTIMAS, {"device_id": device_id}))
        metricas = await _con_timeout(query(SQL_METRICAS, {"device_id": device_id, "dias": 30, "horizon": None}))
    except psycopg.errors.UndefinedTable:
        raise HTTPException(status_code=503, detail=FALTA_008)
    return construir_latest(device_id, filas, cargar_modelo(PREDICCION_MODELO_PATH), metricas,
                            datetime.now(timezone.utc), incluir_ocultos)


@app.get("/devices/{device_id}/predictions/metrics")
async def predictions_metrics(device_id: str, days: int = Query(30, ge=1, le=365), horizon: Optional[int] = None):
    if horizon is not None and horizon not in (3, 4, 5):
        raise HTTPException(status_code=422, detail="horizon debe ser 3, 4 o 5")
    await _con_timeout(require_device(device_id))
    try:
        filas = await _con_timeout(query(SQL_METRICAS, {"device_id": device_id, "dias": days, "horizon": horizon}))
    except psycopg.errors.UndefinedTable:
        raise HTTPException(status_code=503, detail=FALTA_008)
    hoy = datetime.now(timezone(timedelta(hours=-5))).date()
    return construir_metricas(device_id, filas, days, horizon, hoy)


# ---------- Replay HISTORICO (api/replay.py): solo external_measurements, todo al vuelo ----------
# No lee measurements ni predictions y no escribe nada: el panel lo usa con el hardware desconectado.
FALTA_006 = "Falta aplicar db/migrations/006_external_measurements.sql"


def _modelo_replay():
    modelo = cargar_modelo(PREDICCION_MODELO_PATH)
    if modelo is None:
        raise HTTPException(status_code=503, detail="Modelo de prediccion v4 no disponible en la API")
    return modelo


def _estacion_replay(station):
    if station not in replay.ESTACIONES:
        raise HTTPException(status_code=404, detail=f"Estacion '{station}' no disponible para el replay")
    return station


async def _serie_replay(station, desde, hasta):
    sql, params = replay.sql_serie(station)
    try:
        filas = await _con_timeout(query(sql, (*params, desde, hasta)))
    except psycopg.errors.UndefinedTable:
        raise HTTPException(status_code=503, detail=FALTA_006)
    return replay.serie_limpia(filas)


@app.get("/predictions/replay/estaciones")
async def replay_estaciones():
    modelo = _modelo_replay()
    out = []
    for codigo, info in replay.ESTACIONES.items():
        sql, params = replay.sql_rango(codigo)
        try:
            rangos = await _con_timeout(query(sql, tuple(params)))
        except psycopg.errors.UndefinedTable:
            raise HTTPException(status_code=503, detail=FALTA_006)
        if not rangos:
            continue
        primera, ultima = min(r["primera"] for r in rangos), max(r["ultima"] for r in rangos)
        pref = replay.AT_POR_DEFECTO.astimezone(timezone.utc)
        desde = min(pref - timedelta(hours=25), ultima - timedelta(days=4))
        hasta = max(pref + timedelta(hours=6), ultima)
        serie, _ = await _serie_replay(codigo, desde, hasta)
        defecto = replay.ultima_valida(serie, modelo)
        out.append({"station": codigo, "nombre": info["nombre"],
                    "fuentes": [{"source": r["source"], "primera_utc": r["primera"].isoformat(),
                                 "ultima_utc": r["ultima"].isoformat(), "horas": r["horas"]} for r in rangos],
                    "desde_local": primera.astimezone(replay.COLOMBIA).strftime("%Y-%m-%dT%H:%M"),
                    "hasta_local": ultima.astimezone(replay.COLOMBIA).strftime("%Y-%m-%dT%H:%M"),
                    "at_por_defecto": defecto.strftime("%Y-%m-%dT%H:%M") if defecto else None})
    return {"estaciones": out, "aviso": replay.AVISO_REPLAY}


@app.get("/predictions/replay")
async def replay_prediccion(station: str, at: str):
    station = _estacion_replay(station)
    try:
        t = replay.parsear_at(at)
    except replay.ReplayError as e:
        raise HTTPException(status_code=422, detail=str(e))
    modelo = _modelo_replay()
    t_utc = t.astimezone(timezone.utc)
    serie, fuentes = await _serie_replay(station, t_utc - timedelta(hours=25), t_utc + timedelta(hours=6))
    try:
        return replay.calcular_replay(station, serie, fuentes, modelo, t)
    except replay.ReplayError as e:
        raise HTTPException(status_code=422, detail=str(e))


@app.get("/predictions/replay/metrics")
async def replay_metricas(station: str, desde: date, hasta: date):
    station = _estacion_replay(station)
    try:
        ini, fin = replay.rango_periodo(desde, hasta)
    except replay.ReplayError as e:
        raise HTTPException(status_code=422, detail=str(e))
    modelo = _modelo_replay()
    serie, fuentes = await _serie_replay(station, ini.astimezone(timezone.utc) - timedelta(hours=25),
                                         fin.astimezone(timezone.utc) + timedelta(hours=6))
    return replay.calcular_metricas(station, serie, fuentes, modelo, desde, hasta)


# ---------- Portada ("/") y panel ("/panel") ----------
# Compatibilidad: los QR y correos antiguos apuntan a "/?device=..." -> 302 a "/panel?device=..."
# conservando todos los parametros. "/" sin device muestra la portada.
SIN_CACHE = {"Cache-Control": "no-cache"}


@app.get("/", include_in_schema=False)
async def portada(request: Request):
    if "device" in request.query_params:
        return RedirectResponse(f"/panel?{request.url.query}", status_code=302)
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html", headers=SIN_CACHE)


@app.get("/panel", include_in_schema=False)
async def panel():
    return FileResponse(STATIC_DIR / "panel.html", media_type="text/html", headers=SIN_CACHE)


class StaticSinCache(StaticFiles):
    """Archivos del dashboard con Cache-Control: no-cache: el navegador revalida (ETag) en cada
    carga y nunca mezcla un index.html nuevo con un app.js viejo guardado en cache."""

    async def get_response(self, path, scope):
        respuesta = await super().get_response(path, scope)
        respuesta.headers["Cache-Control"] = "no-cache"
        return respuesta


# Dashboard publico en "/". Se monta AL FINAL para que no tape las rutas de la API.
app.mount("/", StaticSinCache(directory=STATIC_DIR, html=True), name="static")
