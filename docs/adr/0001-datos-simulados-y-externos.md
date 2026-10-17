# ADR 0001 · Uso de datos simulados y externos

- **Estado:** Propuesto (2026-10-16)
- **Contexto:** El nodo real tiene muy pocos datos (~6 h, sin episodios de contaminación). Para
  probar el software, hacer la demo de la feria y entrenar modelos experimentales usamos datos
  simulados, y podremos usar datos externos de estaciones de terceros.

## Decisión
1. **Separación física.**
   - Simulados: `measurements` con `is_simulated = true` y dispositivo `ecoair-sim-01`.
   - Externos: solo en `external_measurements` (con fuente, estación, licencia y fecha de carga).
   - Propios reales: `measurements` con `is_simulated = false`.
   - Nunca se mezclan en la misma tabla ni en la misma serie.
2. **Honestidad visible.** Todo dato simulado se muestra como SIMULACIÓN: banda y chip en el
   panel, "[SIMULACIÓN]" en el mensaje de las alertas y en el asunto y cuerpo de los correos.
   Nunca se presenta una lectura simulada como si fuera del sensor real.
3. **Herramientas seguras.** `tools/simular_estacion.py` se niega a escribir en un dispositivo
   con datos reales y solo reemplaza filas simuladas.
4. **Entrenamiento trazable.** Todo modelo guarda en sus metadatos de qué fuentes salieron sus
   datos (tipo, detalle, cantidad). Los entrenados con simulados lo dicen en su advertencia, y
   esa advertencia llega al panel y al correo.
5. **Evaluación separada.** Los datos externos y los simulados nunca se mezclan con los propios
   al evaluar. Las métricas se reportan por fuente y un resultado con simulados no se presenta
   como evidencia de precisión real.
6. **Licencias.** Los datos externos se cargan con su licencia y se citan donde se usen.

## Consecuencias
- (+) Se puede demostrar el sistema completo sin fingir lecturas reales.
- (+) Cualquier métrica se puede rastrear hasta sus datos.
- (−) Los modelos actuales (predicción y anomalías) solo valen como prueba de software hasta
  reentrenarlos con datos reales (2 a 4 semanas continuas del nodo).
- (−) Para la demo hay que activar explícitamente `ALERTER_INCLUIR_SIMULADOS` y
  `NOTIFIER_INCLUIR_SIMULADOS`.
