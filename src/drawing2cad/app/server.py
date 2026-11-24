"""Local HTTP API + static web UI of the desktop app (FastAPI)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from drawing2cad import __version__
from drawing2cad.app.jobs import (
    MODE_AI,
    MODE_LABELS,
    AppConfig,
    AppError,
    JobManager,
    default_max_cost,
)

STATIC_DIR = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
# Mutating requests must carry this header. Browsers cannot add a custom header to a
# cross-origin request without a CORS preflight (which this server never allows), so other
# web pages cannot start runs (especially paid ones) on the local server.
CSRF_HEADER = "x-drawing2cad"
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}


def _job_report(job) -> Path:
    """The job's HTML report, only from inside the job's own result folder."""
    p = (job.result or {}).get("html_report")
    path = Path(p).resolve() if p else None
    if (path is None or not path.is_file() or path.suffix.lower() != ".html"
            or not path.is_relative_to(job.out_dir.resolve())):
        raise AppError("Brak raportu HTML.", 404)
    return path


class JobRequest(BaseModel):
    pdf_id: str
    mode: str
    max_attempts: int = Field(default=3, ge=1, le=5)
    parts: list[str] | None = None
    max_cost: float | None = None
    confirm_cost: bool = False
    accepted_max_cost: float | None = None


class EstimateRequest(BaseModel):
    pdf_id: str
    max_cost: float | None = None
    max_attempts: int = Field(default=3, ge=1, le=5)
    parts: list[str] | None = None


class DecisionRequest(BaseModel):
    part: int
    ambiguity_id: str | None = None
    ambiguity_key: str | None = None  # unique within the part (ids may repeat); preferred over the id
    decision: str | None  # "potwierdzone" | "odrzucone" | None (withdraw)
    comment: str = Field(default="", max_length=2000)


class OpenRequest(BaseModel):
    what: str  # step | report | folder
    part: int | None = None


def _open_path(path: Path, select: bool = False) -> None:
    """Open a file/folder with the system's default application (local desktop app only)."""
    if sys.platform == "win32":
        if select:
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)] if select else ["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent if select else path)])


def create_app(cfg: AppConfig, opener=_open_path, allowed_hosts=None) -> FastAPI:
    allowed = set(allowed_hosts or ALLOWED_HOSTS)
    manager = JobManager(cfg)
    app = FastAPI(title="drawing2cad", version=__version__, docs_url=None, redoc_url=None)
    app.state.manager = manager

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
        if host not in allowed:
            return JSONResponse({"detail": "Niedozwolony host."}, status_code=403)
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CSRF_HEADER) != "1":
            return JSONResponse({"detail": "Brak nagłówka aplikacji."}, status_code=403)
        return await call_next(request)

    @app.exception_handler(AppError)
    async def app_error(_request: Request, exc: AppError):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-store"})

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/api/info")
    def info():
        running = manager.running_job()
        return {
            "version": __version__,
            "samples": manager.samples(),
            "modes": [
                {"id": "demo", "label": MODE_LABELS["demo"], "available": cfg.demo_cache_dir is not None},
                {"id": "manual", "label": MODE_LABELS["manual"], "available": cfg.manual_dir is not None},
                {"id": MODE_AI, "label": MODE_LABELS[MODE_AI], "available": True},
            ],
            "default_max_cost": default_max_cost(),
            "running": running.id if running else None,
            "work_dir": str(cfg.work_dir),
        }

    @app.post("/api/upload")
    async def upload(file: UploadFile = File(...)):
        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise AppError("Plik jest za duży (limit 100 MB).", 413)
        return manager.save_upload(file.filename or "rysunek.pdf", data).to_dict()

    @app.post("/api/samples/{name}")
    def sample(name: str):
        return manager.register_sample(name).to_dict()

    @app.post("/api/estimate")
    def estimate(req: EstimateRequest):
        return manager.estimate(req.pdf_id, req.max_cost, req.max_attempts, req.parts)

    @app.post("/api/jobs")
    def start_job(req: JobRequest):
        job = manager.start(
            pdf_id=req.pdf_id,
            mode=req.mode,
            max_attempts=req.max_attempts,
            parts=req.parts,
            max_cost=req.max_cost,
            confirm_cost=req.confirm_cost,
            accepted_max_cost=req.accepted_max_cost,
        )
        return job.to_dict()

    @app.get("/api/jobs")
    def list_jobs():
        jobs = sorted(manager.jobs.values(), key=lambda j: j.created, reverse=True)
        return [manager.refresh(j).to_dict(with_result=False) | {"log": []} for j in jobs]

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        return manager.snapshot(manager.refresh(manager.get_job(job_id)))

    @app.post("/api/jobs/{job_id}/decisions")
    def decide(job_id: str, req: DecisionRequest):
        job = manager.decide(job_id, req.part, req.ambiguity_id, req.decision, req.comment, amb_key=req.ambiguity_key)
        return manager.snapshot(job)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        return manager.cancel(job_id).to_dict(with_result=False)

    @app.get("/api/jobs/{job_id}/report")
    def html_report(job_id: str):
        return FileResponse(_job_report(manager.get_job(job_id)), media_type="text/html")

    @app.get("/api/jobs/{job_id}/parts/{index}/image")
    def part_image(job_id: str, index: int):
        return FileResponse(manager.report_file(job_id, index, "image"), media_type="image/png")

    @app.get("/api/jobs/{job_id}/parts/{index}/model.stl")
    def part_stl(job_id: str, index: int):
        return FileResponse(manager.report_file(job_id, index, "stl"), media_type="model/stl")

    @app.get("/api/jobs/{job_id}/parts/{index}/edges.json")
    def part_edges(job_id: str, index: int):
        return FileResponse(manager.report_file(job_id, index, "edges"), media_type="application/json")

    @app.get("/api/jobs/{job_id}/parts/{index}/model.step")
    def part_step(job_id: str, index: int):
        p = manager.report_file(job_id, index, "step")
        return FileResponse(p, media_type="application/step", filename=p.name)

    @app.post("/api/jobs/{job_id}/open")
    def open_file(job_id: str, req: OpenRequest):
        job = manager.get_job(job_id)
        if req.what == "folder":
            target, select = job.out_dir, False
        elif req.what == "report":
            target, select = _job_report(job), False
        elif req.what in ("step", "step_folder"):
            if req.part is None:
                raise AppError("Nie wskazano części.")
            target, select = manager.report_file(job_id, req.part, "step"), req.what == "step_folder"
        else:
            raise AppError("Nieznane polecenie.")
        try:
            opener(target, select)
        except OSError as exc:
            raise AppError(f"Nie udało się otworzyć: {exc}", 500) from exc
        return {"opened": str(target)}

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        return Response(status_code=204)

    return app
