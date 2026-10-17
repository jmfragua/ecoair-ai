# ADR 0002 · Dónde corren los modelos

- **Estado:** Propuesto (2026-10-16)
- **Contexto:** Hay dos modelos experimentales: la predicción de PM2.5 a 1 y 3 h y la detección
  de anomalías con Isolation Forest. El despliegue (Railway) cobra por memoria y por servicio, y
  `ai/models/` está ignorado por Git, así que los archivos de modelo no llegarían a la imagen.

## Decisión
1. **Artefactos versionados en `modelos/`.** Cada modelo va con su JSON de procedencia (fuentes,
   evaluación y límites; en el `.joblib`, también el SHA-256). Los Dockerfiles los copian a la
   imagen. Reentrenar = regenerar el archivo y hacer commit.
2. **Predicción: inferencia en Python puro, dentro de la API y del notifier.** El modelo es un
   JSON de 2,5 KB (coeficientes de una regresión Ridge). La fórmula está en
   `shared/prediccion.py`, sin numpy ni scikit-learn, así que la memoria añadida es despreciable.
   Endpoint `GET /devices/{id}/forecast` con caché de 60 s.
3. **Anomalías: dentro del alerter, como capa opcional.** Va con `ANOMALIAS_ACTIVAS=false` por
   defecto. scikit-learn solo se importa si se activa.
   - Memoria medida con `docker stats`: 54 MiB apagado y unos 118 MiB activo (+64 MiB).
   - Imagen: de 318 a 531 MB.
   - Un servicio aparte duplicaría Python, pandas y la conexión a la base, con un coste mayor.
   - Si el modelo falta o su hash no coincide, la capa se desactiva y las reglas siguen.
4. **El entrenamiento no corre en producción.** Se hace a mano en `ai/` (`entrenar_prediccion.py`,
   `exportar_modelo_anomalias.py`) y el resultado se versiona.
5. **Alcance según la procedencia.** Un modelo entrenado solo con simulados solo se aplica a
   dispositivos simulados (`solo_dispositivos_simulados` en su JSON).

## Consecuencias
- (+) Sin servicios nuevos y con poca memoria extra. Se despliega con el mismo `git push`.
- (+) Es reproducible: el modelo que corre es exactamente el evaluado (lo garantiza el hash).
- (−) El `.joblib` (2 MB) va en Git y depende de la versión de scikit-learn (fijada en 1.9.1).
- (−) Un modelo más pesado o un entrenamiento periódico obligaría a revisar esta decisión
  (por ejemplo, almacenamiento de artefactos fuera de Git o un servicio de inferencia aparte).
