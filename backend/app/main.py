from fastapi import FastAPI

from app.api.jobs import router as jobs_router

app = FastAPI(title="LectureNote")
app.include_router(jobs_router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
