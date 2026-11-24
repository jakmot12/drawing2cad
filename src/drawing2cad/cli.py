"""Command line entry point for drawing2cad."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from drawing2cad import __version__
from drawing2cad.interpreter import PROVIDERS, LLMInterpreter, ManualSpecInterpreter
from drawing2cad.pdf_extract import extract_pdf
from drawing2cad.pipeline import process_pdf
from drawing2cad.confirm import DecisionStore, apply_decisions, ask_user, default_store_path
from drawing2cad.report import format_terminal_summary, write_html_report


def _safe_print(text: str, stream=None) -> None:
    stream = stream or sys.stdout
    try:
        print(text, file=stream)
    except UnicodeEncodeError:
        enc = getattr(stream, "encoding", None) or "utf-8"
        print(text.encode(enc, errors="replace").decode(enc), file=stream)


def _rewrite_reports(result, out_dir: Path, html: bool) -> None:
    """Write JSON / HTML reports again after user decisions changed the statuses."""
    payload = {
        "source_pdf": result.document.source_pdf,
        "bom": [b.model_dump() for b in result.document.bom],
        "reports": [r.model_dump() for r in result.reports],
    }
    (out_dir / "raport.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if html:
        write_html_report(result.document, result.reports, out_dir / "raport.html")


def _confirm_cost(interpreter: LLMInterpreter, pdf_path: Path, part_filter, args) -> bool:
    """Show the worst-case cost of a paid run and ask before spending anything."""
    parts = [p for p in extract_pdf(pdf_path).parts if not part_filter or p.drawing_no in part_filter]
    est = interpreter.costs.estimate_call()
    worst = min(len(parts) * args.max_attempts * est, interpreter.costs.max_cost or float("inf"))
    _safe_print(
        f"Wywołania płatnego API ({interpreter.provider}, {interpreter.model}): do {len(parts)} części x "
        f"{args.max_attempts} prób, ~{est:.2f} USD za wywołanie. Odpowiedzi z cache są darmowe.\n"
        f"Najgorszy przypadek: ~{worst:.2f} USD (limit przebiegu: {interpreter.costs.max_cost:.2f} USD)."
    )
    if args.yes:
        return True
    if not sys.stdin or not sys.stdin.isatty():
        _safe_print("Brak zgody: uruchom z --yes, żeby potwierdzić koszt, albo z --offline.", stream=sys.stderr)
        return False
    answer = input("Kontynuować? [t/N] ").strip().lower()
    return answer in ("t", "tak", "y", "yes")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="drawing2cad",
        description="Tworzy model 3D (STEP) z rysunku technicznego PDF i weryfikuje go z rysunkiem.",
    )
    p.add_argument("pdf", nargs="?", help="rysunek techniczny (wektorowy PDF)")
    p.add_argument("--out", default="wyniki", help="katalog wyników (domyślnie: wyniki/)")
    p.add_argument(
        "--cache-dir",
        default=None,
        help="katalog cache odpowiedzi AI (zapisuje i odczytuje odpowiedzi JSON)",
    )
    p.add_argument(
        "--offline",
        action="store_true",
        help="tryb offline: korzystaj wyłącznie z cache (bez wywołań API)",
    )
    p.add_argument(
        "--provider",
        choices=sorted(PROVIDERS),
        default=None,
        help="dostawca LLM (domyślnie LLM_PROVIDER z .env albo deepseek)",
    )
    p.add_argument("--model", default=None, help="model LLM (domyślnie LLM_MODEL z .env)")
    p.add_argument(
        "--reczne",
        default=None,
        metavar="KATALOG",
        help="użyj ręcznie napisanych specyfikacji <nr>.json zamiast AI (tylko testy klocków)",
    )
    p.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="maksymalna liczba prób pętli poprawek (domyślnie: 3)",
    )
    p.add_argument(
        "--parts",
        default=None,
        help="filtr numerów rysunków oddzielonych przecinkami (np. 01.01.01,01.01.02)",
    )
    p.add_argument(
        "--extract-only",
        action="store_true",
        help="tylko ekstrakcja z PDF (obszary, wymiary, BOM) bez wywołania AI i budowy STEP",
    )
    p.add_argument("--no-html", action="store_true", help="nie generuj raportu HTML")
    p.add_argument(
        "--max-cost",
        type=float,
        default=None,
        help="limit kosztu wywołań AI w USD na przebieg (domyślnie LLM_MAX_COST z .env albo 0.50)",
    )
    p.add_argument("--yes", action="store_true", help="nie pytaj o zgodę na płatne wywołania API")
    p.add_argument("--pytaj", action="store_true",
                   help="zapytaj o każde założenie przy niejednoznaczności rysunku (potwierdź / odrzuć)")
    p.add_argument("--decyzje", default=None, metavar="PLIK",
                   help="plik z decyzjami o założeniach (domyślnie <rysunek>.decyzje.json obok PDF)")
    p.add_argument("--version", action="version", version=f"drawing2cad {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    p = build_parser()
    args = p.parse_args(argv)
    if args.pdf is None:
        p.print_help()
        return 0

    pdf_path = Path(args.pdf)
    if not pdf_path.is_file():
        _safe_print(f"Błąd: plik nie istnieje: {pdf_path}", stream=sys.stderr)
        return 1

    out_dir = Path(args.out)

    if args.extract_only:
        doc = extract_pdf(pdf_path, render_dir=out_dir / "obrazy")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_json = out_dir / "ekstrakcja.json"
        out_json.write_text(
            json.dumps(doc.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        _safe_print(
            f"Wyekstrahowano {len(doc.parts)} części i {len(doc.bom)} pozycji BOM -> {out_json}"
        )
        for w in doc.warnings:
            _safe_print(f"  UWAGA: {w}")
        for part in doc.parts:
            dims = ", ".join(d.decoded_text for d in part.dimensions)
            _safe_print(f"  - {part.drawing_no or '?'} {part.name or '?'}: [{dims}]")
            if part.unrecognized_texts:
                _safe_print(f"    nierozpoznane: {', '.join(part.unrecognized_texts)}")
            for w in part.warnings:
                _safe_print(f"    BŁĄD EKSTRAKCJI: {w}")
        if not doc.parts and not doc.bom:
            _safe_print("  BŁĄD EKSTRAKCJI: w pliku nie znaleziono żadnej części ani wykazu części")
        return 1 if any(p.warnings for p in doc.parts) or (not doc.parts and not doc.bom) else 0

    part_filter = (
        {s.strip() for s in args.parts.split(",") if s.strip()} if args.parts else None
    )

    if args.reczne:
        _safe_print("UWAGA: ręczne specyfikacje zamiast AI, wynik nie jest benchmarkiem programu.")
        interpreter = ManualSpecInterpreter(args.reczne)
    else:
        try:
            interpreter = LLMInterpreter(
                provider=args.provider,
                model=args.model,
                cache_dir=args.cache_dir,
                offline_only=args.offline,
                max_cost=args.max_cost,
            )
        except ValueError as exc:
            _safe_print(f"Błąd konfiguracji: {exc}", stream=sys.stderr)
            return 1
        if not args.offline and not _confirm_cost(interpreter, pdf_path, part_filter, args):
            return 1

    try:
        result = process_pdf(
            pdf_path=pdf_path,
            out_dir=out_dir,
            interpreter=interpreter,
            max_attempts=args.max_attempts,
            part_filter=part_filter,
            generate_html=not args.no_html,
        )
    except Exception as exc:
        _safe_print(f"Błąd przetwarzania: {exc}", stream=sys.stderr)
        return 1

    if result.document.is_assembly and not result.reports:
        _safe_print(
            f"Rysunek złożeniowy ({result.document.assembly_title_block.nr_rysunku if result.document.assembly_title_block else '?'}): "
            f"wyekstrahowano {len(result.document.bom)} pozycji BOM."
        )
        return 0
    if not result.reports:
        why = "żadna część nie pasuje do --parts" if part_filter else "w pliku nie znaleziono żadnej części"
        _safe_print(f"BŁĄD EKSTRAKCJI: {why}; nic nie przetworzono.", stream=sys.stderr)
        return 1

    store = DecisionStore(args.decyzje or default_store_path(pdf_path))
    if args.pytaj:
        if not sys.stdin or not sys.stdin.isatty():
            _safe_print("--pytaj wymaga terminala; pomijam pytania.", stream=sys.stderr)
        else:
            for r in result.reports:
                ask_user(r, store, print_fn=_safe_print)
            store.save()
    if store.data:
        result.reports = [apply_decisions(r, store) for r in result.reports]
        _rewrite_reports(result, out_dir, not args.no_html)
        _safe_print(f"Zastosowano decyzje użytkownika z {store.path}")
    _safe_print(format_terminal_summary(result.reports, result.html_report_path))
    costs = getattr(interpreter, "costs", None)
    if costs is not None:
        _safe_print(costs.summary())
    has_error = any(r.status == "BŁĄD" for r in result.reports)
    return 1 if has_error else 0


if __name__ == "__main__":
    sys.exit(main())
