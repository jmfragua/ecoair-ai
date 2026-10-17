# ADR 0003 · Modelo de predicción PM2.5: v2 descartado, v3 activo

- **Estado:** Aceptado (2026-10-17)
- **Cifras:** todas salen de `modelos/prediccion_pm25_v1.json`, `prediccion_pm25_v2.json` y
  `prediccion_pm25_v3.json` (campos `metricas`, `horizontes_visibles`, `sesgo_series_constantes`,
  `prueba_no_virgen`). MAE y RMSE en µg/m³.

## Contexto
- **v1** (`prediccion_pm25_v1.json`): Ridge sobre el residuo de la persistencia, por minuto,
  horizontes 1 y 3 h. Entrenado y evaluado **solo con datos simulados** (fuente `simulados`).
  En su evaluación, a 1 h no superó a la persistencia (MAE 2,954 frente a 2,740) y a 3 h sí
  (4,221 frente a 4,675). Es una prueba de software, no evidencia de precisión.
- **v2** y **v3**: entrenados con PM2.5 horario externo de **OpenAQ, estación MED-UNNV** (Universidad
  Nacional, sede El Volador, Medellín), 2018-11 a 2020-02, solo desde el CSV local. División
  cronológica: entrenamiento 2018-11 a 2019-09, validación 2019-10 a 2019-11 (solo para elegir
  alpha) y prueba 2019-12 a 2020-02 (1947 horas). Horizontes 1, 3 y 6 h. Rivales: persistencia y
  media por hora del día (calculada solo con el entrenamiento).

## Por qué se descartó v2
- v2 usa variables de **nivel absoluto** (`pm25_h1`, `pm25_h2`, `pm25_h3`, `media_3h`). En la prueba
  era el mejor modelo (MAE 3,985 / 5,884 / 6,668 a 1 / 3 / 6 h, frente a 4,078 / 6,619 / 8,457
  de la persistencia).
- Con **series constantes** (3, 6, 12 y 25 µg/m³ a varias horas del día) se vio un **sesgo hacia
  arriba en niveles bajos**: las variables de nivel tienen coeficientes negativos y llevan la
  predicción hacia la media del entrenamiento de MED-UNNV (regresión a la media). Los datos del
  sensor propio rondan esos niveles bajos, así que v2 predeciría de más justo donde se usaría.
  Las cifras de ese sesgo no están en el JSON de v2; se reproducen con
  `sesgo_constantes()` de `ai/entrenar_prediccion_v3.py` aplicada a v2.

## Por qué v3 usa variables relativas
- v3 (`"transformacion": "relativa"`, `c` = 2,0) usa log-razones respecto a la última hora
  (`log((h2+c)/(h1+c))`, `log((h3+c)/(h1+c))`, `log((media_3h+c)/(h1+c))`), una pendiente relativa
  y la hora del día. El objetivo es `log((y+c)/(h1+c))` y la predicción `(h1+c)·exp(f) − c`.
  El modelo no "conoce" el nivel absoluto de Medellín, así que no regresa a su media.
- Criterio de sesgo declarado antes de entrenar: en los niveles 3 y 6, |pred − valor| ≤ 1,0 a 1 y 3 h
  y ≤ 1,5 a 6 h. v3 lo **cumple** en los tres horizontes (`cumple_sesgo: true`). Peor caso en
  esos niveles según su JSON: 0,284 (1 h), 0,267 (3 h) y 1,300 (6 h). La desviación es
  proporcional al nivel (invariancia de escala).

## Qué se muestra: solo 6 h
Regla declarada: un horizonte es visible solo si mejora el MAE de la persistencia en **al menos 5 %**
en la prueba **y** cumple el criterio de sesgo. Resultado (`horizontes_visibles: [360]`):

| Horizonte | MAE v3 | MAE persistencia | Mejora | ¿Visible? |
|---|---|---|---|---|
| 1 h | 4,041 | 4,078 | 0,9 % | No (< 5 %) |
| 3 h | 6,380 | 6,619 | 3,6 % | No (< 5 %) |
| 6 h | 7,384 | 8,457 | 12,7 % | Sí |

## Limitaciones
- **La prueba no es virgen** (`prueba_no_virgen`): el periodo 2019-12 a 2020-02 se miró con v2 y
  otra vez con v3, dos veces en total.
- **A 6 h la media por hora del día gana:** su MAE es 7,049 y el de v3 es 7,384. v3 supera a la
  persistencia, pero no a ese rival simple.
- **v3 es peor que v2 en MAE en los tres horizontes** (v2: 3,985 / 5,884 / 6,668). Se acepta a
  cambio de quitar el sesgo de nivel.
- **Sensor de referencia distinto:** MED-UNNV es una estación oficial; el nodo propio usa un SDS011
  de bajo costo en otro lugar. Las métricas no representan la precisión con el sensor propio.
- **Datos antiguos:** 2018 a 2020, solo PM2.5, sin temperatura ni humedad.

## Decisión
1. **v3 es el modelo activo.** Se activa en api y notifier con
   `MODELO_PREDICCION_PATH=/app/modelos/prediccion_pm25_v3.json`; el valor por defecto en el
   código sigue siendo v1. v2 no se copia a las imágenes.
2. **Modelos congelados.** No se reentrenan v1, v2 ni v3 con este conjunto de prueba. Un modelo
   nuevo necesita datos nuevos (idealmente reales del nodo propio) y un periodo de prueba no visto.
3. **La predicción se presenta como experimental**, con el aviso del JSON: "Entrenado con datos
   externos históricos de Medellín (2018-2020); no representa la precisión con el sensor propio."
   La persistencia nunca se presenta como predicción.
