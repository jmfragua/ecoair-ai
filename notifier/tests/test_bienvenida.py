import os
import smtplib
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import notifier  # noqa: E402
from calidad_aire import SQL_ULTIMA, SQL_VENTANAS, construir_respuesta  # noqa: E402
from correos import (  # noqa: E402
    ADVERTENCIA,
    AVISO_INDICATIVO,
    AVISO_SIMULACION,
    PREDICCION_NO_DISPONIBLE,
    correo_bienvenida,
    enlaces,
)

BASE = "https://ecoair.example"
EMAIL = "ana.perez@gmail.com"
AHORA = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
LINKS = enlaces(BASE, "ecoair-node-01", token_baja="TOKENBAJA")


def _resp(pm25_1h=25.0, pm10_1h=40.0, edad=timedelta(seconds=10)):
    v1 = {"pm25_media": pm25_1h, "pm10_media": pm10_1h, "muestras": 720, "minutos_con_datos": 60}
    v24 = {"pm25_media": 20.0, "pm10_media": 30.0, "muestras": 17280, "minutos_con_datos": 1440}
    ultima = {"received_at": AHORA - edad, "temperature": 22.0, "humidity": 70.0}
    return construir_respuesta("ecoair-node-01", v1, v24, ultima, AHORA)


def _texto(msg, tipo="plain"):
    return msg.get_body((tipo,)).get_content()


# ---------- Contenido ----------

def test_bienvenida_con_ica_recomendacion_y_fuente():
    msg = correo_bienvenida("EcoAir <a@b.co>", EMAIL, "ecoair-node-01", _resp(), LINKS)
    t = _texto(msg)
    assert msg["Subject"] == "[EcoAir AI] Suscripción confirmada · estación ecoair-node-01"
    assert "ICA actual: 76 (Aceptable), definido por PM2.5" in t
    assert "media de la última hora" in t
    for seccion in ("QUÉ SIGNIFICA", "PARA TODOS", "GRUPOS SENSIBLES", "QUÉ HACER", "SI SUBE"):
        assert seccion in t, seccion
    assert "Posibles síntomas respiratorios en grupos poblacionales sensibles." in t
    assert "Res. 2254/2017, Art. 19, Tabla 5, pág. 8 (PDF)" in t and "no IA" in t
    assert f"Predicción experimental:\n{PREDICCION_NO_DISPONIBLE}" in t
    assert AVISO_INDICATIVO in t and ADVERTENCIA in t
    assert f"{BASE}/panel?device=ecoair-node-01" in t and "unsubscribe?token=TOKENBAJA" in t
    assert "SIMULACIÓN" not in t and "SIMULACIÓN" not in msg["Subject"]
    assert msg["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


def test_bienvenida_sin_datos_recientes():
    t = _texto(correo_bienvenida("a@b.co", EMAIL, "ecoair-node-01", _resp(edad=timedelta(minutes=10)), LINKS))
    assert "no hay datos recientes" in t and "ICA actual" not in t and AVISO_INDICATIVO in t


def test_bienvenida_de_estacion_simulada():
    msg = correo_bienvenida("a@b.co", EMAIL, "ecoair-sim-01", _resp(), LINKS, simulado=True)
    assert msg["Subject"].startswith("[SIMULACIÓN] ")
    assert _texto(msg).startswith(AVISO_SIMULACION)
    assert "SIMULACIÓN" in _texto(msg, "html")


def test_bienvenida_con_prediccion_lista_para_el_paso_3():
    pred = {"texto": "PM2.5 en 1 h: 14 µg/m³.", "aviso": "Modelo entrenado con: simulados. Experimental."}
    t = _texto(correo_bienvenida("a@b.co", EMAIL, "ecoair-node-01", _resp(), LINKS, prediccion=pred))
    assert "PM2.5 en 1 h: 14 µg/m³." in t and "Experimental." in t and PREDICCION_NO_DISPONIBLE not in t


def test_html_escapa_el_dispositivo():
    cuerpo = _texto(correo_bienvenida("a@b.co", EMAIL, "<x>", _resp(), LINKS), "html")
    assert "<x>" not in cuerpo and "&lt;x&gt;" in cuerpo


# ---------- Envio unico (base de datos falsa con estado y transacciones) ----------

class _R:
    def __init__(self, filas): self.filas = filas
    def fetchone(self): return self.filas[0] if self.filas else None
    def fetchall(self): return self.filas


class BaseFalsa:
    def __init__(self, con_007=True, simulado=False):
        self.con_007, self.simulado = con_007, simulado
        ahora = datetime.now(timezone.utc)
        self.subs = {1: {"id": 1, "email": EMAIL, "device_id": "ecoair-node-01", "created_at": ahora,
                         "confirmed_at": ahora, "unsubscribed_at": None, "welcome_sent_at": None,
                         "welcome_attempts": 0, "welcome_last_attempt_at": None}}
        self._tx = None

    @contextmanager
    def transaction(self):
        self._tx = []
        try:
            yield
            for cambio in self._tx:
                cambio()
        finally:
            self._tx = None

    def _aplicar(self, cambio):
        if self._tx is not None:
            self._tx.append(cambio)
        else:
            cambio()

    def execute(self, sql, params=()):
        if "information_schema.columns" in sql:
            return _R([{"ok": self.con_007}])
        if sql.startswith("SELECT id, welcome_attempts"):
            return _R([{k: s[k] for k in ("id", "welcome_attempts", "welcome_last_attempt_at")}
                       for s in self.subs.values() if s["confirmed_at"] and not s["welcome_sent_at"]])
        if sql.startswith("SELECT id, email, device_id, created_at"):
            s = self.subs[params[0]]
            return _R([] if s["welcome_sent_at"] else [s])
        if sql is SQL_VENTANAS:
            return _R([{"pm25_1h": 25.0, "pm10_1h": 40.0, "muestras_1h": 720, "minutos_1h": 60,
                        "pm25_24h": 20.0, "pm10_24h": 30.0, "muestras_24h": 17280, "minutos_24h": 1440}])
        if sql is SQL_ULTIMA:
            return _R([{"received_at": datetime.now(timezone.utc) - timedelta(seconds=5),
                        "temperature": 22.0, "humidity": 70.0}])
        if sql is notifier.SQL_SERIE_PM25:
            ahora = datetime.now(timezone.utc).replace(second=0, microsecond=0)
            return _R([{"t": ahora - timedelta(minutes=i), "pm25": 20.0} for i in range(200)])
        if sql is notifier.SQL_ULTIMO_SIMULADO:
            return _R([{"is_simulated": self.simulado}])
        if sql.startswith("UPDATE subscribers SET welcome_sent_at"):
            s = self.subs[params[0]]
            self._aplicar(lambda: s.update(welcome_sent_at=datetime.now(timezone.utc)))
            return _R([])
        if "welcome_attempts = welcome_attempts + 1" in sql:
            s = self.subs[params[0]]
            s["welcome_attempts"] += 1
            s["welcome_last_attempt_at"] = datetime.now(timezone.utc)
            return _R([{"welcome_attempts": s["welcome_attempts"]}])
        raise AssertionError(f"SQL inesperado: {sql[:60]}")


class MailerFalso:
    def __init__(self, falla=False):
        self.enviados, self.falla = [], falla

    def enviar(self, msg):
        if self.falla:
            raise smtplib.SMTPServerDisconnected("sin conexion")
        self.enviados.append(msg)
        return "dry_run"


@pytest.fixture(autouse=True)
def cfg(monkeypatch):
    monkeypatch.setattr(notifier.config, "SUBSCRIPTION_SECRET", "secreto")
    monkeypatch.setattr(notifier.config, "BASE_URL", BASE)
    monkeypatch.setattr(notifier.config, "MAX_INTENTOS", 5)
    monkeypatch.setattr(notifier.config, "BIENVENIDA_VENTANA_H", 24)
    monkeypatch.setattr(notifier, "_aviso_007", False)


def test_se_envia_una_sola_vez_aunque_se_procese_varias_veces():
    base, mailer = BaseFalsa(), MailerFalso()
    assert notifier.procesar_bienvenidas(base, mailer) == 1
    assert notifier.procesar_bienvenidas(base, mailer) == 0   # p. ej. el enlace se abrio otra vez
    assert notifier.procesar_bienvenidas(base, mailer) == 0
    assert len(mailer.enviados) == 1 and base.subs[1]["welcome_sent_at"] is not None
    t = _texto(mailer.enviados[0])
    assert "ICA actual: 76 (Aceptable)" in t
    # modelo versionado: 1 h no supero la linea base; 3 h si. Siempre con sus fuentes y el aviso.
    assert "a 1 h: no disponible (el modelo no superó la línea base)" in t and "PM2.5 estimado en 3 h:" in t
    assert "Modelo entrenado con datos simulados. Experimental." in t and "datos simulados" in t


def test_si_el_envio_falla_no_se_marca_y_se_reintenta():
    base = BaseFalsa()
    assert notifier.procesar_bienvenidas(base, MailerFalso(falla=True)) == 0
    assert base.subs[1]["welcome_sent_at"] is None and base.subs[1]["welcome_attempts"] == 1
    # tras la espera de 1 min, se reintenta y sale una sola vez
    base.subs[1]["welcome_last_attempt_at"] -= timedelta(minutes=2)
    mailer = MailerFalso()
    assert notifier.procesar_bienvenidas(base, mailer) == 1 and len(mailer.enviados) == 1


def test_estacion_simulada_va_marcada_en_la_bienvenida():
    mailer = MailerFalso()
    notifier.procesar_bienvenidas(BaseFalsa(simulado=True), mailer)
    assert mailer.enviados[0]["Subject"].startswith("[SIMULACIÓN]")


def test_sin_migracion_007_no_envia_ni_falla(caplog):
    mailer = MailerFalso()
    assert notifier.procesar_bienvenidas(BaseFalsa(con_007=False), mailer) == 0
    assert mailer.enviados == [] and "007_welcome_email.sql" in caplog.text


def test_dry_run_respetado():
    """Con DRY_RUN el Mailer real no envia (escribe en el log) y la bienvenida queda marcada."""
    base = BaseFalsa()
    m = notifier.Mailer(True, "h", 587, "u", "p", proveedor="brevo", brevo_api_key="k")
    assert notifier.procesar_bienvenidas(base, m) == 1 and base.subs[1]["welcome_sent_at"] is not None


# ---------- Prediccion en el correo (paso 3) ----------

def test_prediccion_que_no_supera_la_linea_base_da_la_tendencia():
    r = {"estado": "no_supera_linea_base", "texto": "Predicción no disponible: el modelo no superó la línea base.",
         "tendencia": "Tendencia de las últimas 3 horas: el PM2.5 subió (de 10.0 a 30.0 µg/m³). Es lo observado, no una predicción.",
         "fuentes": "Modelo entrenado con datos simulados. Experimental.", "aviso": "Atención: ... simulados ..."}
    p = notifier.prediccion_para_correo(r)
    assert p["texto"].startswith("Predicción no disponible: el modelo no superó la línea base.")
    assert "Tendencia de las últimas 3 horas" in p["texto"] and "no una predicción" in p["texto"]
    assert "Modelo entrenado con datos simulados" in p["aviso"] and "simulados" in p["aviso"]
    t = _texto(correo_bienvenida("a@b.co", EMAIL, "ecoair-node-01", _resp(), LINKS, prediccion=p))
    assert "Predicción no disponible" in t and "Tendencia de las últimas 3 horas" in t


def test_sin_modelo_el_correo_dice_no_disponible():
    assert notifier.prediccion_para_correo({"estado": "sin_modelo"}) is None



# ---------- Aviso de ultima medicion antigua ----------

def test_bienvenida_avisa_si_la_ultima_medicion_tiene_mas_de_10_min():
    t = _texto(correo_bienvenida("a@b.co", EMAIL, "ecoair-node-01", _resp(edad=timedelta(minutes=47, seconds=30)), LINKS))
    assert "Última medición hace 47 min (el nodo puede estar apagado)" in t


def test_bienvenida_no_avisa_con_10_min_o_menos_ni_sin_mediciones():
    for edad in (timedelta(seconds=10), timedelta(minutes=10)):
        t = _texto(correo_bienvenida("a@b.co", EMAIL, "ecoair-node-01", _resp(edad=edad), LINKS))
        assert "Última medición hace" not in t
    sin_mediciones = construir_respuesta("x", {"muestras": 0}, {"muestras": 0}, None, AHORA)
    t = _texto(correo_bienvenida("a@b.co", EMAIL, "x", sin_mediciones, LINKS))
    assert "Última medición hace" not in t   # no se inventa una antiguedad



# ---------- Recomendaciones completas (texto fijo de la norma) ----------

def test_bienvenida_lleva_las_secciones_de_recomendaciones_en_html_legible():
    msg = correo_bienvenida("a@b.co", EMAIL, "ecoair-node-01", _resp(pm25_1h=45.0), LINKS)   # ICA 121
    t, h = _texto(msg), _texto(msg, "html")
    for seccion in ("QUÉ SIGNIFICA", "PARA TODOS", "GRUPOS SENSIBLES", "QUÉ HACER", "CUÁNDO CONSULTAR", "SI SUBE"):
        assert seccion in t, seccion
    assert "- Vigila síntomas como tos o falta de aire." in t                 # vinetas en texto plano
    assert "<ul" in h and h.count("<li") >= 3 and "<b>Qué hacer</b>" in h     # lista y titulos en negrita
    assert "Texto fijo de la norma, no IA" in t and "Texto fijo de la norma, no IA" in h
    assert "Tabla 4, grupos sensibles, pág. 7 impresa (PDF 12)" in t          # fuente al final
    assert AVISO_INDICATIVO in t and ADVERTENCIA in t


def test_correo_de_alerta_ica_lleva_las_recomendaciones_de_su_categoria():
    from correos import correo_alerta
    alerta = {"device_id": "ecoair-node-01", "kind": "threshold", "rule_code": "ica_1h", "severity": "critica",
              "message": "ICA 160 (Dañina a la salud)...", "started_at": AHORA, "ended_at": None,
              "details": {"ica": {"category_code": "danina"}}}
    t = _texto(correo_alerta("a@b.co", EMAIL, alerta, "open", LINKS))
    assert "Todos los individuos pueden comenzar a experimentar efectos" in t
    assert "QUÉ HACER" in t and "Grupos sensibles: reprograma o pasa las actividades a un lugar cerrado." in t
    assert "CUÁNDO CONSULTAR" in t and "Texto fijo de la norma, no IA" in t


def test_alertas_sin_categoria_ica_no_llevan_recomendaciones_de_salud():
    from correos import correo_alerta
    alerta = {"device_id": "ecoair-node-01", "kind": "device_down", "rule_code": "dispositivo_caido",
              "severity": "critica", "message": "Sin datos durante 6 min.", "started_at": AHORA, "ended_at": None}
    t = _texto(correo_alerta("a@b.co", EMAIL, alerta, "open", LINKS))
    assert "QUÉ HACER" not in t and "Texto fijo de la norma" not in t
