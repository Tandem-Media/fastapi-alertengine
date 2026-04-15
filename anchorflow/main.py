# anchorflow/main.py
"""
AnchorFlow — FastAPI backend entry point.

Run:
    uvicorn anchorflow.main:app --reload
"""

from fastapi import FastAPI

from anchorflow.actions.router import router as actions_router
from anchorflow.observability import setup_observability

app = FastAPI(
    title="AnchorFlow",
    description="AnchorFlow backend with integrated observability.",
)

setup_observability(app)
app.include_router(actions_router)


@app.get("/health", tags=["health"])
async def health() -> dict:
    """Basic liveness probe."""
    return {"status": "up", "service": "anchorflow"}
