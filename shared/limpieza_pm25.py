"""Limpieza de series horarias de PM2.5 externas (IDEAM datos.gov.co y SISAIRE), independiente del formato.
La usan tools/importar_ideam.py, tools/importar_sisaire.py y el replay historico de la API.

Recibe filas ya leidas {"station", "measured_at" (datetime con zona, UTC), "pm25" (float o None)}
y devuelve las validas mas un resumen con cada motivo de descarte contado aparte.

Reglas (en este orden):
1. Nulo -> descartado ("nulos").
2. Negativo -> descartado ("negativos").
3. Exactamente 0 -> descartado ("ceros"): en estas estaciones un cero exacto indica falla del equipo.
4. Mayor que 1000 ug/m3 (CHECK de external_measurements) -> descartado ("mayores_1000").
5. Misma estacion y hora repetida -> se conserva la primera ("duplicadas").
6. Rachas: 6 o mas horas SEGUIDAS de la misma estacion con exactamente el mismo valor -> se
   descarta la racha completa. "Seguidas" = cada hora es la anterior + 1 h; un hueco o un valor
   distinto corta la racha. Se evalua despues de las reglas 1-5 (un cero nunca forma racha porque
   ya se descarto). Se cuentan aparte: numero de rachas, horas descartadas y el detalle.
"""

from collections import Counter, defaultdict
from datetime import timedelta

MIN_RACHA_HORAS = 6
MAX_PM25 = 1000.0
UNA_HORA = timedelta(hours=1)


def _rachas(serie):
    """serie: [(measured_at, pm25)] ordenada. -> lista de (i_inicio, i_fin) inclusive con largo >= MIN."""
    out, ini = [], 0
    for i in range(1, len(serie) + 1):
        corta = (i == len(serie) or serie[i][1] != serie[i - 1][1] or serie[i][0] - serie[i - 1][0] != UNA_HORA)
        if corta:
            if i - ini >= MIN_RACHA_HORAS:
                out.append((ini, i - 1))
            ini = i
    return out


def limpiar(filas):
    """-> (filas validas ordenadas por estacion y hora, resumen)."""
    c = Counter()
    por_est = defaultdict(dict)
    for f in filas:
        c["leidas"] += 1
        v = f.get("pm25")
        if v is None:
            c["nulos"] += 1
            continue
        if v < 0:
            c["negativos"] += 1
            continue
        if v == 0:
            c["ceros"] += 1
            continue
        if v > MAX_PM25:
            c["mayores_1000"] += 1
            continue
        st, t = f["station"], f["measured_at"]
        if t in por_est[st]:
            c["duplicadas"] += 1
            continue
        por_est[st][t] = f
    validas, rachas = [], []
    for st in sorted(por_est):
        horas = sorted(por_est[st])
        serie = [(t, por_est[st][t]["pm25"]) for t in horas]
        fuera = set()
        for a, b in _rachas(serie):
            rachas.append({"station": st, "inicio": serie[a][0], "fin": serie[b][0],
                           "valor": serie[a][1], "horas": b - a + 1})
            fuera.update(range(a, b + 1))
        validas += [por_est[st][t] for i, t in enumerate(horas) if i not in fuera]
    c["rachas"] = len(rachas)
    c["horas_en_rachas"] = sum(r["horas"] for r in rachas)
    c["validas"] = len(validas)
    return validas, {"conteos": dict(c), "rachas": rachas}


def imprimir_resumen(res, max_rachas=20):
    c = res["conteos"]
    print(f"Filas leidas: {c.get('leidas', 0)} · validas: {c.get('validas', 0)}")
    for k, txt in (("nulos", "nulos"), ("negativos", "negativos"), ("ceros", "cero exacto (falla del equipo)"),
                   ("mayores_1000", "> 1000 ug/m3"), ("duplicadas", "duplicadas (misma estacion y hora)")):
        print(f"  descartadas por {txt}: {c.get(k, 0)}")
    print(f"  rachas de {MIN_RACHA_HORAS}+ horas con el mismo valor: {c.get('rachas', 0)} "
          f"({c.get('horas_en_rachas', 0)} horas descartadas)")
    for r in res["rachas"][:max_rachas]:
        print(f"    {r['station']} · {r['inicio']:%Y-%m-%d %H:%M} -> {r['fin']:%Y-%m-%d %H:%M} UTC · "
              f"{r['horas']} h con {r['valor']:g} ug/m3")
    if len(res["rachas"]) > max_rachas:
        print(f"    ... y {len(res['rachas']) - max_rachas} rachas mas")
