"""Indice de Calidad del Aire (ICA) segun la Resolucion 2254 de 2017 (Colombia).

Modulo puro: sin base de datos ni red. Fuente de las cifras: docs/normas/normas_aire.md
(Tabla 4, Tabla 6 y Art. 21 de la Resolucion 2254 de 2017).

Supuestos del proyecto (no estan en la Resolucion):
- Las tablas tienen huecos entre filas (p. ej. PM2.5: 12 y 13). Antes de buscar la fila,
  la concentracion se trunca a entero: PM2.5 12.5 pasa a 12.
- El ICA se redondea al entero mas cercano con la mitad hacia arriba (75.5 -> 76).
  El calculo usa Decimal multiplicando antes de dividir, para que un .5 exacto no
  se convierta en 75.4999... por error de coma flotante.
- Una concentracion mayor al ultimo punto de corte da ICA 500, categoria peligrosa.
- Las tablas usan promedios de 24 h; este modulo no promedia, calcula con el valor recibido.
"""

import math
from decimal import ROUND_HALF_UP, Decimal

# Tabla 6, promedio 24 h, ug/m3.
# Cada fila: (ica_low, ica_high, conc_low, conc_high, category_code, category_name, color)
BREAKPOINTS = {
    "pm25": (
        (0, 50, 0, 12, "buena", "Buena", "verde"),
        (51, 100, 13, 37, "aceptable", "Aceptable", "amarillo"),
        (101, 150, 38, 55, "danina_sensibles", "Dañina a la salud de grupos sensibles", "naranja"),
        (151, 200, 56, 150, "danina", "Dañina a la salud", "rojo"),
        (201, 300, 151, 250, "muy_danina", "Muy dañina a la salud", "purpura"),
        (301, 500, 251, 500, "peligrosa", "Peligrosa", "marron"),
    ),
    "pm10": (
        (0, 50, 0, 54, "buena", "Buena", "verde"),
        (51, 100, 55, 154, "aceptable", "Aceptable", "amarillo"),
        (101, 150, 155, 254, "danina_sensibles", "Dañina a la salud de grupos sensibles", "naranja"),
        (151, 200, 255, 354, "danina", "Dañina a la salud", "rojo"),
        (201, 300, 355, 424, "muy_danina", "Muy dañina a la salud", "purpura"),
        (301, 500, 425, 604, "peligrosa", "Peligrosa", "marron"),
    ),
}

# Tabla 4, promedio 24 h, ug/m3: limite inferior de cada nivel, de mayor a menor.
ALERT_LEVELS = {
    "pm25": (("emergencia", 151), ("alerta", 56), ("prevencion", 38)),
    "pm10": (("emergencia", 355), ("alerta", 255), ("prevencion", 155)),
}


def _truncar(pollutant, concentracion):
    """Valida el contaminante y la concentracion y devuelve la concentracion truncada a entero."""
    if pollutant not in BREAKPOINTS:
        raise ValueError(f"contaminante desconocido: {pollutant!r} (usa 'pm25' o 'pm10')")
    if isinstance(concentracion, bool) or not isinstance(concentracion, (int, float, Decimal)):
        raise ValueError(f"concentracion no numerica: {concentracion!r}")
    if not math.isfinite(concentracion):
        raise ValueError(f"concentracion no finita: {concentracion!r}")
    if concentracion < 0:
        raise ValueError(f"concentracion negativa: {concentracion!r}")
    return int(concentracion)


def calcular_ica(pollutant, concentracion):
    """ICA de un contaminante ('pm25' o 'pm10') con la formula del Art. 21.

    ICA = (I_alto - I_bajo) / (PC_alto - PC_bajo) * (C - PC_bajo) + I_bajo

    La concentracion se trunca a entero antes de buscar la fila (supuesto del proyecto,
    ver el docstring del modulo). Lanza ValueError si la concentracion es negativa,
    no numerica o no finita.
    """
    c = _truncar(pollutant, concentracion)
    rows = BREAKPOINTS[pollutant]

    if c > rows[-1][3]:
        ica = 500
        row = rows[-1]
    else:
        row = next(r for r in rows if r[2] <= c <= r[3])
        ica_low, ica_high, conc_low, conc_high = row[:4]
        # Multiplicar antes de dividir: con enteros, un .5 exacto queda exacto en Decimal.
        valor = (
            Decimal((ica_high - ica_low) * (c - conc_low)) / Decimal(conc_high - conc_low)
            + ica_low
        )
        ica = int(valor.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    return {
        "ica": ica,
        "category_code": row[4],
        "category_name": row[5],
        "color": row[6],
        "concentracion_usada": c,
    }


def ica_general(pm25, pm10):
    """ICA general: el mas alto entre PM2.5 y PM10 (Art. 21, Paragrafo 1).

    Devuelve el resultado de calcular_ica del contaminante que define el ICA,
    con la clave extra 'pollutant'. Si empatan, gana pm25.
    """
    r25 = calcular_ica("pm25", pm25)
    r10 = calcular_ica("pm10", pm10)
    if r10["ica"] > r25["ica"]:
        return {**r10, "pollutant": "pm10"}
    return {**r25, "pollutant": "pm25"}


def nivel_alerta(pollutant, concentracion):
    """Nivel de la Tabla 4: None, "prevencion", "alerta" o "emergencia".

    Supuesto del proyecto, no de la Resolucion 2254: la concentracion se trunca a entero
    antes de comparar, porque la Tabla 4 tiene huecos entre niveles (PM2.5: 37 y 38).
    Asi, PM2.5 37.9 se trata como 37 y no activa nivel. La Resolucion no indica como
    tratar los valores con decimales.
    """
    c = _truncar(pollutant, concentracion)
    for nivel, minimo in ALERT_LEVELS[pollutant]:
        if c >= minimo:
            return nivel
    return None
