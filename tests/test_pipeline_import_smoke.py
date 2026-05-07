import sys
from pathlib import Path


def test_pipeline_import_smoke():
    orchestrator_dir = Path(__file__).resolve().parents[1] / "orchestrator"
    sys.path.insert(0, str(orchestrator_dir))
    try:
        from pipeline import (  # noqa: F401
            ALLOWED_TRANSITIONS,
            STAGE_GATES,
            STAGES,
            apply_transition,
            decide,
            decide_new_incident,
            open_incident,
            validate_decision_schema,
        )
    finally:
        sys.path = [p for p in sys.path if p != str(orchestrator_dir)]
