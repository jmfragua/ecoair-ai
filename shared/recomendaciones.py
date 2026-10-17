"""Recomendaciones de salud por categoria del ICA. UNA sola fuente de verdad para el dashboard,
el correo de bienvenida, los correos de alerta y el alerter.

TEXTO FIJO DE LA NORMA, NO IA: escrito a mano a partir de las fuentes citadas; ningun modelo lo
genera ni lo modifica. No se anadio ningun consejo medico propio.

Fuentes (texto en docs/normas/texto/; paginas segun sus marcas "=== PAGINA N ==="):
- RES_T5: Res. 2254/2017, Art. 19, Tabla 5 (PDF pags. 8-9): "efectos" de cada categoria y quienes
  son los grupos sensibles para material particulado.
- EPA_T5: EPA AQI Technical Assistance Document (mayo 2026), Tabla 5, columna de material
  particulado (PM2.5/PM10, 24 h), pags. impresas 8-12 (PDF 13-17): indicaciones por grupo.
  Traduccion del equipo.
- EPA_T4: mismo documento, Tabla 4 "Pollutant-specific Sensitive Groups", pag. impresa 7 (PDF 12).
- EPA_I: mismo documento, seccion I, pag. impresa 1 (PDF 6): el AQI hasta 100 se considera en
  general satisfactorio; por encima, es danino primero para grupos sensibles y luego para todos.

Supuesto del proyecto: las categorias EPA se asocian a las colombianas por rango del indice
(0-50, 51-100, ...). Los cortes de concentracion no son iguales, asi que lo de la EPA es orientativo.

Campos por categoria: efectos, poblacion_general, grupos_sensibles {quienes, recomendacion},
acciones (lista corta), cuando_consultar (None si la fuente no lo respalda), si_sube (None en la
categoria mas alta), resumen (frase corta), fuentes (que bloque sale de que tabla y pagina).
"""

RES_T5 = {"documento": "Res. 2254/2017, Art. 19, Tabla 5", "pagina": "8 (PDF)"}
RES_T5_P9 = {"documento": "Res. 2254/2017, Art. 19, Tabla 5", "pagina": "9 (PDF)"}
EPA_DOC = "EPA AQI Technical Assistance Document (mayo 2026)"
EPA_T4 = {"documento": f"{EPA_DOC}, Tabla 4, grupos sensibles", "pagina": "7 impresa (PDF 12)"}
EPA_I = {"documento": f"{EPA_DOC}, sección I", "pagina": "1 impresa (PDF 6)"}


def _epa_t5(pagina_impresa):
    return {"documento": f"{EPA_DOC}, Tabla 5, material particulado",
            "pagina": f"{pagina_impresa} impresa (PDF {pagina_impresa + 5})"}


ETIQUETA = "Texto fijo de la norma, no IA"
ORDEN = ["buena", "aceptable", "danina_sensibles", "danina", "muy_danina", "peligrosa"]
NOMBRES = {
    "buena": "Buena (ICA 0–50)", "aceptable": "Aceptable (ICA 51–100)",
    "danina_sensibles": "Dañina para grupos sensibles (ICA 101–150)",
    "danina": "Dañina para la salud (ICA 151–200)", "muy_danina": "Muy dañina para la salud (ICA 201–300)",
    "peligrosa": "Peligrosa (ICA 301–500)",
}

# Quienes son los grupos sensibles para material particulado: Res. 2254 Tabla 5 + EPA Tabla 4.
QUIENES = ("Niños (incluidos adolescentes), adultos mayores y personas con enfermedades del corazón "
           "o de los pulmones (como el asma). Quienes trabajan al aire libre o tienen menos recursos "
           "pueden estar más expuestos.")

ASMA = "Si tienes asma, sigue tu plan de acción para el asma y ten a mano tu medicamento de alivio rápido."
CORAZON = ("Personas con enfermedad del corazón: síntomas como palpitaciones, falta de aire o cansancio "
           "inusual pueden indicar un problema serio; si los tienes, contacta a tu proveedor de salud.")
CALOR = ("Ojo con el calor: si no tienes aire acondicionado, quedarse adentro con las ventanas cerradas "
         "puede ser peligroso con calor extremo; busca un lugar fresco.")

EFECTOS = {
    "buena": "La contaminación atmosférica supone un riesgo bajo para la salud.",
    "aceptable": "Posibles síntomas respiratorios en grupos poblacionales sensibles.",
    "danina_sensibles": "Los grupos poblacionales sensibles pueden presentar efectos sobre la salud.",
    "danina": ("Todos los individuos pueden comenzar a experimentar efectos sobre la salud. Los grupos "
               "sensibles pueden experimentar efectos más graves para la salud."),
    "muy_danina": "Estado de alerta que significa que todos pueden experimentar efectos más graves para la salud.",
    "peligrosa": ("Advertencia sanitaria. Toda la población puede presentar efectos adversos graves en la "
                  "salud humana y están propensos a verse afectados por graves efectos sobre la salud."),
}


def _si_sube(siguiente):
    return f"Si sube a {NOMBRES[siguiente]}: {EFECTOS[siguiente]}"


RECOMENDACIONES = {
    "buena": {
        "efectos": EFECTOS["buena"],
        "resumen": "Es un gran día para estar activo al aire libre.",
        "poblacion_general": "Es un gran día para estar activo al aire libre.",
        "grupos_sensibles": {"quienes": QUIENES,
                             "recomendacion": "La EPA no da indicaciones especiales para esta categoría."},
        "acciones": ["Puedes hacer actividades al aire libre con normalidad."],
        "cuando_consultar": None,
        "si_sube": _si_sube("aceptable"),
        "fuentes": {"efectos": RES_T5, "poblacion_general": _epa_t5(8), "grupos_sensibles": EPA_T4,
                    "acciones": _epa_t5(8), "si_sube": RES_T5},
    },
    "aceptable": {
        "efectos": EFECTOS["aceptable"],
        "resumen": "Personas inusualmente sensibles: actividades al aire libre más cortas y menos intensas.",
        "poblacion_general": "La calidad del aire se considera en general satisfactoria.",
        "grupos_sensibles": {
            "quienes": QUIENES,
            "recomendacion": ("Personas inusualmente sensibles: consideren hacer las actividades al aire "
                              "libre más cortas y menos intensas. Entren a un lugar cerrado si tienen "
                              "síntomas como tos o falta de aire.")},
        "acciones": ["Si eres inusualmente sensible, acorta y suaviza las actividades al aire libre.",
                     "Si aparece tos o falta de aire, entra a un lugar cerrado."],
        "cuando_consultar": None,
        "si_sube": _si_sube("danina_sensibles"),
        "fuentes": {"efectos": RES_T5, "poblacion_general": EPA_I, "grupos_sensibles": _epa_t5(8),
                    "acciones": _epa_t5(8), "si_sube": RES_T5},
    },
    "danina_sensibles": {
        "efectos": EFECTOS["danina_sensibles"],
        "resumen": "Grupos sensibles: actividades al aire libre más cortas y menos intensas, con más pausas.",
        "poblacion_general": ("Por encima de 100 el aire es dañino primero para los grupos sensibles; la EPA "
                              "no da indicaciones especiales para el resto de la población en esta categoría."),
        "grupos_sensibles": {
            "quienes": QUIENES,
            "recomendacion": ("Hagan las actividades al aire libre más cortas y menos intensas. Pueden estar "
                              "activos al aire libre, pero tomen más pausas. Estén atentos a síntomas como "
                              "tos o falta de aire.")},
        "acciones": ["Grupos sensibles: acorta y suaviza las actividades al aire libre y toma más pausas.",
                     "Vigila síntomas como tos o falta de aire.", ASMA],
        "cuando_consultar": CORAZON,
        "si_sube": _si_sube("danina"),
        "fuentes": {"efectos": RES_T5, "poblacion_general": EPA_I, "grupos_sensibles": _epa_t5(9),
                    "acciones": _epa_t5(9), "cuando_consultar": _epa_t5(9), "si_sube": RES_T5},
    },
    "danina": {
        "efectos": EFECTOS["danina"],
        "resumen": "Todos: actividades al aire libre más cortas y menos intensas; grupos sensibles, mejor bajo techo.",
        "poblacion_general": ("Haz las actividades al aire libre más cortas y menos intensas. Entra a un "
                              "lugar cerrado si tienes síntomas."),
        "grupos_sensibles": {
            "quienes": QUIENES,
            "recomendacion": ("Consideren reprogramar o pasar todas las actividades a un lugar cerrado. "
                              "Entren si tienen síntomas.")},
        "acciones": ["Grupos sensibles: reprograma o pasa las actividades a un lugar cerrado.",
                     "Todos: acorta y suaviza las actividades al aire libre.",
                     "Si tienes síntomas, entra a un lugar cerrado.", ASMA],
        "cuando_consultar": CORAZON,
        "si_sube": _si_sube("muy_danina"),
        "fuentes": {"efectos": RES_T5, "poblacion_general": _epa_t5(10), "grupos_sensibles": _epa_t5(10),
                    "acciones": _epa_t5(10), "cuando_consultar": _epa_t5(10), "si_sube": RES_T5_P9},
    },
    "muy_danina": {
        "efectos": EFECTOS["muy_danina"],
        "resumen": "Grupos sensibles: eviten toda actividad física al aire libre. Todos: limítenla.",
        "poblacion_general": ("Limita la actividad física al aire libre. Entra a un lugar cerrado si tienes "
                              "síntomas."),
        "grupos_sensibles": {
            "quienes": QUIENES,
            "recomendacion": ("Eviten toda actividad física al aire libre. Reprogramen para cuando la "
                              "calidad del aire sea mejor o pasen las actividades a un lugar cerrado.")},
        "acciones": ["Grupos sensibles: evita toda actividad física al aire libre.",
                     "Todos: limita la actividad física al aire libre.",
                     "Si tienes síntomas, entra a un lugar cerrado.", ASMA, CALOR],
        "cuando_consultar": CORAZON,
        "si_sube": _si_sube("peligrosa"),
        "fuentes": {"efectos": RES_T5_P9, "poblacion_general": _epa_t5(11), "grupos_sensibles": _epa_t5(11),
                    "acciones": _epa_t5(11), "cuando_consultar": _epa_t5(11), "si_sube": RES_T5_P9,
                    "nota_calor": {**_epa_t5(12), "documento": f"{EPA_DOC}, Tabla 5, nota al pie (*)"}},
    },
    "peligrosa": {
        "efectos": EFECTOS["peligrosa"],
        "resumen": "Todos: eviten toda actividad física al aire libre.",
        "poblacion_general": "Evita toda actividad física al aire libre.",
        "grupos_sensibles": {
            "quienes": QUIENES,
            "recomendacion": ("Permanezcan en un lugar cerrado y mantengan un nivel de actividad ligero. "
                              "Sigan recomendaciones para mantener bajos los niveles de partículas en "
                              "interiores.")},
        "acciones": ["Todos: evita toda actividad física al aire libre.",
                     "Grupos sensibles: quédate en un lugar cerrado con actividad ligera.", ASMA, CALOR],
        "cuando_consultar": CORAZON,
        "si_sube": None,
        "fuentes": {"efectos": RES_T5_P9, "poblacion_general": _epa_t5(12), "grupos_sensibles": _epa_t5(12),
                    "acciones": _epa_t5(12), "cuando_consultar": _epa_t5(12),
                    "nota_calor": {**_epa_t5(12), "documento": f"{EPA_DOC}, Tabla 5, nota al pie (*)"}},
    },
}

# "Quienes son los grupos sensibles" sale de las dos fuentes en todas las categorias.
for _r in RECOMENDACIONES.values():
    _r["fuentes"]["grupos_sensibles_quienes"] = EPA_T4
    _r["fuentes"]["grupos_sensibles_quienes_res"] = RES_T5

CAMPOS_TEXTO = ("efectos", "resumen", "poblacion_general")
# Titulos de seccion: los mismos en el correo y en el dashboard.
TITULOS = {"efectos": "Qué significa", "poblacion_general": "Para todos", "grupos_sensibles": "Grupos sensibles",
           "acciones": "Qué hacer", "cuando_consultar": "Cuándo consultar", "si_sube": "Si sube"}


def fuentes_unicas(rec):
    """Lista sin repetir de {documento, pagina} usadas por una categoria (para el pie)."""
    vistas, out = set(), []
    for f in rec["fuentes"].values():
        clave = (f["documento"], f["pagina"])
        if clave not in vistas:
            vistas.add(clave)
            out.append(dict(f))
    return out


def recomendacion(category_code):
    """Copia de la recomendacion completa de una categoria (dict), lista para JSON.

    Incluye tambien los campos anteriores ('recomendacion' = resumen y 'fuente' = lista de
    referencias) para no romper a quien ya los usa.
    """
    r = RECOMENDACIONES[category_code]
    return {
        "categoria": category_code,
        "nombre": NOMBRES[category_code],
        "efectos": r["efectos"],
        "resumen": r["resumen"],
        "poblacion_general": r["poblacion_general"],
        "grupos_sensibles": dict(r["grupos_sensibles"]),
        "acciones": list(r["acciones"]),
        "cuando_consultar": r["cuando_consultar"],
        "si_sube": r["si_sube"],
        "fuentes_por_bloque": {k: dict(v) for k, v in r["fuentes"].items()},
        "fuente": fuentes_unicas(r),
        "recomendacion": r["resumen"],
        "titulos": dict(TITULOS),
        "etiqueta": ETIQUETA,
        "tipo": "texto_fijo_no_ia",
    }
