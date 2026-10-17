"""Recomendaciones por categoria del ICA: puente al modulo compartido shared/recomendaciones.py.

TEXTO FIJO DE LA NORMA, NO IA. La unica fuente de verdad es shared/recomendaciones.py, que usan el
dashboard (via la API), el correo de bienvenida, los correos de alerta y el alerter.
"""

import sys
from pathlib import Path

# shared/ esta en la raiz del repo en desarrollo; en Docker se copia junto al codigo (/app/shared).
_RAIZ = Path(__file__).resolve().parent.parent
if (_RAIZ / "shared").is_dir() and str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from shared.recomendaciones import (  # noqa: E402,F401
    ETIQUETA,
    NOMBRES,
    ORDEN,
    RECOMENDACIONES,
    fuentes_unicas,
    recomendacion,
)
