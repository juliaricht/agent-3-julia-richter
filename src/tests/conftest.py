"""pytest-Konfiguration: legt das src-Verzeichnis auf den Importpfad.

Dadurch funktioniert `import factory_model_de` unabhaengig davon, von wo
pytest gestartet wird. Zusaetzlich werden vor jedem Test die Modul-Puffer
geleert, damit die Tests voneinander isoliert sind.
"""

import os
import sys

import pytest

SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)


@pytest.fixture(autouse=True)
def _clean_buffers():
    """Leert die OPC-Puffer vor jedem Test (Isolation)."""
    import factory_model_de as fm
    fm.reset_opcua_buffers()
    yield
    fm.reset_opcua_buffers()
