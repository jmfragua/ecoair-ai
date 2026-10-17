"""Copia el modelo de anomalias evaluado a modelos/ (versionado) con su JSON de procedencia.

No reentrena ni cambia nada del modelo: copia el .joblib tal cual y resume su procedencia y su
evaluacion a partir del JSON que escribio ai/evaluar_prueba.py (metricas de codigo ejecutado).

  python ai/exportar_modelo_anomalias.py
"""

import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ORIGEN = RAIZ / "ai" / "models" / "sim_isolation_forest_v2_A"
DESTINO = RAIZ / "modelos" / "anomalias_sim_if_v2_A"

LIMITES = [
    "Entrenado SOLO con datos simulados (ecoair-sim-01, semilla 42): se aplica solo a la estacion "
    "simulada. Para el nodo real hay que reentrenar con datos reales antes de activarlo.",
    "En la evaluacion (semilla 43) NO mejoro a las reglas en la anomalia contextual de PM: con el "
    "umbral de contamination 0.005 detecto 0 de 4; con umbrales mas bajos las 'detectaba' por los "
    "bordes bruscos que inventa el simulador, no por el contexto.",
    "Falsas alarmas medidas en la evaluacion: entre 1 y 6 por dia segun el umbral (con datos simulados).",
    "Es experimental: sus alertas son kind='anomaly', severidad info, y no sustituyen a las reglas.",
]


def sha256(ruta):
    return hashlib.sha256(Path(ruta).read_bytes()).hexdigest()


def procedencia(meta, joblib_dest):
    ev = meta["evaluacion_prueba"]
    return {
        "nombre": DESTINO.name,
        "archivo": joblib_dest.name,
        "sha256": sha256(joblib_dest),
        "experimental": True,
        "exportado_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "modelo_original": meta["modelo"],
        "creado_utc": meta["creado_utc"],
        "algoritmo": meta["parametros"],
        "librerias": meta["librerias"],
        "features": meta["features"],
        "entrenado_con": [{"tipo": "simulados", "detalle": meta["entrenamiento"]["datos"],
                           "semilla_datos": meta["entrenamiento"]["semilla_datos"],
                           "etiquetas_usadas": meta["entrenamiento"]["etiquetas_usadas"]}],
        "solo_dispositivos_simulados": True,
        "evaluacion": {
            "conjunto": meta["prueba"]["datos"], "semilla_datos": meta["prueba"]["semilla_datos"],
            "nota": ev["nota"], "auc_por_tipo": ev["auc_por_tipo"], "pr_auc_todos": ev["pr_auc_todos"],
            "deteccion_por_tipo_contamination_0_005": ev["deteccion_por_tipo"],
            "falsas_alarmas_filas_por_dia": ev["falsas_alarmas_filas_por_dia"],
            "curva_operacion": meta.get("curva_operacion_prueba", {}).get("puntos", []),
        },
        "advertencia": meta["advertencia"],
        "limites": LIMITES,
    }


def main():
    meta = json.loads(ORIGEN.with_suffix(".json").read_text(encoding="utf-8"))
    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    joblib_dest = DESTINO.with_suffix(".joblib")
    shutil.copyfile(ORIGEN.with_suffix(".joblib"), joblib_dest)
    p = procedencia(meta, joblib_dest)
    DESTINO.with_suffix(".json").write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Copiado {joblib_dest.relative_to(RAIZ)} ({joblib_dest.stat().st_size / 1e6:.2f} MB, sha256 {p['sha256'][:12]}...)")
    print(f"Procedencia en {DESTINO.with_suffix('.json').relative_to(RAIZ)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
