"""Offline benchmark: re-verify stored real AI answers with the current code, at zero API cost.

Every answer (PartSpec JSON) is built, verified against the drawing (dimensions + views) and
compared with the reference model from podnosnik-testset (which the program itself never sees).

Usage (PowerShell):
    $env:PYTHONUTF8 = "1"
    python tools\\benchmark_offline.py                      # tests\\fixtures\\deepseek\\odpowiedzi
    python tools\\benchmark_offline.py --answers cache       # any folder with <nr>_*.json answers
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTSET = ROOT / "podnosnik-testset"
sys.path.insert(0, str(TESTSET))

from porownaj import TOL_OBJ, porownaj, wczytaj  # noqa: E402

from drawing2cad.blocks import BuildError, build_part  # noqa: E402
from drawing2cad.drawing_compare import extract_views, parse_scale  # noqa: E402
from drawing2cad.pdf_extract import extract_pdf  # noqa: E402
from drawing2cad.schema import PartSpec  # noqa: E402
from drawing2cad.verifier import verify_part  # noqa: E402

PDF = ROOT / "rysunki" / "cad_07_1.pdf"
DEFAULT_ANSWERS = ROOT / "tests" / "fixtures" / "deepseek" / "odpowiedzi"


@dataclass
class Row:
    answer: str
    drawing_no: str
    status: str  # OK / NIEPEWNE / BŁĄD (program)
    reference: str  # ZGODNY / NIEZGODNY / -
    volume_diff: tuple[float, float] | None
    reasons: list[str]
    gt_ambiguities: list[str] = None  # drawing ambiguities listed in ground_truth.json

    @property
    def ok_despite_ambiguity(self) -> bool:
        return self.status == "OK" and bool(self.gt_ambiguities)

    @property
    def silent_error(self) -> bool:
        """Wrong model the program did not reject. NIEPEWNE counts as silent only when no
        program-side reason was given (AI remarks alone do not protect the user)."""
        if self.reference != "NIEZGODNY":
            return False
        if self.status == "OK":
            return True
        return self.status == "NIEPEWNE" and not any(r.startswith("[program]") for r in self.reasons)


_CONTEXT: dict = {}


def _context():
    """Drawing data shared by all answers (extracted once per process)."""
    if not _CONTEXT:
        parts = {p.drawing_no: p for p in extract_pdf(PDF).parts}
        _CONTEXT.update(
            parts=parts,
            views={n: extract_views(str(PDF), p.page_index, p.bbox) for n, p in parts.items()},
            scales={n: parse_scale(p.title_block.skala) if p.title_block else None for n, p in parts.items()},
            refs={f.name[:8]: f for f in (TESTSET / "step").glob("*.step")},
        )
        gt = json.loads((TESTSET / "ground_truth.json").read_text(encoding="utf-8"))
        _CONTEXT["gt_amb"] = {
            c["nr_rysunku"]: [a["id"] for a in c.get("niejednoznacznosci", []) if a.get("waga") in ("blad_rysunku", "ostrzezenie")]
            for c in gt["czesci"]
        }
    return _CONTEXT


def evaluate(answers_dir: Path) -> list[Row]:
    return [r for f in sorted(Path(answers_dir).glob("*.json")) if (r := evaluate_file(f)) is not None]


def evaluate_file(f: Path) -> Row | None:
    """Build, verify and compare one stored answer."""
    ctx = _context()
    parts, views, scales, refs, gt_amb = ctx["parts"], ctx["views"], ctx["scales"], ctx["refs"], ctx["gt_amb"]
    try:
        spec = PartSpec.model_validate_json(f.read_text(encoding="utf-8"))
    except Exception:
        return None
    n = spec.drawing_no
    if n not in parts:
        return None
    try:
        solid = build_part(spec)
    except BuildError as exc:
        return Row(f.name, n, "BŁĄD", "-", None, [f"[budowa] {exc}"], gt_amb.get(n, []))
    report = verify_part(spec, solid, parts[n].dimensions, views=views[n], sheet_scale=scales[n])
    nad, brak = porownaj(wczytaj(str(refs[n])), solid.val())
    ref = "ZGODNY" if nad < TOL_OBJ and brak < TOL_OBJ else "NIEZGODNY"
    reasons = [f"[błąd] {e}" for e in report.errors[:3]]
    reasons += [
        f"[{'program' if a.source == 'program' else 'AI'}] {a.id}: {a.description}"
        for a in report.ambiguities if a.severity in ("blad_rysunku", "ostrzezenie")
    ]
    return Row(f.name, n, report.status, ref, (round(nad, 1), round(brak, 1)), reasons, gt_amb.get(n, []))


def main() -> int:
    ap = argparse.ArgumentParser(description="Offline benchmark na zapisanych odpowiedziach AI (bez kosztów API).")
    ap.add_argument("--answers", default=str(DEFAULT_ANSWERS))
    ap.add_argument("--json", default=None, help="zapisz wyniki do pliku JSON")
    args = ap.parse_args()
    rows = evaluate(Path(args.answers))

    print(f"{'Odpowiedź':<34} {'Program':<9} {'Wzorzec':<10} {'Nadmiar/brak mm3':<18} Powód")
    print("-" * 110)
    for r in rows:
        vd = f"{r.volume_diff[0]}/{r.volume_diff[1]}" if r.volume_diff else "-"
        flag = "  <-- CICHY BŁĄD" if r.silent_error else (
            f"  <-- OK mimo niejednoznaczności {','.join(r.gt_ambiguities)}" if r.ok_despite_ambiguity else "")
        print(f"{r.answer:<34} {r.status:<9} {r.reference:<10} {vd:<18} {(r.reasons[0][:60] if r.reasons else '')}{flag}")
    n = len(rows)
    ok_good = sum(r.reference == "ZGODNY" for r in rows)
    print("-" * 110)
    print(f"Odpowiedzi: {n} | zgodnych ze wzorcem: {ok_good} | OK: {sum(r.status == 'OK' for r in rows)} | "
          f"NIEPEWNE: {sum(r.status == 'NIEPEWNE' for r in rows)} | BŁĄD: {sum(r.status == 'BŁĄD' for r in rows)} | "
          f"ciche błędy: {sum(r.silent_error for r in rows)} | "
          f"OK mimo niejednoznaczności z ground_truth: {sum(r.ok_despite_ambiguity for r in rows)}")
    if args.json:
        Path(args.json).write_text(json.dumps([r.__dict__ for r in rows], ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if any(r.silent_error for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
