"""Background pipeline runs for the desktop app.

One run at a time in a worker thread. Progress comes from a thin wrapper around the
interpreter (the pipeline itself is not modified): every `interpret()` call marks which
part and attempt is being processed. The wrapper also implements cancellation by raising
FatalInterpreterError, which the repair loop never retries.
"""
from __future__ import annotations

import copy
import json
import math
import os
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from drawing2cad.app.mesh import export_preview
from drawing2cad.confirm import (
    BLOCKING,
    CONFIRMED,
    REJECTED,
    DecisionStore,
    apply_decisions,
    decision_keys,
    default_store_path,
)
from drawing2cad.interpreter import (
    DEFAULT_MAX_COST_USD,
    DEFAULT_MAX_TOKENS,
    PROVIDERS,
    FatalInterpreterError,
    LLMInterpreter,
    ManualSpecInterpreter,
)
from drawing2cad.pdf_extract import extract_pdf
from drawing2cad.pipeline import process_pdf
from drawing2cad.report import write_html_report
from drawing2cad.schema import ExtractedDocument, PartRegion, PartSpec, VerificationReport

MODE_DEMO = "demo"
MODE_MANUAL = "manual"
MODE_AI = "ai"
MODES = (MODE_DEMO, MODE_MANUAL, MODE_AI)
MODE_LABELS = {
    MODE_DEMO: "Demo offline (zapisane odpowiedzi AI)",
    MODE_MANUAL: "Ręczne specyfikacje (test klocków)",
    MODE_AI: "Prawdziwy przebieg z AI",
}
CANCELLED_MSG = "Przerwano przez użytkownika (pozostałe części nie były wysyłane do AI)."


class AppError(Exception):
    """Error with a user-facing (Polish) message and an HTTP status code."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def find_repo_root() -> Path | None:
    """Repository root of an editable (src layout) install; None for a plain wheel install."""
    root = Path(__file__).resolve().parents[3]
    return root if (root / "tests" / "fixtures").is_dir() else None


@dataclass
class AppConfig:
    work_dir: Path  # uploads/ and per-run result folders
    repo_root: Path | None = field(default_factory=find_repo_root)
    ai_cache_dir: Path = field(default_factory=lambda: Path("cache").resolve())
    # None: decisions next to the PDF (<rysunek>.decyzje.json), the same file as CLI --pytaj
    decisions_dir: Path | None = None

    def decisions_path(self, pdf_path: Path) -> Path:
        if self.decisions_dir is None:
            return default_store_path(pdf_path)
        return self.decisions_dir / default_store_path(pdf_path).name

    @property
    def demo_cache_dir(self) -> Path | None:
        if self.repo_root is None:
            return None
        d = self.repo_root / "tests" / "fixtures" / "deepseek" / "odpowiedzi"
        return d if d.is_dir() else None

    @property
    def manual_dir(self) -> Path | None:
        if self.repo_root is None:
            return None
        d = self.repo_root / "tests" / "fixtures" / "reczne"
        return d if d.is_dir() else None

    @property
    def samples_dir(self) -> Path | None:
        if self.repo_root is None:
            return None
        d = self.repo_root / "rysunki"
        return d if d.is_dir() else None


def make_interpreter(mode: str, cfg: AppConfig, max_cost: float | None = None):
    """Build the interpreter for a mode. Only MODE_AI can ever call a paid API."""
    if mode == MODE_DEMO:
        if cfg.demo_cache_dir is None:
            raise AppError("Brak zapisanych odpowiedzi AI (tests/fixtures/deepseek/odpowiedzi).")
        ds = PROVIDERS["deepseek"]
        # Fixed provider/model/limits: the stored answers are keyed by them, independent of .env.
        interp = LLMInterpreter(
            provider="deepseek",
            model=ds["model"],
            base_url=ds["base_url"],
            vision=True,
            cache_dir=cfg.demo_cache_dir,
            offline_only=True,
        )
        interp.max_tokens = DEFAULT_MAX_TOKENS
        return interp
    if mode == MODE_MANUAL:
        if cfg.manual_dir is None:
            raise AppError("Brak katalogu ręcznych specyfikacji (tests/fixtures/reczne).")
        return ManualSpecInterpreter(cfg.manual_dir)
    if mode == MODE_AI:
        try:
            return LLMInterpreter(cache_dir=cfg.ai_cache_dir, offline_only=False, max_cost=max_cost)
        except ValueError as exc:
            raise AppError(f"Błąd konfiguracji AI: {exc}") from exc
    raise AppError(f"Nieznany tryb: {mode}")


def cost_info(interpreter: Any) -> dict:
    costs = getattr(interpreter, "costs", None)
    if costs is None:
        return {
            "calls": 0,
            "usd": 0.0,
            "max_cost": None,
            "summary": "Koszt AI: 0 wywołań API (ręczne specyfikacje, bez AI).",
        }
    return {
        "calls": len(costs.calls),
        "usd": round(costs.total_usd, 5),
        "max_cost": costs.max_cost,
        "summary": costs.summary(),
    }


def estimate_ai_cost(interpreter: LLMInterpreter, n_parts: int, max_attempts: int) -> dict:
    """Worst-case cost of a paid run, computed exactly like the CLI's confirmation prompt."""
    est = interpreter.costs.estimate_call()
    max_cost = interpreter.costs.max_cost
    worst = min(n_parts * max_attempts * est, max_cost if max_cost is not None else math.inf)
    return {
        "provider": interpreter.provider,
        "model": interpreter.model,
        "n_parts": n_parts,
        "max_attempts": max_attempts,
        "per_call_usd": round(est, 4),
        "worst_usd": round(worst, 4),
        "max_cost": max_cost,
        "key_configured": bool(os.environ.get(interpreter.key_env, "").strip()),
        "key_env": interpreter.key_env,
        "cache_dir": str(interpreter.cache.cache_dir) if interpreter.cache else None,
    }


# ---------------------------------------------------------------------------
# PDF registry (uploaded files and bundled sample drawings)
# ---------------------------------------------------------------------------


@dataclass
class PdfEntry:
    id: str
    name: str
    path: Path
    size: int
    parts: list[dict]
    is_assembly: bool
    bom: list[dict]
    title: str | None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "size": self.size,
            "parts": self.parts,
            "is_assembly": self.is_assembly,
            "bom": self.bom,
            "title": self.title,
        }


def describe_pdf(path: Path, name: str | None = None) -> PdfEntry:
    try:
        doc = extract_pdf(path)
    except Exception as exc:  # corrupted file, not a PDF, ...
        raise AppError(f"Nie udało się odczytać PDF: {exc}") from exc
    tb = doc.assembly_title_block
    return PdfEntry(
        id=uuid.uuid4().hex[:12],
        name=name or path.name,
        path=path,
        size=path.stat().st_size,
        parts=[{"drawing_no": p.drawing_no, "name": p.name, "n_dims": len(p.dimensions)} for p in doc.parts],
        is_assembly=doc.is_assembly,
        bom=[b.model_dump() for b in doc.bom],
        title=(tb.nazwa if tb else None) or (doc.parts[0].title_block.nazwa if doc.parts and doc.parts[0].title_block else None),
    )


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


@dataclass
class Job:
    id: str
    mode: str
    pdf_name: str
    pdf_path: Path
    out_dir: Path
    max_attempts: int
    part_filter: list[str] | None
    parts: list[dict]
    created: float = field(default_factory=time.time)
    status: str = "queued"  # queued | running | done | error | cancelled
    phase: str = "W kolejce"
    current: int | None = None
    attempt: int = 0
    started: float | None = None
    finished: float | None = None
    error: str | None = None
    log: list[dict] = field(default_factory=list)
    result: dict | None = None
    costs: dict | None = None
    cancel_requested: bool = False
    interpreter: Any = None
    decisions_path: Path | None = None  # user decisions on assumptions (shared with CLI --pytaj)

    def add_log(self, text: str, level: str = "info") -> None:
        self.log.append({"t": round(time.time() - (self.started or self.created), 1), "text": text, "level": level})

    def to_dict(self, with_result: bool = True) -> dict:
        costs = cost_info(self.interpreter) if self.interpreter is not None else self.costs
        d = {
            "id": self.id,
            "mode": self.mode,
            "mode_label": MODE_LABELS.get(self.mode, self.mode),
            "pdf_name": self.pdf_name,
            "out_dir": str(self.out_dir),
            "max_attempts": self.max_attempts,
            "status": self.status,
            "phase": self.phase,
            "current": self.current,
            "attempt": self.attempt,
            "parts": self.parts,
            "created": self.created,
            "started": self.started,
            "finished": self.finished,
            "elapsed": round((self.finished or time.time()) - self.started, 1) if self.started else 0.0,
            "error": self.error,
            "cancel_requested": self.cancel_requested,
            "log": self.log[-200:],
            "costs": costs,
            "counts": _status_counts(self.result),
            "decisions_path": str(self.decisions_path) if self.decisions_path else None,
        }
        if with_result:
            d["result"] = self.result
        return d


def _status_counts(result: dict | None) -> dict:
    counts = {"OK": 0, "NIEPEWNE": 0, "BŁĄD": 0}
    for r in (result or {}).get("reports", []):
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return counts


class _ProgressInterpreter:
    """Delegates to the real interpreter and reports progress to the job."""

    def __init__(self, inner: Any, job: Job, lock: threading.Lock) -> None:
        self.inner = inner
        self.job = job
        self.lock = lock

    @property
    def costs(self):  # pragma: no cover - passthrough
        return getattr(self.inner, "costs", None)

    def interpret(self, part: PartRegion, feedback_errors: list[str] | None = None, attempt: int = 1) -> PartSpec:
        job = self.job
        label = f"{part.drawing_no or '?'} {part.name or ''}".strip()
        with self.lock:
            idx = next((i for i, p in enumerate(job.parts) if p["drawing_no"] == part.drawing_no), None)
            if idx is not None and idx != job.current:
                if job.current is not None:
                    job.parts[job.current]["state"] = "done"
                job.current = idx
                job.parts[idx]["state"] = "running"
            job.attempt = attempt
            if idx is not None:
                job.parts[idx]["attempt"] = attempt
            if job.cancel_requested:
                job.phase = "Przerywanie"
                raise FatalInterpreterError(CANCELLED_MSG)
            job.phase = "Interpretacja AI" if job.mode != MODE_MANUAL else "Odczyt specyfikacji"
            if attempt > 1:
                job.add_log(f"{label}: próba {attempt} (poprawki po weryfikacji)", "warn")
            else:
                job.add_log(f"{label}: interpretacja rysunku")
        spec = self.inner.interpret(part, feedback_errors=feedback_errors, attempt=attempt)
        with self.lock:
            job.phase = "Budowa bryły i weryfikacja z rysunkiem"
        return spec


def _report_to_dict(report, job_dir: Path) -> dict:
    d = report.model_dump()
    d["has_step"] = bool(report.step_path and Path(report.step_path).is_file())
    d["has_image"] = bool(report.image_path and Path(report.image_path).is_file())
    d["rejected_model"] = bool(report.step_path and Path(report.step_path).name.startswith("BLEDNY_"))
    d["step_name"] = Path(report.step_path).name if report.step_path else None
    d["n_checks_ok"] = sum(1 for c in report.checks if c.status == "OK")
    d["n_checks_warn"] = sum(1 for c in report.checks if c.status == "NIEPEWNE")
    d["n_views_ok"] = sum(1 for v in report.view_checks if v.ok)
    return d


def _with_decisions(report: VerificationReport, raw: VerificationReport, store: DecisionStore | None) -> dict:
    """Report for the UI: status after user decisions + the original ambiguities with their decision."""
    d = _report_to_dict(report, Path("."))
    resolved = store.resolve(raw) if store is not None else [(k, None, False) for k in decision_keys(raw)]
    d["ambiguities_raw"] = [
        {
            **a.model_dump(),
            "key": key,  # unique within the part: two entries may share an id
            "blocking": a.severity in BLOCKING,
            "decision": dec if a.severity in BLOCKING else None,
            # an old decision shared by identical copies of this entry: not applied, decide again
            "decision_ambiguous": ambiguous and a.severity in BLOCKING,
        }
        for a, (key, dec, ambiguous) in zip(raw.ambiguities, resolved)
    ]
    return d


class JobManager:
    def __init__(self, cfg: AppConfig) -> None:
        self.cfg = cfg
        self.jobs: dict[str, Job] = {}
        self.pdfs: dict[str, PdfEntry] = {}
        self.lock = threading.Lock()
        self._write_lock = threading.Lock()  # report / history files; taken before self.lock, never inside it
        self._thread: threading.Thread | None = None
        (cfg.work_dir / "przebiegi").mkdir(parents=True, exist_ok=True)
        self._load_history()

    # ----- PDFs -----------------------------------------------------------
    def register_pdf(self, path: Path, name: str | None = None) -> PdfEntry:
        entry = describe_pdf(path, name)
        with self.lock:
            self.pdfs[entry.id] = entry
        return entry

    def save_upload(self, filename: str, data: bytes) -> PdfEntry:
        if not data.startswith(b"%PDF"):
            raise AppError("To nie jest plik PDF.")
        safe = "".join(c if c.isalnum() or c in "._- " else "_" for c in Path(filename).name).strip() or "rysunek.pdf"
        if not safe.lower().endswith(".pdf"):
            safe += ".pdf"
        d = self.cfg.work_dir / "wgrane" / uuid.uuid4().hex[:12]
        d.mkdir(parents=True, exist_ok=True)
        path = d / safe
        path.write_bytes(data)
        return self.register_pdf(path, safe)

    def samples(self) -> list[dict]:
        d = self.cfg.samples_dir
        if d is None:
            return []
        return [{"name": p.name, "size": p.stat().st_size} for p in sorted(d.glob("*.pdf"))]

    def register_sample(self, name: str) -> PdfEntry:
        d = self.cfg.samples_dir
        path = (d / name) if d else None
        if path is None or path.parent != d or not path.is_file():
            raise AppError("Nie ma takiego przykładowego rysunku.", 404)
        return self.register_pdf(path, name)

    def get_pdf(self, pdf_id: str) -> PdfEntry:
        entry = self.pdfs.get(pdf_id)
        if entry is None:
            raise AppError("Nieznany plik PDF: wybierz go ponownie.", 404)
        return entry

    # ----- jobs -----------------------------------------------------------
    def running_job(self) -> Job | None:
        return next((j for j in self.jobs.values() if j.status in ("queued", "running")), None)

    def estimate(self, pdf_id: str, max_cost: float | None, max_attempts: int, parts: list[str] | None) -> dict:
        entry = self.get_pdf(pdf_id)
        interp = make_interpreter(MODE_AI, self.cfg, max_cost=max_cost)
        n = len(self._selected_parts(entry, parts))
        return estimate_ai_cost(interp, n, max_attempts)

    @staticmethod
    def _selected_parts(entry: PdfEntry, parts: list[str] | None) -> list[dict]:
        return [p for p in entry.parts if not parts or p["drawing_no"] in parts]

    def start(
        self,
        pdf_id: str,
        mode: str,
        max_attempts: int = 3,
        parts: list[str] | None = None,
        max_cost: float | None = None,
        confirm_cost: bool = False,
        accepted_max_cost: float | None = None,
    ) -> Job:
        if mode not in MODES:
            raise AppError(f"Nieznany tryb: {mode}")
        if not 1 <= max_attempts <= 5:
            raise AppError("Liczba prób musi być od 1 do 5.")
        entry = self.get_pdf(pdf_id)
        if mode == MODE_AI:
            # A paid run needs an explicit confirmation of the limit that was shown to the user.
            if not confirm_cost:
                raise AppError("Prawdziwy przebieg z AI wymaga potwierdzenia kosztu.", 403)
            if max_cost is None or max_cost <= 0:
                raise AppError("Podaj limit kosztu (USD) większy od zera.")
            if accepted_max_cost is None or abs(accepted_max_cost - max_cost) > 1e-9:
                raise AppError("Potwierdzony limit kosztu nie zgadza się z limitem przebiegu.", 403)
        interpreter = make_interpreter(mode, self.cfg, max_cost=max_cost)
        selected = self._selected_parts(entry, parts)
        if parts and not selected:
            raise AppError("Nie wybrano żadnej części.")
        with self.lock:
            if self.running_job() is not None:
                raise AppError("Inny przebieg jeszcze trwa.", 409)
            job_id = time.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:4]
            job = Job(
                id=job_id,
                mode=mode,
                pdf_name=entry.name,
                pdf_path=entry.path,
                out_dir=self.cfg.work_dir / "przebiegi" / job_id,
                max_attempts=max_attempts,
                part_filter=[p["drawing_no"] for p in selected] if parts else None,
                parts=[{**p, "state": "pending", "attempt": 0} for p in selected],
                interpreter=interpreter,
                decisions_path=self.cfg.decisions_path(entry.path),
            )
            self.jobs[job.id] = job
            self._thread = threading.Thread(target=self._run, args=(job,), name=f"job-{job.id}", daemon=True)
            self._thread.start()
        return job

    def cancel(self, job_id: str) -> Job:
        job = self.get_job(job_id)
        with self.lock:
            if job.status in ("queued", "running"):
                job.cancel_requested = True
                job.add_log("Zażądano przerwania: bieżąca część zostanie dokończona.", "warn")
        return job

    def get_job(self, job_id: str) -> Job:
        job = self.jobs.get(job_id)
        if job is None:
            raise AppError("Nie ma takiego przebiegu.", 404)
        return job

    def wait(self, timeout: float | None = None) -> None:
        """Wait for the current worker thread (used by tests)."""
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self, job: Job) -> None:
        with self.lock:
            job.status = "running"
            job.started = time.time()
            job.phase = "Ekstrakcja wektorowa PDF"
            job.add_log(f"Start: {job.pdf_name}, tryb: {MODE_LABELS[job.mode]}")
        wrapped = _ProgressInterpreter(job.interpreter, job, self.lock)
        try:
            result = process_pdf(
                pdf_path=job.pdf_path,
                out_dir=job.out_dir,
                interpreter=wrapped,
                max_attempts=job.max_attempts,
                part_filter=set(job.part_filter) if job.part_filter else None,
                generate_html=True,
            )
            with self.lock:
                if job.current is not None:
                    job.parts[job.current]["state"] = "done"
                job.phase = "Przygotowanie podglądu 3D"
            for report in result.reports:
                if report.step_path and Path(report.step_path).is_file():
                    try:
                        export_preview(report.step_path, job.out_dir / "podglad3d")
                    except Exception as exc:  # preview only, never fails the run
                        job.add_log(f"{report.drawing_no}: brak podglądu 3D ({exc})", "warn")
            doc = result.document
            job_result = {
                "raw_reports": [r.model_dump() for r in result.reports],
                "reports": [],
                "is_assembly": doc.is_assembly,
                "bom": [b.model_dump() for b in doc.bom],
                "assembly_title": doc.assembly_title_block.model_dump() if doc.assembly_title_block else None,
                "html_report": result.html_report_path,
                "json_report": result.json_report_path,
            }
            with self.lock:
                job.result = job_result
                self._refresh_reports(job)
            self._write_reports(job, doc=doc)
            with self.lock:
                job.costs = cost_info(job.interpreter)
                job.status = "cancelled" if job.cancel_requested else "done"
                job.phase = "Przerwano" if job.cancel_requested else "Gotowe"
                c = _status_counts(job_result)
                job.add_log(
                    f"Koniec: OK {c['OK']}, NIEPEWNE {c['NIEPEWNE']}, BŁĄD {c['BŁĄD']}. {job.costs['summary']}",
                    "done",
                )
        except Exception as exc:
            with self.lock:
                job.status = "error"
                job.phase = "Błąd"
                job.error = f"{type(exc).__name__}: {exc}"
                job.costs = cost_info(job.interpreter)
                job.add_log(f"Błąd przetwarzania: {exc}", "error")
                job.add_log(traceback.format_exc(limit=6), "error")
        finally:
            with self.lock:
                job.finished = time.time()
                job.current = None
                job.costs = cost_info(job.interpreter)
                job.interpreter = None
            self._save_job(job)

    # ----- user decisions on assumptions (rule 6, same store as CLI --pytaj) -----
    def _refresh_reports(self, job: Job) -> tuple[list[VerificationReport], bool] | None:
        """Recompute the shown reports from the raw ones and the stored decisions (call under
        self.lock). Returns (reports after decisions, whether a decision store exists with any
        decisions), or None without a result.

        Like the CLI, decisions are applied only when the store has any. A run saved before the
        raw reports were kept gets them now, before its reports are replaced by the ones with
        decisions applied (otherwise the next refresh would take those as raw)."""
        res = job.result
        if not res:
            return None
        if not res.get("raw_reports"):
            res["raw_reports"] = [VerificationReport.model_validate(r).model_dump() for r in res.get("reports", [])]
        raws = [VerificationReport.model_validate(r) for r in res["raw_reports"]]
        store = None
        if job.decisions_path is not None:
            try:
                store = DecisionStore(job.decisions_path)
            except (OSError, ValueError) as exc:
                job.add_log(f"Nie można odczytać decyzji {job.decisions_path}: {exc}", "warn")
        has_decisions = store is not None and bool(store.data)
        applied = [apply_decisions(r, store) for r in raws] if has_decisions else raws
        res["reports"] = [_with_decisions(a, r, store) for a, r in zip(applied, raws)]
        return applied, has_decisions

    def _write_reports(self, job: Job, doc: ExtractedDocument | None = None, force: bool = False) -> None:
        """Write the run's raport.json and raport.html again with the current decisions, when there
        are any (or always with `force`, e.g. after the last decision was withdrawn).

        The state is taken under self.lock, the slow part (PDF extraction, HTML) runs outside it;
        writers are serialized and each takes the newest state, so the last file is up to date."""
        with self._write_lock:
            with self.lock:
                if job.decisions_path is None:
                    return
                refreshed = self._refresh_reports(job)
                if refreshed is None:
                    return
                applied, has_decisions = refreshed
                payload = json.dumps({
                    "source_pdf": str(job.pdf_path),
                    "bom": job.result.get("bom", []),
                    "reports": [a.model_dump() for a in applied],
                }, ensure_ascii=False, indent=2)
            if not (has_decisions or force):
                return
            (job.out_dir / "raport.json").write_text(payload, encoding="utf-8")
            if doc is None and job.pdf_path.is_file():
                doc = extract_pdf(job.pdf_path)
            if doc is not None:
                write_html_report(doc, applied, job.out_dir / "raport.html")

    def refresh(self, job: Job) -> Job:
        """Re-read decisions (they may have changed in another run or in the CLI)."""
        with self.lock:
            if job.status in ("done", "cancelled") and job.result:
                self._refresh_reports(job)
        return job

    def snapshot(self, job: Job, with_result: bool = True) -> dict:
        """The job as a dict, copied under the lock (never serialized while another thread changes it)."""
        with self.lock:
            return copy.deepcopy(job.to_dict(with_result=with_result))

    def decide(
        self, job_id: str, index: int, amb_id: str | None, decision: str | None, comment: str = "",
        amb_key: str | None = None,
    ) -> Job:
        """Confirm / reject (or clear the decision on) one blocking assumption of a part.

        The entry is chosen by its `key` (unique within the part); a bare id is accepted only
        when exactly one blocking entry of the part has it."""
        if decision not in (CONFIRMED, REJECTED, None):
            raise AppError("Nieznana decyzja.")
        if not amb_key and not amb_id:
            raise AppError("Nie wskazano zastrzeżenia (ambiguity_key lub ambiguity_id).")
        job = self.get_job(job_id)
        with self.lock:  # the whole read-modify-write of the decision; slow writes come after
            if job.status not in ("done", "cancelled") or not job.result:
                raise AppError("Decyzje można podejmować po zakończeniu przebiegu.", 409)
            if job.decisions_path is None:
                raise AppError("Brak pliku decyzji dla tego przebiegu.")
            self._refresh_reports(job)  # makes sure the run has its raw reports
            raws = job.result["raw_reports"]
            if not 0 <= index < len(raws):
                raise AppError("Nie ma takiej części.", 404)
            raw = VerificationReport.model_validate(raws[index])
            blocking = [(a, k) for a, k in zip(raw.ambiguities, decision_keys(raw)) if a.severity in BLOCKING]
            if amb_key:
                found = [(a, k) for a, k in blocking if k == amb_key]
            else:
                found = [(a, k) for a, k in blocking if a.id == amb_id]
                if len(found) > 1:
                    raise AppError(f"W tej części jest {len(found)} zastrzeżeń o identyfikatorze {amb_id}; "
                                   "wskaż, którego dotyczy decyzja (ambiguity_key).", 409)
            if not found:
                raise AppError("Nie ma takiego zastrzeżenia do potwierdzenia.", 404)
            amb, key = found[0]
            try:
                store = DecisionStore(job.decisions_path)
            except (OSError, ValueError) as exc:
                raise AppError(f"Nie można odczytać pliku decyzji: {exc}", 500) from exc
            store.set_key(raw, key, decision, comment.strip())
            store.save()
            label = {CONFIRMED: "potwierdzone", REJECTED: "odrzucone", None: "decyzja cofnięta"}[decision]
            job.add_log(f"{raw.drawing_no} {raw.name}: założenie {amb.id} {label}.",
                        "warn" if decision == REJECTED else "done")
            self._refresh_reports(job)
        self._write_reports(job, force=True)
        self._save_job(job)
        return job

    # ----- history ----------------------------------------------------------
    def _save_job(self, job: Job) -> None:
        """Write przebieg.json; the state is taken under self.lock, writers are serialized."""
        with self._write_lock:
            with self.lock:
                payload = job.to_dict()
                payload["pdf_path"] = str(job.pdf_path)
                text = json.dumps(payload, ensure_ascii=False, indent=1)
            try:
                job.out_dir.mkdir(parents=True, exist_ok=True)
                (job.out_dir / "przebieg.json").write_text(text, encoding="utf-8")
            except OSError:
                pass

    def _load_history(self) -> None:
        for f in sorted((self.cfg.work_dir / "przebiegi").glob("*/przebieg.json")):
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
                job = Job(
                    id=d["id"],
                    mode=d["mode"],
                    pdf_name=d["pdf_name"],
                    pdf_path=Path(d.get("pdf_path", "")),
                    out_dir=f.parent,
                    max_attempts=d.get("max_attempts", 3),
                    part_filter=None,
                    parts=d.get("parts", []),
                    created=d.get("created", 0.0),
                    status=d["status"] if d["status"] in ("done", "error", "cancelled") else "error",
                    phase=d.get("phase", ""),
                    started=d.get("started"),
                    finished=d.get("finished"),
                    error=d.get("error"),
                    log=d.get("log", []),
                    result=d.get("result"),
                    costs=d.get("costs"),
                    decisions_path=Path(d["decisions_path"]) if d.get("decisions_path")
                    else (self.cfg.decisions_path(Path(d["pdf_path"])) if d.get("pdf_path") else None),
                )
            except (OSError, KeyError, ValueError, TypeError):
                continue
            self.jobs[job.id] = job

    def report_file(self, job_id: str, index: int, kind: str) -> Path:
        """Path of a part's image / STEP / preview file; only files inside the run folder."""
        job = self.get_job(job_id)
        reports = (job.result or {}).get("reports", [])
        if not 0 <= index < len(reports):
            raise AppError("Nie ma takiej części.", 404)
        r = reports[index]
        if kind == "image":
            p = r.get("image_path")
        elif kind in ("step", "stl", "edges"):
            p = r.get("step_path")
            if p and kind != "step":
                if not Path(p).is_file():
                    raise AppError("Brak pliku STEP.", 404)
                stl, edges = export_preview(p, job.out_dir / "podglad3d")
                p = str(stl if kind == "stl" else edges)
        else:
            raise AppError("Nieznany rodzaj pliku.", 404)
        if not p:
            raise AppError("Brak pliku dla tej części.", 404)
        path = Path(p).resolve()
        if not path.is_file() or not path.is_relative_to(job.out_dir.resolve()):
            raise AppError("Brak pliku dla tej części.", 404)
        return path


def default_max_cost() -> float:
    """Per-run cost limit from LLM_MAX_COST (environment or .env, as in the CLI)."""
    load_dotenv()
    env = os.environ.get("LLM_MAX_COST")
    try:
        return float(env) if env else DEFAULT_MAX_COST_USD
    except ValueError:
        return DEFAULT_MAX_COST_USD
