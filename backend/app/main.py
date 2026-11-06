from fastapi import FastAPI

from app.api.jobs import router as jobs_router
from app.api.evaluation import router as evaluation_router

app = FastAPI(title="LectureNote")
app.include_router(jobs_router)
app.include_router(evaluation_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
