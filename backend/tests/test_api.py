import time

import pytest
from fastapi.testclient import TestClient

from app.api import jobs
from app.main import app
from app.pipeline import run as pipeline
from app.samples import make_test_pdf


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def pdf_bytes(tmp_path):
    path = tmp_path / "slides.pdf"
    make_test_pdf(path)
    return path.read_bytes()


def wait_until_finished(client, job_id):
    for _ in range(300):
        body = client.get(f"/api/jobs/{job_id}").json()
        if body["status"] in ("done", "error"):
            return body
        time.sleep(0.1)
    raise AssertionError("The job didn't finish in time.")


@pytest.fixture
def finished_job(client, pdf_bytes):
    response = client.post("/api/jobs", files={"pdf": ("slides.pdf", pdf_bytes)})
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    body = wait_until_finished(client, job_id)
    assert (body["status"], body["error"]) == ("done", None)
    return job_id


def test_results_are_served_after_a_job_finishes(client, finished_job):
    annotations = client.get(f"/api/jobs/{finished_job}/annotations").json()
    assert annotations["job_id"] == finished_job
    assert annotations["pages"]

    debug = client.get(f"/api/jobs/{finished_job}/debug").json()
    assert debug["windows"][0]["raw_page"] >= 1

    for name in ["pdf", "annotated.pdf"]:
        response = client.get(f"/api/jobs/{finished_job}/{name}")
        assert response.status_code == 200
        assert response.content.startswith(b"%PDF")


def test_statuses_go_through_each_step(client, pdf_bytes, monkeypatch):
    seen = []
    write_status = jobs._write_status

    def record(folder, status, error=None):
        seen.append(status)
        write_status(folder, status, error)

    monkeypatch.setattr(jobs, "_write_status", record)
    job_id = client.post("/api/jobs", files={"pdf": ("slides.pdf", pdf_bytes)}).json()["job_id"]
    wait_until_finished(client, job_id)
    assert list(dict.fromkeys(seen)) == [
        "queued",
        "extracting_pdf",
        "transcribing",
        "aligning",
        "writing_notes",
        "done",
    ]


def test_pipeline_failure_is_reported(client, pdf_bytes, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("Something broke")

    monkeypatch.setattr(jobs.pipeline, "run_pipeline", fail)
    job_id = client.post("/api/jobs", files={"pdf": ("slides.pdf", pdf_bytes)}).json()["job_id"]
    body = wait_until_finished(client, job_id)
    assert (body["status"], body["error"]) == ("error", "Something broke")


def test_audio_supports_range_requests_for_seeking(client, finished_job):
    audio = pipeline.job_dir(finished_job) / pipeline.AUDIO_NAME
    audio.write_bytes(bytes(range(100)))
    response = client.get(f"/api/jobs/{finished_job}/audio", headers={"Range": "bytes=10-19"})
    assert response.status_code == 206
    assert response.content == bytes(range(10, 20))
    assert response.headers["content-range"] == "bytes 10-19/100"


def test_job_without_audio_says_so(client, finished_job):
    assert client.get(f"/api/jobs/{finished_job}/audio").status_code == 404


def test_rejects_a_non_pdf(client):
    response = client.post("/api/jobs", files={"pdf": ("slides.txt", b"hello")})
    assert response.status_code == 400


def test_real_stt_requires_audio(client, pdf_bytes, monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "faster-whisper")
    response = client.post("/api/jobs", files={"pdf": ("slides.pdf", pdf_bytes)})
    assert response.status_code == 400


def test_unknown_job_is_404(client):
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.get("/api/jobs/nope/annotations").status_code == 404


def test_job_id_cannot_escape_the_jobs_folder(client, tmp_path):
    outside = pipeline.job_dir("x").parent.parent / "secret"
    outside.mkdir(parents=True)
    assert client.get("/api/jobs/..%2Fsecret").status_code == 404
    assert client.get("/api/jobs/..%2Fsecret/pdf").status_code == 404


def test_uploaded_filename_cannot_choose_the_path(client, pdf_bytes):
    response = client.post(
        "/api/jobs", files={"pdf": ("../../../evil.pdf", pdf_bytes)}
    )
    job_id = response.json()["job_id"]
    wait_until_finished(client, job_id)
    assert (pipeline.job_dir(job_id) / pipeline.PDF_NAME).exists()
    assert not (pipeline.job_dir(job_id).parents[2] / "evil.pdf").exists()


def test_after_a_restart_finished_and_interrupted_jobs_are_reported(client, finished_job):
    jobs._live_statuses.clear()
    assert client.get(f"/api/jobs/{finished_job}").json()["status"] == "done"

    jobs._write_status(pipeline.job_dir(finished_job), "transcribing")
    jobs._live_statuses.clear()
    body = client.get(f"/api/jobs/{finished_job}").json()
    assert body["status"] == "error"
    assert "서버가 다시 시작" in body["error"]


def test_cli_job_without_a_status_file_reports_done(client, pdf_bytes, tmp_path):
    path = tmp_path / "cli.pdf"
    path.write_bytes(pdf_bytes)
    job_id = pipeline.create_job(path, None)
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "queued"
    pipeline.run_pipeline(job_id)
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "done"
