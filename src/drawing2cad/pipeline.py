"""End-to-end processing pipeline with repair loop (max 3 attempts) and ambiguity handling."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

import cadquery as cq

from drawing2cad.blocks import BuildError, build_part
from drawing2cad.drawing_compare import extract_views, parse_scale
from drawing2cad.interpreter import FatalInterpreterError, PartInterpreter
from drawing2cad.pdf_extract import extract_pdf
from drawing2cad.report import write_html_report
from drawing2cad.schema import (
    Ambiguity,
    ExtractedDocument,
    PartRegion,
    PartSpec,
    VerificationReport,
)
from drawing2cad.verifier import verify_part

_POLISH_ASCII = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")


def _slugify(text: str) -> str:
    s = text.translate(_POLISH_ASCII).lower()
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "czesc"


@dataclass
class PipelineResult:
    document: ExtractedDocument
    reports: list[VerificationReport] = field(default_factory=list)
    specs: dict[str, PartSpec] = field(default_factory=dict)
    html_report_path: str | None = None
    json_report_path: str | None = None


def flag_unrecognized_texts(report: VerificationReport, part: PartRegion) -> VerificationReport:
    """Texts that look like dimensions but were not understood were not checked: never OK."""
    if not part.unrecognized_texts:
        return report
    amb = Ambiguity(
        id="PROG-NIEROZPOZNANE-TEKSTY",
        description="Na rysunku są teksty z liczbami, których program nie rozpoznał jako wymiarów: "
                    + ", ".join(f"'{t}'" for t in part.unrecognized_texts[:8]),
        assumption="Te teksty nie zostały sprawdzone w modelu.",
        severity="ostrzezenie",
        source="program",
    )
    status = report.status if report.status == "BŁĄD" else "NIEPEWNE"
    return report.model_copy(update={"ambiguities": list(report.ambiguities) + [amb], "status": status})


def flag_document_warnings(report: VerificationReport, doc: ExtractedDocument) -> VerificationReport:
    """Document-level extraction notes (e.g. pages that may be sheets of one part): never OK."""
    if not doc.warnings:
        return report
    ambs = [
        Ambiguity(id=f"PROG-DOKUMENT-{i + 1}", description=w, assumption="Wynik może dotyczyć tylko fragmentu części.",
                  severity="ostrzezenie", source="program")
        for i, w in enumerate(doc.warnings)
    ]
    status = report.status if report.status == "BŁĄD" else "NIEPEWNE"
    return report.model_copy(update={"ambiguities": list(report.ambiguities) + ambs, "status": status})


def process_part(
    part: PartRegion,
    interpreter: PartInterpreter,
    out_dir: Path,
    max_attempts: int = 3,
    pdf_path: str | Path | None = None,
    part_id: str | None = None,
) -> tuple[VerificationReport, PartSpec | None]:
    """Run interpret -> build -> verify repair loop for a single part (up to `max_attempts`).

    With `pdf_path` the model is also compared with the drawn views; without it the best
    possible result is NIEPEWNE. `part_id` names the output files (unique within one PDF)."""
    if part_id is None:
        part_id = part.drawing_no or f"strona_{part.page_index + 1}"
    part_id = re.sub(r"[^\w.\-]+", "_", part_id)
    part_name = part.name or "czesc"
    if part.warnings:
        # the dimensions cannot be read (no vector text, inch units...): never build something silently
        return VerificationReport(
            drawing_no=part_id,
            name=part_name,
            status="BŁĄD",
            passed=False,
            errors=[f"Ekstrakcja z PDF: {w}" for w in part.warnings] + ["Nie budowano modelu (nie wywołano AI)."],
            attempts=0,
            image_path=part.image_path,
        ), None

    views = extract_views(str(pdf_path), part.page_index, part.bbox) if pdf_path else None
    sheet_scale = parse_scale(part.title_block.skala) if part.title_block else None
    feedback_errors: list[str] = []
    last_report: VerificationReport | None = None
    last_spec: PartSpec | None = None
    last_solid: cq.Workplane | None = None

    step_filename = f"{part_id}_{_slugify(part_name)}.step"
    step_path = out_dir / step_filename

    for attempt in range(1, max_attempts + 1):
        try:
            spec = interpreter.interpret(
                part,
                feedback_errors=feedback_errors if feedback_errors else None,
                attempt=attempt,
            )
            last_spec = spec
        except FatalInterpreterError as exc:
            last_report = VerificationReport(
                drawing_no=part_id,
                name=part_name,
                status="BŁĄD",
                passed=False,
                errors=[f"Błąd interpretacji AI (nie ponawiam): {exc}"],
                attempts=attempt,
                image_path=part.image_path,
            )
            break
        except Exception as exc:
            err = f"Błąd interpretacji AI (próba {attempt}): {exc}"
            feedback_errors = [err]
            last_report = VerificationReport(
                drawing_no=part_id,
                name=part_name,
                status="BŁĄD",
                passed=False,
                errors=[err],
                attempts=attempt,
                image_path=part.image_path,
            )
            continue

        try:
            solid = build_part(spec)
            last_solid = solid
        except BuildError as exc:
            err = f"Błąd budowy bryły CAD: {exc}"
            feedback_errors = [err]
            last_report = VerificationReport(
                drawing_no=spec.drawing_no,
                name=spec.name,
                status="BŁĄD",
                passed=False,
                ambiguities=list(spec.ambiguities),
                threads=list(spec.threads),
                errors=[err],
                attempts=attempt,
                image_path=part.image_path,
            )
            continue

        report = verify_part(
            spec=spec,
            solid=solid,
            extracted_dimensions=part.dimensions,
            attempts=attempt,
            image_path=part.image_path,
            views=views,
            sheet_scale=sheet_scale,
        )
        report = flag_unrecognized_texts(report, part)
        last_report = report

        if report.passed:
            out_dir.mkdir(parents=True, exist_ok=True)
            cq.exporters.export(solid, str(step_path))
            report.step_path = str(step_path)
            return report, spec

        feedback_errors = list(report.errors)

    # All attempts exhausted: still export last valid solid (clearly marked BŁĄD) for inspection
    if last_solid is not None and last_report is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        failed_step = out_dir / f"BLEDNY_{step_filename}"
        cq.exporters.export(last_solid, str(failed_step))
        last_report.step_path = str(failed_step)

    assert last_report is not None
    return last_report, last_spec


def process_pdf(
    pdf_path: str | Path,
    out_dir: str | Path,
    interpreter: PartInterpreter,
    max_attempts: int = 3,
    part_filter: set[str] | None = None,
    generate_html: bool = True,
) -> PipelineResult:
    """Process an entire vector PDF drawing and export STEP models + HTML/JSON reports."""
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    render_dir = out_path / "obrazy"

    doc = extract_pdf(pdf_path, render_dir=render_dir)
    result = PipelineResult(document=doc)

    used_ids: set[str] = set()
    counts: dict[str | None, int] = {}
    for part in doc.parts:
        counts[part.drawing_no] = counts.get(part.drawing_no, 0) + 1
    for part in doc.parts:
        if part_filter and (part.drawing_no not in part_filter):
            continue
        # sheets with the same drawing number keep their own STEP file and spec
        part_id = part.drawing_no or f"strona_{part.page_index + 1}"
        if part.drawing_no and counts[part.drawing_no] > 1:
            part_id = f"{part.drawing_no}_s{part.page_index + 1}"
        base, k = part_id, 2
        while part_id.lower() in used_ids:
            part_id, k = f"{base}_{k}", k + 1
        used_ids.add(part_id.lower())
        report, spec = process_part(
            part=part,
            interpreter=interpreter,
            out_dir=out_path,
            max_attempts=max_attempts,
            pdf_path=pdf_path,
            part_id=part_id,
        )
        report = flag_document_warnings(report, doc)
        result.reports.append(report)
        if spec is not None:
            key = report.drawing_no if report.drawing_no not in result.specs else part_id
            result.specs[key] = spec

    # Save structured JSON report
    json_path = out_path / "raport.json"
    json_payload = {
        "source_pdf": doc.source_pdf,
        "bom": [b.model_dump() for b in doc.bom],
        "reports": [r.model_dump() for r in result.reports],
    }
    json_path.write_text(json.dumps(json_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    result.json_report_path = str(json_path)

    if generate_html:
        html_path = out_path / "raport.html"
        write_html_report(doc, result.reports, html_path)
        result.html_report_path = str(html_path)

    return result
