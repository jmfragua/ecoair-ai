# Normas de calidad del aire — referencia para EcoAir AI

Todos los valores de este archivo se leyeron de los PDF de esta carpeta (res_2254_2017.pdf, who_aqg_2021.pdf, epa_aqi_tad.pdf).
La resolución es un escaneo; sus tablas se leyeron de la imagen de la página. Aun así, revisa cada cifra contra el PDF antes de citarla.

## 1. Resolución 2254 de 2017 (Colombia, MinAmbiente, 1 nov 2017)

### Niveles máximos permisibles (Art. 2, Tabla 1; vigentes desde 1 jul 2018)
| Contaminante | 24 h | Anual |
|---|---|---|
| PM2.5 | 37 µg/m³ | 25 µg/m³ |
| PM10 | 75 µg/m³ | 50 µg/m³ |

Metas 2030 (Art. 3, Tabla 2): PM2.5 anual 15 y PM10 anual 30 µg/m³.

### Prevención, alerta y emergencia (Tabla 4, promedio 24 h)
| Nivel | PM10 (µg/m³) | PM2.5 (µg/m³) |
|---|---|---|
| Prevención | 155 – 254 | 38 – 55 |
| Alerta | 255 – 354 | 56 – 150 |
| Emergencia | >= 355 | >= 151 |

Los niveles por PM2.5 se aplican desde el 1 jul 2018.

### Puntos de corte del ICA (Art. 20, Tabla 6, promedio 24 h, µg/m³)
| ICA | Color | Categoría | PM10 | PM2.5 |
|---|---|---|---|---|
| 0 – 50 | Verde | Buena | 0 – 54 | 0 – 12 |
| 51 – 100 | Amarillo | Aceptable | 55 – 154 | 13 – 37 |
| 101 – 150 | Naranja | Dañina a la salud de grupos sensibles | 155 – 254 | 38 – 55 |
| 151 – 200 | Rojo | Dañina a la salud | 255 – 354 | 56 – 150 |
| 201 – 300 | Púrpura | Muy dañina a la salud | 355 – 424 | 151 – 250 |
| 301 – 500 | Marrón | Peligrosa | 425 – 604 | 251 – 500 |

### Fórmula del ICA (Art. 21)
ICA_p = ((I_alto - I_bajo) / (PC_alto - PC_bajo)) * (C_p - PC_bajo) + I_bajo

- C_p: concentración medida del contaminante p.
- PC_alto / PC_bajo: puntos de corte que encierran a C_p.
- I_alto / I_bajo: valores del ICA de esos puntos de corte.
- El ICA se calcula por cada contaminante. Se reporta el más alto (Art. 21, Parágrafo 1: se calcula para cada contaminante medido).

### Efectos por categoría (Art. 19, Tabla 5)
- Buena: riesgo bajo para la salud.
- Aceptable: posibles síntomas respiratorios en grupos sensibles.
- Dañina a la salud de grupos sensibles: los grupos sensibles pueden presentar efectos. Material particulado: personas con enfermedad cardiaca o pulmonar, adultos mayores y niños.
- Dañina a la salud: todos pueden comenzar a experimentar efectos; los grupos sensibles, efectos más graves.
- Muy dañina a la salud: estado de alerta; todos pueden experimentar efectos más graves.
- Peligrosa: advertencia sanitaria; toda la población puede presentar efectos adversos graves.
Los textos completos están en la página 8-9 del PDF. Para las recomendaciones al usuario, copiar la redacción literal y citar la página.

## 2. OMS, Guías de calidad del aire 2021 (Tabla 0.1)
| Contaminante | 24 h (AQG) | Anual (AQG) |
|---|---|---|
| PM2.5 | 15 µg/m³ | 5 µg/m³ |
| PM10 | 45 µg/m³ | 15 µg/m³ |

Metas intermedias PM2.5 24 h: 75, 50, 37.5, 25. PM10 24 h: 150, 100, 75, 50.
Son recomendaciones de salud, no norma obligatoria en Colombia.

## 3. EPA AQI (TAD de AirNow, mayo 2026), Tabla 6, promedio 24 h
| AQI | Categoría | PM2.5 (µg/m³) | PM10 (µg/m³) |
|---|---|---|---|
| 0 – 50 | Good | 0.0 – 9.0 | 0 – 54 |
| 51 – 100 | Moderate | 9.1 – 35.4 | 55 – 154 |
| 101 – 150 | Unhealthy for Sensitive Groups | 35.5 – 55.4 | 155 – 254 |
| 151 – 200 | Unhealthy | 55.5 – 125.4 | 255 – 354 |
| 201 – 300 | Very Unhealthy | 125.5 – 225.4 | 355 – 424 |
| >= 301 | Hazardous | >= 225.5 | >= 425 |

Mismo esquema de fórmula que Colombia (Ecuación 1 del TAD). Truncar PM2.5 a 1 decimal y PM10 a entero.
La EPA define el NowCast (promedio ponderado de 12 h) para mostrar el AQI casi en tiempo real. Las cautionary statements por categoría están en la Tabla 5 del TAD (páginas 8-12).

## 4. Diferencias importantes Colombia vs EPA
- PM2.5: Colombia 0-12 / 13-37 / 38-55 / 56-150 / 151-250 / 251-500. EPA 0-9 / 9.1-35.4 / 35.5-55.4 / 55.5-125.4 / 125.5-225.4 / 225.5+. La EPA es más estricta en los niveles bajos.
- PM10: los puntos de corte coinciden en la parte baja (0-54, 55-154, 155-254, 255-354), y en el rango alto Colombia 355-424 / 425-604.
- La norma colombiana de Tabla 4 coincide con las bandas naranja y rojo del ICA de PM2.5 (38-55, 56-150).

## 5. Decisiones de diseño (pasar a ADR)
- Norma principal: Res. 2254/2017. OMS 2021 como referencia de salud. EPA solo como apoyo de redacción, citando la fuente.
- Las normas usan promedio de 24 h. Propuesta: mostrar con media móvil de 1 h y calcular el ICA oficial con 24 h. Documentarlo como limitación.
- El SDS011 es de bajo costo; no sustituye una estación de referencia certificada.
- Recomendaciones = texto fijo por categoría (reglas). La IA entra con RAG sobre estos documentos, citando página. El modelo no calcula ni inventa cifras: el ICA lo calcula código con la fórmula del Art. 21.
