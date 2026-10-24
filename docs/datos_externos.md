# Datos externos de PM2.5 (estaciones de terceros)

Datos de estaciones oficiales del Valle de Aburrá que EcoAir usa **solo** para entrenar y evaluar los
modelos de predicción y para el **replay histórico** del panel. Van en la tabla `external_measurements` (migración 006), separada de
`measurements`: nunca se mezclan con el sensor propio (`ecoair-node-*`) ni con los datos simulados
(`ecoair-sim-01`). El notifier y el alerter **no** leen esta tabla; la API solo la lee en el replay
histórico (`/predictions/replay*`, solo lectura, al vuelo).

Todas las cifras de este documento salen de consultas reales a las fuentes o de los JSON de los
modelos en `modelos/`. Fecha de corte: 2026-10-24.

## Fuentes

| Fuente (`source`) | Qué es | Estaciones | Periodo útil | Cómo se obtiene | Licencia / condiciones |
|---|---|---|---|---|---|
| `OpenAQ archive` | Archivo público de OpenAQ (S3) | MED-UNNV, El Volador (location_id 10641) | 2018-11-05 → 2020-02-25 (después todo es -9999) | `tools/convertir_openaq.py` → `db/load_external.py` | "No especificada en OpenAQ (proveedor Medellín, entidad gubernamental); uso académico con atribución" |
| `IDEAM datos.gov.co` | Conjunto `g4t8-zkc3` "Calidad del Aire en Colombia" (API Socrata) | Estaciones del AMVA; v4 usa 29509, 29493 y 31854 | Horario 2020-01-01 → 2025-12-31 (tras la actualización del 2026-10-24) | `tools/importar_ideam.py` (API, en memoria) | CC BY-SA 4.0 |
| `IDEAM SISAIRE` | Reportes CSV horarios del SISAIRE, descargados a mano con cuenta | Tráfico Centro, Fiscalía Sector Caribe, Parque Biblioteca Fernando Botero, Parque Biblioteca Tomás Carrasquilla | 2025-01-01 → 2026-09-30 (se importa solo 2026: `--desde 2026-01-01`) | `tools/importar_sisaire.py` | "IDEAM SISAIRE – citar la fuente, sin alterar, sin uso comercial" (condiciones del IDEAM; **no** es CC) |

### Atribución (para el panel, informes y la feria)
- OpenAQ: "Datos de la estación MED-UNNV (Universidad Nacional, sede El Volador), proveedor Medellín,
  obtenidos de OpenAQ (openaq.org); no se redistribuyen".
- IDEAM datos.gov.co: "Fuente: IDEAM – Calidad del Aire en Colombia, datos.gov.co (CC BY-SA 4.0)".
  Los datos derivados que se publiquen deben ir con la misma licencia.
- IDEAM SISAIRE: citar al Instituto de Hidrología, Meteorología y Estudios Ambientales – IDEAM
  (SISAIRE) y la fecha de descarga; sin alterar los datos y sin uso comercial. Los archivos no se
  versionan ni se redistribuyen.
- Uso del proyecto: académico, no comercial.

### Qué fuente usar por periodo
- **2025**: el SISAIRE y datos.gov.co traen las mismas horas (ver "Convención de hora"). Para no
  duplicar, 2025 se toma de **datos.gov.co** y el SISAIRE se importa con `--desde 2026-01-01`.
- **2026**: solo el SISAIRE.
- Al armar un conjunto de entrenamiento, una sola fuente por estación y periodo.

## Convención de hora
- Las tres fuentes entregan horas **sin zona** (OpenAQ: con un desfase `-01:00` que se respeta).
  Supuesto del proyecto: son **hora de Colombia (UTC-5)**.
- Se guarda en `measured_at`, en **UTC**, el **fin de la hora**: el valor de la hora T es la media de
  la hora que termina en T (SISAIRE "22:00–22:59" → 23:00 local → 04:00 UTC del día siguiente).
- Evidencia:
  - IDEAM: el pico medio de PM2.5 cae a las 08:00 y el mínimo a las 15–16 h de la hora etiquetada
    (perfil de Medellín en hora local).
  - OpenAQ: con el desfase `-01:00` el pico queda a las 08:00 de Colombia.
  - SISAIRE vs datos.gov.co, 2025, misma convención: **100 %** de las horas comunes idénticas
    (Tráfico Centro 8.197 de 8.197; Fiscalía Caribe 8.068 de 8.068). Con ±1 h solo coincide el 5–9 %.

## Estación 31366 (Biblioteca Fernando B.) excluida de v4
- En datos.gov.co, de **2020-01 a 2023-12** sus valores son incoherentes: medianas mensuales de
  97 a 1.955 µg/m³ frente a 15–44 µg/m³ de Tráfico Centro en los mismos meses, y 12.517 valores
  > 1000 µg/m³. Los valores ≤ 1000 de ese periodo también son incoherentes, así que el filtro de
  rango no basta.
- Desde 2024-01 es normal (medianas de 11 a 27 µg/m³).
- Decisión: **no se usa** en el modelo v4, ni en entrenamiento ni en prueba (motivo en
  `modelos/prediccion_pm25_v4.json`, `estaciones_excluidas`). Sus filas no se borran.
- Es la misma estación que "Parque Biblioteca Fernando Botero" del SISAIRE; en 2026 sus valores son
  plausibles (medianas mensuales de 11 a 17 µg/m³).

## Limpieza (`tools/limpieza_pm25.py`, la usan los dos importadores del IDEAM)
En este orden:
1. Nulos, negativos y > 1000 µg/m³ (CHECK de la tabla): descartados.
2. **0 exacto**: descartado (en estas estaciones indica falla del equipo).
3. Duplicados (misma estación y hora): se conserva la primera.
4. **Rachas** de 6 o más horas seguidas con exactamente el mismo valor: se descarta la racha entera.

Además, cada importador descarta filas con formato inválido (duración distinta de 60 min, fecha
final distinta de inicial + 59 min, unidad distinta de µg/m³, fuera del periodo pedido).

Resultados:
- IDEAM datos.gov.co, 3 estaciones de v4 (2020–2025): 41 ceros y 0 rachas.
- SISAIRE 2026 (`--desde 2026-01-01`): 1.387 ceros, 0 rachas, 96 horas duplicadas entre archivos
  (el 2026-01-01); quedan 24.785 horas válidas.
- `tools/reporte_limpieza_external.py` (solo lectura) cuenta ceros y rachas por fuente y estación en
  la base, sin borrar nada.

## Aumento de ceros en 2026
Porcentaje de horas con 0 exacto por año (antes de la limpieza):

| Estación | IDEAM datos.gov.co 2020 → 2025 | SISAIRE 2025 | SISAIRE 2026 (ene–sep) |
|---|---|---|---|
| Tráfico Centro (29509) | 0,15 % · 0,01 % · 0,03 % · 0 % · 0 % · 0 % | 0,9 % | **7,4 %** |
| Fiscalía Sector Caribe (31854) | 0,03 % · 0 % · 0,01 % · 0 % · 0 % · 0 % | 4,2 % | **7,1 %** |
| Fernando Botero (31366) | 0,58 % · 0,40 % · 0,33 % · 0 % · 0 % · 0 % | 2,1 % | 5,5 % |
| Tomás Carrasquilla | — | 1,3 % | 1,1 % |

- Todos los ceros del SISAIRE de 2025 son horas que datos.gov.co **no publica** (75 de 75 en Tráfico
  Centro, 354 de 354 en Fiscalía Caribe): el IDEAM los trata como no válidos, y la limpieza hace lo
  mismo.
- En 2026, 1 de cada 14 horas de Tráfico Centro y Fiscalía Caribe es un cero: menos horas útiles y
  más huecos. No se rellenan.

## Cambio de formato del IDEAM (2026-10-24)
datos.gov.co actualizó `g4t8-zkc3` el 2026-10-24 (00:18 UTC): las fechas pasaron a ser texto
(`2024-01-01 00:00:00`), la duración de 2024 llega como `"60.0"` y se añadió 2025.
`tools/importar_ideam.py` está corregido (acepta `"60"` y `"60.0"`, filtra por texto y vuelve a
comprobar el rango de fechas). Una importación hecha **antes** de la corrección pudo perder todo 2024
e incluir filas de 31366 de 2020–2023: hay que comprobarlo con una consulta de solo lectura.

## Protecciones de los importadores
- `--dry-run`: lee y limpia; no se conecta a ninguna base.
- Sin `--local` exigen `DATABASE_URL` (si falta, se detienen); con `--local` usan solo `DB_*`
  (localhost:5433) aunque `DATABASE_URL` exista.
- Antes de escribir muestran la base de destino (sin usuario ni clave); los errores de conexión
  salen sin URL ni clave.
- Idempotentes: `ON CONFLICT (source, station, measured_at) DO NOTHING`.

## Replay histórico (`api/replay.py`)
- Estación del replay = estación física, con sus nombres en cada fuente: 29509 Tráfico Centro e
  31854 Fiscalía Caribe (datos.gov.co hasta 2025 + SISAIRE 2026), 29493 Casa de Justicia (solo
  datos.gov.co), 31366 Fernando Botero y 31946 Tomás Carrasquilla (solo SISAIRE 2026). Si una hora está
  en las dos fuentes manda datos.gov.co.
- Coordenadas del mapa (`api/mapa.py`), copiadas de datos.gov.co `g4t8-zkc3` (columnas `latitud` y
  `longitud`, una sola pareja por estación, consultado el 2026-10-24):

  | Estación | Latitud | Longitud | Contraste SIATA (`Estaciones.txt`) |
  |---|---|---|---|
  | 29509 Tráfico Centro | 6.252709 | -75.569656 | a menos de 25 m |
  | 31854 Fiscalía Sector Caribe | 6.268792 | -75.573653 | no aparece con ese nombre |
  | 29493 Casa de Justicia (Itagüí) | 6.185667 | -75.597206 | idéntica |
  | 31366 Fernando Botero | 6.277838 | -75.636363 | a unos 300 m |
  | 31946 Tomás Carrasquilla | 6.285101 | -75.583145 | a menos de 25 m |
- Vuelve a aplicar la limpieza al leer y necesita la ventana completa de v4 (6 horas previas + 18 de 24):
  con los ceros de 2026, en septiembre de 2026 Tráfico Centro no tiene ventana completa en 199 de 720
  horas de emisión.
