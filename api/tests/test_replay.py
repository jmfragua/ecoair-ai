"""Tests del replay historico (api/replay.py y rutas /predictions/replay*). Sin base: query simulada."""
import math
import os
import re
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import psycopg
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("POSTGRES_PASSWORD", "solo-para-tests")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import replay as rp  # noqa: E402
from shared import prediccion as pr  # noqa: E402

client = TestClient(main.app)
MODELO = pr.cargar_modelo(main.PREDICCION_MODELO_PATH)
AT = datetime(2026, 9, 30, 12, 0, tzinfo=rp.COLOMBIA)
SIS = "ESTACIÓN TRÁFICO CENTRO - MEDELLÍN"
IDE = "TRÁFICO CENTRO (IDEAM 29509)"


def valor(h):
    """Serie inventada: ciclo diario suave segun la hora UTC h (indice)."""
    return round(18 + 6 * math.sin(2 * math.pi * (h % 24) / 24), 1)


def filas_serie(ini, fin, source=rp.FUENTE_SISAIRE, station=SIS, f=valor, saltar=()):
    out, h = [], rp.hora_idx(ini)
    while h <= rp.hora_idx(fin):
        if h not in saltar:
            out.append({"source": source, "station": station, "measured_at": rp.de_idx(h), "pm25": Decimal(str(f(h)))})
        h += 1
    return out


T = rp.hora_idx(AT)
FILAS = filas_serie(rp.de_idx(T - 40), rp.de_idx(T + 30))


# ---------- Logica pura ----------

def test_parsear_at_hora_de_colombia_y_errores():
    assert rp.parsear_at("2026-09-30T12:00") == AT
    assert rp.parsear_at("2026-09-30T17:00:00+00:00") == AT
    for malo, txt in (("2026-09-30T12:30", "hora en punto"), ("ayer", "ISO valida")):
        with pytest.raises(rp.ReplayError, match=txt):
            rp.parsear_at(malo)


def test_serie_limpia_prioriza_ideam_y_quita_ceros():
    h = T - 1
    filas = [{"source": rp.FUENTE_SISAIRE, "station": SIS, "measured_at": rp.de_idx(h), "pm25": Decimal("30")},
             {"source": rp.FUENTE_IDEAM, "station": IDE, "measured_at": rp.de_idx(h), "pm25": Decimal("20")},
             {"source": rp.FUENTE_SISAIRE, "station": SIS, "measured_at": rp.de_idx(h + 1), "pm25": Decimal("0")}]
    serie, fuentes = rp.serie_limpia(filas)
    assert serie == {h: 20.0} and fuentes == [rp.FUENTE_IDEAM]


def test_replay_4_y_5_h_con_observado_persistencia_y_error():
    serie, fuentes = rp.serie_limpia(FILAS)
    r = rp.calcular_replay("29509", serie, fuentes, MODELO, AT)
    assert [p["horizon_hours"] for p in r["predicciones"]] == [4, 5]          # 3 h oculto
    p4 = r["predicciones"][0]
    assert p4["target_local"] == "2026-09-30T16:00" and p4["observed_pm25"] == valor(T + 4)
    assert p4["persistence_pm25"] == valor(T)
    assert p4["error_modelo"] == round(p4["predicted_pm25"] - valor(T + 4), 2) or \
        abs(p4["error_modelo"] - (p4["predicted_pm25"] - valor(T + 4))) < 0.06
    assert r["aviso"] == rp.AVISO_REPLAY and r["ventana"]["horas_24h"] == 24 and r["fuentes"] == [rp.FUENTE_SISAIRE]


def test_replay_sin_observado_y_ventana_incompleta():
    serie, f = rp.serie_limpia(filas_serie(rp.de_idx(T - 30), rp.de_idx(T + 4)))   # falta T+5
    r = rp.calcular_replay("29509", serie, f, MODELO, AT)
    assert r["predicciones"][1]["observed_pm25"] is None and r["predicciones"][1]["error_modelo"] is None
    serie, f = rp.serie_limpia(filas_serie(rp.de_idx(T - 30), rp.de_idx(T + 6), saltar={T - 2}))
    with pytest.raises(rp.ReplayError, match="faltan horas de las 6 previas.*2026-09-30 10:00"):
        rp.calcular_replay("29509", serie, f, MODELO, AT)


def test_metricas_mae_rmse_y_pocas_horas():
    serie, f = rp.serie_limpia(FILAS)
    m = rp.calcular_metricas("29509", serie, f, MODELO, date(2026, 9, 30), date(2026, 9, 30))
    assert m["emisiones"] == 24 and m["estado"] == "pocas_horas" and "Pocas horas" in m["mensaje"]
    h4 = m["por_horizonte"][0]
    assert h4["horizon_hours"] == 4 and h4["n"] > 0 and h4["mae_modelo"] >= 0 and "rmse_persistencia" in h4
    # comprobacion manual de una emision
    x = rp.ventana(serie, T, MODELO["c"])
    assert abs(pr.predecir_horizonte(MODELO, x, 240) - rp.calcular_replay("29509", serie, f, MODELO, AT)["predicciones"][0]["predicted_pm25"]) < 0.06
    vacia = rp.calcular_metricas("29509", {}, [], MODELO, date(2026, 9, 1), date(2026, 9, 2))
    assert vacia["estado"] == "sin_datos"
    with pytest.raises(rp.ReplayError, match="anterior"):
        rp.rango_periodo(date(2026, 9, 2), date(2026, 9, 1))
    with pytest.raises(rp.ReplayError, match="366"):
        rp.rango_periodo(date(2025, 1, 1), date(2026, 1, 2))


def test_ultima_valida_usa_la_preferida_o_la_ultima_posible():
    serie, _ = rp.serie_limpia(FILAS)
    assert rp.ultima_valida(serie, MODELO) == AT
    corta, _ = rp.serie_limpia(filas_serie(rp.de_idx(T - 60), rp.de_idx(T - 20)))
    u = rp.ultima_valida(corta, MODELO)
    assert u is not None and rp.hora_idx(u) == T - 25            # necesita observado a +5 h
    assert rp.ultima_valida({}, MODELO) is None


# ---------- Rutas, con registro de TODA consulta a la base ----------

class Registro:
    def __init__(self, filas=FILAS, falta_tabla=False):
        self.filas, self.falta_tabla, self.sql = filas, falta_tabla, []

    async def query(self, sql, params=()):
        self.sql.append(sql)
        if self.falta_tabla:
            raise psycopg.errors.UndefinedTable("relation external_measurements does not exist")
        if "min(measured_at)" in sql:
            hs = [f for f in self.filas if (f["source"], f["station"]) in zip(params[0::2], params[1::2])]
            por = {}
            for f in hs:
                d = por.setdefault(f["source"], {"source": f["source"], "primera": f["measured_at"], "ultima": f["measured_at"], "horas": 0})
                d["primera"], d["ultima"] = min(d["primera"], f["measured_at"]), max(d["ultima"], f["measured_at"])
                d["horas"] += 1
            return list(por.values())
        if "SELECT source, station, measured_at, pm25" in sql:
            pares = list(zip(params[0:-2:2], params[1:-2:2]))
            desde, hasta = params[-2], params[-1]
            return [f for f in self.filas if (f["source"], f["station"]) in pares and desde < f["measured_at"] <= hasta]
        raise AssertionError(sql)


@pytest.fixture
def registro(monkeypatch):
    r = Registro()
    monkeypatch.setattr(main, "query", r.query)
    return r


def test_rutas_200(registro):
    e = client.get("/predictions/replay/estaciones").json()
    tc = next(x for x in e["estaciones"] if x["station"] == "29509")
    assert tc["at_por_defecto"] == "2026-09-30T12:00" and e["aviso"] == rp.AVISO_REPLAY
    r = client.get("/predictions/replay?station=29509&at=2026-09-30T12:00")
    assert r.status_code == 200 and [p["horizon_hours"] for p in r.json()["predicciones"]] == [4, 5]
    m = client.get("/predictions/replay/metrics?station=29509&desde=2026-09-30&hasta=2026-09-30")
    assert m.status_code == 200 and m.json()["por_horizonte"][0]["n"] > 0


@pytest.mark.parametrize("url,codigo", [
    ("/predictions/replay?station=99999&at=2026-09-30T12:00", 404),
    ("/predictions/replay/metrics?station=99999&desde=2026-09-01&hasta=2026-09-30", 404),
    ("/predictions/replay?station=29509&at=2026-09-30T12:30", 422),
    ("/predictions/replay?station=29509&at=no-es-fecha", 422),
    ("/predictions/replay?station=29509", 422),
    ("/predictions/replay?station=29509&at=2026-01-01T12:00", 422),            # sin datos en la ventana
    ("/predictions/replay/metrics?station=29509&desde=2026-09-30&hasta=2026-09-01", 422),
    ("/predictions/replay/metrics?station=29509&desde=2024-01-01&hasta=2026-09-30", 422),
    ("/predictions/replay/metrics?station=29509&desde=x&hasta=2026-09-30", 422),
])
def test_rutas_404_422(registro, url, codigo):
    r = client.get(url)
    assert r.status_code == codigo, r.text
    if url.endswith("2026-01-01T12:00"):
        assert "Ventana incompleta" in r.json()["detail"]


def test_rutas_503_sin_tabla(monkeypatch):
    monkeypatch.setattr(main, "query", Registro(falta_tabla=True).query)
    for url in ("/predictions/replay?station=29509&at=2026-09-30T12:00", "/predictions/replay/estaciones",
                "/predictions/replay/metrics?station=29509&desde=2026-09-01&hasta=2026-09-30"):
        r = client.get(url)
        assert r.status_code == 503 and "006" in r.json()["detail"]


def test_replay_no_lee_ni_escribe_measurements_ni_predictions(registro):
    for url in ("/predictions/replay/estaciones", "/predictions/replay?station=29509&at=2026-09-30T12:00",
                "/predictions/replay/metrics?station=29509&desde=2026-09-01&hasta=2026-09-30",
                "/predictions/replay?station=29509&at=2026-01-01T12:00",
                "/predictions/replay?station=29509&at=2026-09-29T07:00&buscar=siguiente",
                "/predictions/replay/serie?station=29509&at=2026-09-30T12:00&horas=168&horizonte=5",
                "/predictions/replay/serie?station=29509&at=2026-09-30T12:00&horas=24&horizonte=4"):
        client.get(url)
    assert registro.sql, "no se registro ninguna consulta"
    for sql in registro.sql:
        s = sql.upper()
        assert s.lstrip().startswith("SELECT"), sql
        assert not re.search(r"\b(INSERT|UPDATE|DELETE|TRUNCATE|ALTER|DROP|CREATE)\b", s), sql
        assert "EXTERNAL_MEASUREMENTS" in s
        assert not re.search(r"(?<!EXTERNAL_)\bMEASUREMENTS\b", s), sql
        assert not re.search(r"\bPREDICTIONS\b|\bPREDICTION_METRICS\b", s), sql
        assert not re.search(r"\b(ALERTS|SUBSCRIBERS|NOTIFICATIONS_SENT)\b", s), sql



# ---------- Modo replay del panel: ICA solo con PM2.5 y salto a la siguiente hora valida ----------

def test_actual_ica_solo_pm25_con_la_misma_funcion_que_el_panel_en_vivo():
    from ica import calcular_ica
    serie, f = rp.serie_limpia(FILAS)
    a = rp.calcular_replay("29509", serie, f, MODELO, AT)["actual"]
    esperado = calcular_ica("pm25", valor(T))
    assert (a["ica"]["ica"], a["ica"]["category_code"]) == (esperado["ica"], esperado["category_code"])
    assert a["ica"]["solo_pm25"] is True and "PM2.5 solamente" in a["ica"]["base"]
    assert a["pm25_1h"] == valor(T) and a["recomendacion"]["efectos"]
    assert a["no_disponibles"] == {k: "no disponible en el histórico" for k in ("pm10", "temperature", "humidity")}
    assert not any(k in a for k in ("pm10_1h", "temperature", "humidity"))      # no se inventan valores


def test_siguiente_hora_valida_salta_y_lo_avisa():
    serie, f = rp.serie_limpia(filas_serie(rp.de_idx(T - 30), rp.de_idx(T + 30), saltar={T - 1}))
    r = rp.calcular_replay_siguiente("29509", serie, f, MODELO, AT)
    # con T-1 ausente, las emisiones T .. T+4 no tienen las 6 horas previas completas
    assert r["at_local"] == "2026-09-30T17:00" and r["salto"]["horas"] == 5
    assert r["salto"]["pedida_local"] == "2026-09-30T12:00" and "faltan horas" in r["salto"]["motivo"]
    assert "salto" not in rp.calcular_replay_siguiente("29509", serie, f, MODELO, AT + timedelta(hours=6))
    with pytest.raises(rp.ReplayError, match="No hay horas con ventana completa"):
        rp.calcular_replay_siguiente("29509", {}, [], MODELO, AT, max_horas=3)


def test_ruta_buscar_siguiente(registro):
    # la serie empieza en T-40 (2026-09-28 20:00): a las 07:00 del 29 (T-29) hay 12 de 24 horas;
    # la primera hora con >= 18 es las 13:00 (T-23), 6 h despues
    r = client.get("/predictions/replay?station=29509&at=2026-09-29T07:00&buscar=siguiente")
    assert r.status_code == 200 and r.json()["salto"]["horas"] == 6 and r.json()["at_local"] == "2026-09-29T13:00"
    assert r.json()["actual"]["ica"]["solo_pm25"]
    assert client.get("/predictions/replay?station=29509&at=2026-09-30T12:00&buscar=otra").status_code == 422


def test_panel_modo_replay_banner_y_sin_consultas_en_vivo():
    html = client.get("/panel").text
    banner = html[html.index('id="replay-banner"'):]
    banner = banner[:banner.index("</div>")]
    assert "REPLAY HISTÓRICO</strong> · datos reales del IDEAM de una fecha pasada." in banner
    assert "No es en vivo" not in banner and "certificado" not in banner
    assert 'class="sim-banner replay-banner"' in html
    for id_ in ("replay-play", "replay-velocidad", "replay-salto"):
        assert f'id="{id_}"' in html
    js = client.get("/js/app.js").text
    inicio = js[js.index("function start()"):]
    # en modo replay se sale de start() antes de refresh(), refreshHistory(), consentimiento y suscripcion
    corte = inicio.index("if (MODO_REPLAY) { iniciarModoReplay(); return; }")
    for vivo in ("refresh()", "refreshHistory()", "loadConsent()", "initSubscriptionForm()"):
        assert inicio.index(vivo) > corte, vivo
    modo = js[js.index("function iniciarModoReplay()"):js.index("// ---------- Suscripcion por correo")]
    assert "/devices/" not in modo and "/subscriptions" not in modo and 'subs-form").hidden = true' in modo
    replay_js = js[js.index("// ---------- Replay historico ----------"):js.index("// ---------- Suscripcion por correo")]
    assert "/devices/" not in replay_js and "/predictions/latest" not in replay_js


def test_formulario_de_suscripcion_se_oculta_de_verdad():
    css = client.get("/css/app.css").text
    assert ".subs[hidden] { display: none; }" in css     # .subs usa display: grid, que anula [hidden]



# ---------- Grafica real frente a predicho (/predictions/replay/serie) ----------

def test_serie_indexada_por_hora_objetivo_con_huecos_null_y_metricas():
    # sin T-3 (hueco en la serie): las emisiones T-3 .. T+2 no tienen las 6 previas completas
    serie, f = rp.serie_limpia(filas_serie(rp.de_idx(T - 60), rp.de_idx(T + 2), saltar={T - 3}))
    r = rp.calcular_serie("29509", serie, f, MODELO, AT, 24, 4)
    pts = r["puntos"]
    assert len(pts) == 24 and pts[-1]["emision_local"] == "2026-09-30T12:00" and pts[-1]["target_local"] == "2026-09-30T16:00"
    por_emision = {p["emision_local"]: p for p in pts}
    hueco = por_emision["2026-09-30T09:00"]                       # emision T-3: su h1 falta
    assert hueco["predicho"] is None and hueco["persistencia"] is None
    assert por_emision["2026-09-30T11:00"]["predicho"] is None   # T-1: falta T-3 entre sus 6 previas
    assert por_emision["2026-09-30T12:00"]["real"] is None       # objetivo T+4 > fin de la serie (T+2)
    completos = [p for p in pts if p["predicho"] is not None and p["real"] is not None and p["persistencia"] is not None]
    assert r["metricas"]["n"] == len(completos) > 0
    mae = sum(abs(p["predicho"] - p["real"]) for p in completos) / len(completos)
    assert abs(r["metricas"]["mae_modelo"] - mae) < 0.06            # redondeo de los valores a 0,1
    assert r["aviso"] == rp.AVISO_REPLAY


def test_serie_parametros_invalidos():
    serie, f = rp.serie_limpia(FILAS)
    with pytest.raises(rp.ReplayError, match="horas"):
        rp.calcular_serie("29509", serie, f, MODELO, AT, 48, 4)
    with pytest.raises(rp.ReplayError, match="horizonte"):
        rp.calcular_serie("29509", serie, f, MODELO, AT, 24, 3)       # 3 h no es visible


def test_ruta_serie(registro):
    r = client.get("/predictions/replay/serie?station=29509&at=2026-09-30T12:00&horas=24&horizonte=5")
    assert r.status_code == 200 and len(r.json()["puntos"]) == 24 and r.json()["horizonte"] == 5
    consultas_serie = [q for q in registro.sql if "SELECT source, station, measured_at, pm25" in q]
    assert len(consultas_serie) == 1                                   # una sola consulta amplia
    for q, codigo in (("horas=48", 422), ("horizonte=3", 422), ("at=2026-09-30T12:30", 422)):
        url = "/predictions/replay/serie?station=29509&at=2026-09-30T12:00&horas=24&horizonte=4"
        clave = q.split("=")[0]
        url = re.sub(rf"{clave}=[^&]+", q, url)
        assert client.get(url).status_code == codigo, url
    assert client.get("/predictions/replay/serie?station=99999&at=2026-09-30T12:00").status_code == 404


def test_ruta_serie_503(monkeypatch):
    monkeypatch.setattr(main, "query", Registro(falta_tabla=True).query)
    assert client.get("/predictions/replay/serie?station=29509&at=2026-09-30T12:00").status_code == 503


def test_panel_grafica_replay_markup():
    html = client.get("/panel").text
    tarjeta = html[html.index('class="card card--replay"'):html.index("<!-- Suscripcion por correo -->")]
    for id_ in ("replay-rango", "replay-horizonte", "replay-chart", "replay-svg", "replay-tip", "replay-gmet"):
        assert f'id="{id_}"' in tarjeta
    assert "Real (línea continua)" in tarjeta and "Predicho (rayas)" in tarjeta and "Persistencia (puntos)" in tarjeta
    assert "No es un pronóstico en tiempo real" in tarjeta
    css = client.get("/css/app.css").text
    assert "stroke-dasharray: 6 4" in css and "stroke-dasharray: 1.5 4" in css     # no solo color
