"""Tests de alerter/prediccion_horaria.py y alerter/metricas_prediccion.py (sin base: conexion falsa)."""
import copy
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import metricas_prediccion as mp  # noqa: E402
import prediccion_horaria as ph  # noqa: E402
from shared import prediccion as pr  # noqa: E402

RAIZ = Path(__file__).resolve().parents[2]
RUTA_V4 = RAIZ / "modelos" / "prediccion_pm25_v4.json"
V4 = pr.cargar_modelo(RUTA_V4)
HORA = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)      # 10:00 en Colombia


def filas_minuto(fin, valor=lambda i: 12.0, minutos=24 * 60, faltan=()):
    return [{"t": fin - timedelta(minutes=i), "pm25": valor(i)} for i in range(minutos) if i not in faltan]


class Resultado:
    def __init__(self, uno=None, todos=None):
        self.uno, self.todos = uno, todos or []

    def fetchone(self):
        return self.uno

    def fetchall(self):
        return self.todos


class ConexionFalsa:
    """Responde a las consultas por su texto e imita los UNIQUE de predictions y prediction_metrics."""

    def __init__(self, serie=None, tabla=True, simulado=False, filas_metricas=None):
        self.serie, self.tabla, self.simulado = serie or [], tabla, simulado
        self.filas_metricas = filas_metricas or []
        self.predicciones, self.metricas, self.consultas = {}, {}, []

    def execute(self, sql, params=None):
        self.consultas.append(sql)
        if "to_regclass" in sql:
            return Resultado({"ok": self.tabla})
        if "bool_or(is_simulated)" in sql:
            return Resultado({"sim": self.simulado})
        if "date_trunc('minute', received_at) AS t" in sql:
            assert params["ahora"].minute == 0       # solo datos hasta la hora en punto
            return Resultado(todos=[f for f in self.serie if f["t"] <= params["ahora"]])
        if "FROM predictions p LEFT JOIN LATERAL" in sql:
            return Resultado(todos=self.filas_metricas)
        raise AssertionError(sql)

    def cursor(self):
        con = self

        class Cur:
            rowcount = 0

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def executemany(self, sql, filas):
                n = 0
                for f in filas:
                    if sql.startswith("INSERT INTO predictions"):
                        k = (f["device_id"], f["created_at"], f["horizon_hours"])
                        if k not in con.predicciones:
                            con.predicciones[k], n = f, n + 1
                    else:
                        con.metricas[(f["fecha"], f["device_id"], f["horizon_hours"], f["model_version"])] = f
                        n += 1
                self.rowcount = n
        return Cur()


# ---------- Emision horaria ----------

def test_hora_de_emision_desde_el_minuto_5():
    assert ph.hora_de_emision(HORA.replace(minute=4, second=59), 5) is None
    assert ph.hora_de_emision(HORA.replace(minute=5, second=3), 5) == HORA
    assert ph.hora_de_emision(HORA.replace(minute=58), 5) == HORA


def test_calcular_los_3_horizontes_con_linea_base_y_entradas():
    serie = pr.serie_por_minuto([(f["t"], f["pm25"]) for f in filas_minuto(HORA)])
    filas, recortes = ph.calcular(V4, "v4@x", "ecoair-node-01", serie, HORA)
    assert [f["horizon_hours"] for f in filas] == [3, 4, 5] and recortes == []
    assert [f["target_time"] for f in filas] == [HORA + timedelta(hours=h) for h in (3, 4, 5)]
    assert all(f["baseline_pm25"] == 12.0 and f["predicted_pm25"] >= 0 and f["model_version"] == "v4@x" for f in filas)
    assert filas[0]["inputs"]["pm25_h1"] == 12.0 and filas[0]["inputs"]["horas_24h"] == 24
    assert filas[0]["last_reading_at"] == HORA


def test_calcular_omite_si_faltan_datos():
    # falta casi toda la segunda hora (solo 30 min con datos)
    serie = pr.serie_por_minuto([(f["t"], f["pm25"]) for f in filas_minuto(HORA, faltan=range(60, 90))])
    filas, motivo = ph.calcular(V4, "v", "d", serie, HORA)
    assert filas is None and "faltan datos" in motivo


def test_recorte_a_cero_se_cuenta():
    m = copy.deepcopy(V4)
    for h in m["horizontes"].values():
        h["intercepto"] = -10.0                  # (h1 + c) * exp(f) - c < 0
    serie = pr.serie_por_minuto([(f["t"], f["pm25"]) for f in filas_minuto(HORA, valor=lambda i: 1.0)])
    filas, recortes = ph.calcular(m, "v", "d", serie, HORA)
    assert [f["predicted_pm25"] for f in filas] == [0.0, 0.0, 0.0]
    assert [h for h, _ in recortes] == [3, 4, 5] and all(c < 0 for _, c in recortes)


def test_emisor_idempotente_una_vez_por_hora_y_tras_reinicio():
    con = ConexionFalsa(serie=filas_minuto(HORA))
    e = ph.EmisorHorario(RUTA_V4, ["ecoair-node-01"], 5)
    assert e.revisar(con, HORA.replace(minute=3)) == 0 and con.consultas == []
    assert e.revisar(con, HORA.replace(minute=5)) == 3
    n = len(con.consultas)
    assert e.revisar(con, HORA.replace(minute=6)) == 0 and len(con.consultas) == n     # ya emitida: ni consulta
    reiniciado = ph.EmisorHorario(RUTA_V4, ["ecoair-node-01"], 5)
    assert reiniciado.revisar(con, HORA.replace(minute=30)) == 0                       # ON CONFLICT DO NOTHING
    assert len(con.predicciones) == 3
    assert e.version.startswith("prediccion_pm25_v4@") and len(e.version.split("@")[1]) == 8


def test_emisor_sin_tabla_simulado_o_sin_datos(caplog):
    caplog.set_level("INFO", logger="alerter.prediccion")
    e = ph.EmisorHorario(RUTA_V4, ["ecoair-node-01"], 5)
    sin = ConexionFalsa(tabla=False)
    assert e.revisar(sin, HORA.replace(minute=5)) == 0 and e.revisar(sin, HORA.replace(minute=7)) == 0
    assert caplog.text.count("008_predictions.sql") == 1
    sim = ConexionFalsa(serie=filas_minuto(HORA), simulado=True)
    assert ph.EmisorHorario(RUTA_V4, ["ecoair-sim-01"], 5).revisar(sim, HORA.replace(minute=5)) == 0
    assert sim.predicciones == {}
    vacia = ConexionFalsa(serie=filas_minuto(HORA, minutos=120))
    e2 = ph.EmisorHorario(RUTA_V4, ["ecoair-node-01"], 5)
    assert e2.revisar(vacia, HORA.replace(minute=5)) == 0 and "omitida, faltan datos" in caplog.text
    n = len(vacia.consultas)
    assert e2.revisar(vacia, HORA.replace(minute=40)) == 0 and len(vacia.consultas) == n   # no reintenta en la hora


# ---------- Metricas diarias ----------

def fila_m(h, pred, base, obs, minutos=60, version="v4@x"):
    return {"horizon_hours": h, "model_version": version, "predicted_pm25": pred, "baseline_pm25": base,
            "media": obs, "minutos": minutos}


def test_metricas_de_mae_rmse_y_sin_observacion():
    filas = [fila_m(3, 10, 14, 12), fila_m(3, 15, 9, 12), fila_m(3, 20, 20, None, 0), fila_m(3, 5, 5, 6, 39),
             fila_m(4, 8, 8, None, 0)]
    out = mp.metricas_de(filas, date(2026, 10, 20), "ecoair-node-01")
    assert len(out) == 1                                # 4 h no tiene ninguna observacion: no se escribe
    m = out[0]
    assert (m["horizon_hours"], m["n"], m["n_sin_observacion"]) == (3, 2, 2)
    assert (m["mae_model"], m["mae_baseline"]) == (2.5, 2.5)
    assert m["rmse_model"] == round(((4 + 9) / 2) ** 0.5, 3) and m["rmse_baseline"] == round(((4 + 9) / 2) ** 0.5, 3)


def test_dias_y_limites_en_hora_de_colombia():
    assert mp.dias_a_calcular(datetime(2026, 10, 21, 5, 29, tzinfo=timezone.utc)) == []          # 00:29 Colombia
    assert mp.dias_a_calcular(datetime(2026, 10, 21, 5, 30, tzinfo=timezone.utc)) == [date(2026, 10, 19), date(2026, 10, 20)]
    desde, hasta = mp.limites_del_dia(date(2026, 10, 20))
    assert desde < datetime(2026, 10, 20, 5, 0, tzinfo=timezone.utc) <= hasta          # 00:00 Colombia entra
    assert hasta < datetime(2026, 10, 21, 5, 0, tzinfo=timezone.utc)                   # 00:00 del dia siguiente no


def test_calculador_una_vez_por_dia_e_idempotente():
    con = ConexionFalsa(filas_metricas=[fila_m(5, 10, 12, 11)])
    c = mp.CalculadorMetricas(["ecoair-node-01"])
    ahora = datetime(2026, 10, 21, 6, 0, tzinfo=timezone.utc)
    assert c.revisar(con, ahora) == 2                   # ayer y anteayer
    assert c.revisar(con, ahora + timedelta(hours=1)) == 0
    assert mp.CalculadorMetricas(["ecoair-node-01"]).revisar(con, ahora) == 2
    assert len(con.metricas) == 2                       # ON CONFLICT DO UPDATE: no duplica
