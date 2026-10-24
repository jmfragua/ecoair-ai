"""Compatibilidad: la limpieza vive en shared/limpieza_pm25.py (la usan los importadores y la API)."""
import sys
from pathlib import Path

_RAIZ = Path(__file__).resolve().parent.parent
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from shared.limpieza_pm25 import MAX_PM25, MIN_RACHA_HORAS, UNA_HORA, imprimir_resumen, limpiar  # noqa: E402,F401
