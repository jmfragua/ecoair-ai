import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import entrenar_prediccion as ep  # noqa: E402
from shared.prediccion import minuto, serie_por_minuto  # noqa: E402

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


def test_ridge_recupera_coeficientes_conocidos():
    rng = np.random.default_rng(0)
    Z = rng.normal(size=(500, 3))
    y = 1.5 + Z @ np.array([2.0, -1.0, 0.5])
    b, c = ep.ajustar_ridge(Z, y, alpha=1e-6)
    assert abs(b - 1.5) < 1e-6 and np.allclose(c, [2.0, -1.0, 0.5], atol=1e-6)


def test_muestras_no_miran_mas_alla_del_corte():
    """Con corte en 'hasta' se obtiene lo mismo que si los datos posteriores no existieran."""
    rng = np.random.default_rng(1)
    s = serie_por_minuto([(T0 + timedelta(minutes=i), float(rng.uniform(5, 50))) for i in range(2000)])
    hasta = minuto(T0) + 1000
    X1, Y1, P1 = ep.muestras(s, hasta=hasta)
    X2, Y2, P2 = ep.muestras({m: v for m, v in s.items() if m <= hasta})
    assert len(P1) > 0 and np.array_equal(X1, X2) and np.array_equal(P1, P2)
    assert all(np.array_equal(Y1[h], Y2[h]) for h in Y1)


def test_partir_por_tiempo_deja_hueco():
    s = serie_por_minuto([(T0 + timedelta(minutes=i), 1.0) for i in range(1000)])
    fin_tr, ini_ev = ep.partir_por_tiempo(s)
    assert ini_ev - fin_tr == ep.HUECO_MIN


def test_si_no_mejora_a_la_persistencia_se_marca():
    """Serie constante: el objetivo es igual a la persistencia; el modelo no puede superarla."""
    s = serie_por_minuto([(T0 + timedelta(minutes=i), 12.0) for i in range(3000)])
    X, Y, P = ep.muestras(s)
    _, _, hs = ep.entrenar(X, Y, P, X, Y, P)
    assert all(not h["supera_persistencia"] for h in hs.values())
