"""Mapa del panel: dónde están las estaciones (lógica pura, sin base de datos).

- Estaciones de referencia del replay: coordenadas COPIADAS de la fuente oficial, nunca estimadas.
  Si una estación del replay no está en COORDENADAS, no se dibuja y la respuesta lo dice.
- Estación EcoAir: punto APROXIMADO que define el equipo con la variable ECOAIR_UBICACION_APROX
  ("lat,lon"). Se redondea a 2 decimales (~1 km) para no publicar una dirección exacta. Si falta o
  no es válida, no se dibuja.
"""
import os

import replay

FUENTE_IDEAM = ("IDEAM – Calidad del Aire en Colombia, datos.gov.co (conjunto g4t8-zkc3, "
                "columnas latitud y longitud), consultado el 2026-10-24")

# station del replay -> coordenadas tal como las publica el IDEAM (una sola pareja por estación).
# Contraste con el catálogo del SIATA (siata.gov.co/CalidadAire/cadatos/Estaciones.txt, 2026-10-24):
# Casa de Justicia idéntica; Tráfico Centro y Tomás Carrasquilla a menos de 25 m; Fernando Botero
# a unos 300 m; Fiscalía Caribe no aparece con ese nombre en el SIATA.
COORDENADAS = {
    "29509": {"lat": 6.252709, "lon": -75.569656, "fuente": FUENTE_IDEAM},
    "31854": {"lat": 6.268792, "lon": -75.573653, "fuente": FUENTE_IDEAM},
    "29493": {"lat": 6.185667, "lon": -75.597206, "fuente": FUENTE_IDEAM},
    "31366": {"lat": 6.277838, "lon": -75.636363, "fuente": FUENTE_IDEAM},
    "31946": {"lat": 6.285101, "lon": -75.583145, "fuente": FUENTE_IDEAM},
}

DECIMALES_ECOAIR = 2
NOMBRE_ECOAIR = "Estación EcoAir (ecoair-node-01)"
SIN_ECOAIR = "La ubicación de la estación EcoAir no está configurada: no se dibuja."


def ubicacion_ecoair(valor):
    """'6.25,-75.57' -> {lat, lon} redondeados; None si falta o no es una coordenada válida."""
    if not valor or not str(valor).strip():
        return None
    partes = str(valor).split(",")
    if len(partes) != 2:
        return None
    try:
        lat, lon = (float(p.strip()) for p in partes)
    except ValueError:
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):     # tambien descarta nan e inf
        return None
    return {"lat": round(lat, DECIMALES_ECOAIR), "lon": round(lon, DECIMALES_ECOAIR)}


def construir_mapa(valor_ecoair=None):
    referencia, faltantes = [], []
    for codigo, info in replay.ESTACIONES.items():
        c = COORDENADAS.get(codigo)
        if not c:
            faltantes.append({"station": codigo, "nombre": info["nombre"]})
            continue
        referencia.append({"station": codigo, "nombre": info["nombre"], "lat": c["lat"], "lon": c["lon"],
                           "fuente": c["fuente"], "tipo": "referencia"})
    punto = ubicacion_ecoair(valor_ecoair)
    ecoair = None
    if punto:
        ecoair = {**punto, "nombre": NOMBRE_ECOAIR, "tipo": "ecoair", "aproximada": True,
                  "fuente": "Ubicación aproximada definida por el equipo EcoAir (redondeada a ~1 km)"}
    return {
        "estaciones": referencia,
        "faltantes": faltantes,
        "ecoair": ecoair,
        "mensaje_ecoair": "" if ecoair else SIN_ECOAIR,
    }


def mapa_desde_entorno():
    return construir_mapa(os.getenv("ECOAIR_UBICACION_APROX"))
