import sys
import pytest
from pathlib import Path

# Ensure orchestrator/ is always in sys.path so that its internal imports
# resolve correctly when orchestrator.main is imported as a package
# (e.g. `from orchestrator import main`).
_ORCHESTRATOR_DIR = str(Path(__file__).resolve().parents[1] / "orchestrator")


@pytest.fixture(autouse=True)
def _ensure_orchestrator_path():
    if _ORCHESTRATOR_DIR not in sys.path:
        sys.path.insert(0, _ORCHESTRATOR_DIR)
    yield
