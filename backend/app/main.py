from fastapi import FastAPI

app = FastAPI(title="LectureNote")


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
