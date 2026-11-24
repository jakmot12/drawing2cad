"""Rule 6: drawing ambiguities are not guessed silently - the user confirms or rejects them.

Every blocking ambiguity (severity 'blad_rysunku' or 'ostrzezenie') of a report can be
confirmed or rejected by the user. Decisions are stored in a JSON file next to the drawing
and keyed by part, ambiguity id AND the exact assumption text: if a later run makes a
different assumption, the old decision does not apply and the user is asked again.

Every blocking entry of a part has its own key (`decision_keys`). The key covers the entry's
wording AND its sorted affected dimensions (a confirmation turns exactly those dimensions OK).
An id may repeat within a part, and even an identical entry may repeat: the n-th repetition of
an identical blocking entry gets the suffix "#n". Non-blocking ('info') entries never take part
in that numbering and never get a decision.

Decision files written by earlier versions (keys without dimensions, numbering that counted
'info' entries, or one decision shared by identical copies) are applied only where the old key
certainly meant the same entry; otherwise the old decision is not applied and the user is asked
again (`ambiguous`). A shared old decision is marked as superseded as soon as the user decides
on any copy of that entry, so it never comes back when the number of copies drops.

  confirmed -> the ambiguity stays in the report as 'info', marked as confirmed by the user;
  rejected  -> the part gets BŁĄD ("the assumption is wrong"), never OK.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Callable

from drawing2cad.schema import Ambiguity, VerificationReport

BLOCKING = ("blad_rysunku", "ostrzezenie")
CONFIRMED = "potwierdzone"
REJECTED = "odrzucone"


VERSION = 2  # decision records of the current key scheme carry "wersja": 2


def _digest(*parts: str) -> str:
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:12]


def decision_key(drawing_no: str, amb: Ambiguity, model: str = "") -> str:
    """Part + ambiguity + exact wording + affected dimensions + model fingerprint: a confirmation
    made for one model never carries over to a different model with the same (generic) remark,
    nor to other dimensions. Without dimensions the key equals the one of earlier versions."""
    dims = sorted(amb.affected_dimensions)
    parts = [amb.description, amb.assumption, model] + (["\x1f".join(dims)] if dims else [])
    return f"{drawing_no}|{amb.id}|{_digest(*parts)}"


def _old_key(drawing_no: str, amb: Ambiguity, model: str = "") -> str:
    """Plain key of earlier versions (without affected dimensions)."""
    return f"{drawing_no}|{amb.id}|{_digest(amb.description, amb.assumption, model)}"


def model_fingerprint(report: VerificationReport) -> str:
    bb = ",".join(f"{v:.3f}" for v in report.bounding_box_mm)
    return f"{bb}|{report.volume_mm3:.2f}"


def _record(drawing_no: str, amb: Ambiguity, decision: str, comment: str) -> dict:
    return {
        "decyzja": decision,
        "czas": time.strftime("%Y-%m-%d %H:%M"),
        "czesc": drawing_no,
        "zastrzezenie": amb.description,
        "zalozenie": amb.assumption,
        "komentarz": comment,
    }


def _keyed(report: VerificationReport) -> list[tuple[str, str]]:
    """(key, plain key of earlier versions) per ambiguity.

    Blocking entries: the n-th identical copy gets "#n". 'info' entries get keys of their own
    ("...#info", "...#info#n"), so they never shift the numbering of blocking entries."""
    fp = model_fingerprint(report)
    seen: dict[str, int] = {}
    out = []
    for a in report.ambiguities:
        tag = decision_key(report.drawing_no, a, fp)
        if a.severity not in BLOCKING:
            tag += "#info"
        n = seen[tag] = seen.get(tag, 0) + 1
        out.append((tag if n == 1 else f"{tag}#{n}", _old_key(report.drawing_no, a, fp)))
    return out


def decision_keys(report: VerificationReport) -> list[str]:
    """One key per ambiguity of the report, unique within the part and stable between runs
    (it depends only on the entries' content and order among identical blocking copies)."""
    return [k for k, _ in _keyed(report)]


def _is_current(record: dict) -> bool:
    return record.get("wersja", 1) >= VERSION


class DecisionStore:
    """User decisions on assumptions, persisted as JSON."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.data: dict[str, dict] = {}
        if self.path.is_file():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))

    def get(self, drawing_no: str, amb: Ambiguity, model: str = "") -> dict | None:
        """Decision in the format of earlier versions (one per wording). Prefer `resolve`."""
        return self.data.get(_old_key(drawing_no, amb, model))

    def set(self, drawing_no: str, amb: Ambiguity, decision: str, comment: str = "", model: str = "") -> None:
        """Decision in the format of earlier versions (one per wording). Prefer `set_key`."""
        self.data[_old_key(drawing_no, amb, model)] = _record(drawing_no, amb, decision, comment)

    def _has_old_records(self, old: str) -> bool:
        return any((k == old or k.startswith(old + "#")) and not _is_current(r) for k, r in self.data.items())

    def resolve(self, report: VerificationReport) -> list[tuple[str, dict | None, bool]]:
        """Per ambiguity of the report: (key, stored decision or None, ambiguous).

        `ambiguous` is True when a decision of an earlier version exists for the entry but cannot
        be assigned to it with certainty; it is then not applied (decision None) and the user
        must decide again. Non-blocking entries never have a decision."""
        keyed = _keyed(report)
        groups: dict[str, list[Ambiguity]] = {}
        for a, (_key, old) in zip(report.ambiguities, keyed):
            groups.setdefault(old, []).append(a)
        out = []
        for a, (key, old) in zip(report.ambiguities, keyed):
            if a.severity not in BLOCKING:
                out.append((key, None, False))
                continue
            d = self.data.get(key)
            if d is not None and _is_current(d):
                out.append((key, d, False))
                continue
            group = groups[old]
            blocking = [g for g in group if g.severity in BLOCKING]
            # earlier keys equal the current ones for this wording: no dimensions, no 'info' entries
            same_keys = len(blocking) == len(group) and not any(g.affected_dimensions for g in group)
            if d is not None:
                if "klucz" in d:  # per-copy decision of the previous scheme
                    ok = same_keys and d["klucz"] == key
                else:  # one decision per wording: certain only for a single blocking copy
                    ok = (key == old and len(blocking) == 1 and not blocking[0].affected_dimensions
                          and not d.get("zastapione"))
                if ok:
                    out.append((key, d, False))
                    continue
            if same_keys:  # only a decision shared by all copies is in doubt
                legacy = self.data.get(old)
                ambiguous = legacy is not None and "klucz" not in legacy and not _is_current(legacy)
            else:
                ambiguous = self._has_old_records(old)
            out.append((key, None, ambiguous))
        return out

    def set_key(self, report: VerificationReport, key: str, decision: str | None, comment: str = "") -> None:
        """Store (or with decision None remove) the decision for the blocking entry with this key.

        A decision of an earlier version shared by all copies of this wording is marked as
        superseded, so it never applies again (e.g. after the number of copies drops)."""
        keyed = _keyed(report)
        idx = next((i for i, (k, _old) in enumerate(keyed)
                    if k == key and report.ambiguities[i].severity in BLOCKING), None)
        if idx is None:
            raise KeyError(key)
        amb, old = report.ambiguities[idx], keyed[idx][1]
        legacy = self.data.get(old)
        if legacy is not None and old != key and "klucz" not in legacy and not _is_current(legacy):
            legacy["zastapione"] = time.strftime("%Y-%m-%d %H:%M")
        if decision is None:
            self.data.pop(key, None)
            return
        self.data[key] = _record(report.drawing_no, amb, decision, comment) | {
            "klucz": key, "wersja": VERSION, "wymiary": sorted(amb.affected_dimensions),
        }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)


def default_store_path(pdf_path: str | Path) -> Path:
    p = Path(pdf_path)
    return p.with_name(p.stem + ".decyzje.json")


def apply_decisions(report: VerificationReport, store: DecisionStore) -> VerificationReport:
    """Return a copy of the report with stored user decisions applied and the status recomputed."""
    ambiguities: list[Ambiguity] = []
    errors = list(report.errors)
    for a, (_key, d, _ambiguous) in zip(report.ambiguities, store.resolve(report)):
        if a.severity not in BLOCKING:
            d = None
        if d and d["decyzja"] == CONFIRMED:
            note = f" [Potwierdzone przez użytkownika {d['czas']}" + (f": {d['komentarz']}" if d.get("komentarz") else "") + ".]"
            ambiguities.append(a.model_copy(update={"severity": "info", "assumption": a.assumption + note}))
        elif d and d["decyzja"] == REJECTED:
            ambiguities.append(a)
            errors.append(
                f"Użytkownik odrzucił założenie ({a.id}): {a.assumption}"
                + (f" Uwaga użytkownika: {d['komentarz']}" if d.get("komentarz") else "")
            )
        else:
            ambiguities.append(a)

    blocking_dims = {dim for a in ambiguities if a.severity in BLOCKING for dim in a.affected_dimensions}
    checks = [
        c.model_copy(update={"status": "OK"}) if c.status == "NIEPEWNE" and c.dim_id not in blocking_dims else c
        for c in report.checks
    ]
    has_error = bool(errors) or any(c.status == "BŁĄD" for c in checks)
    uncertain = any(a.severity in BLOCKING for a in ambiguities) or any(c.status == "NIEPEWNE" for c in checks)
    status = "BŁĄD" if has_error else ("NIEPEWNE" if uncertain else "OK")
    return report.model_copy(update={
        "ambiguities": ambiguities, "errors": errors, "checks": checks,
        "status": status, "passed": not has_error,
    })


def ask_user(
    report: VerificationReport,
    store: DecisionStore,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> int:
    """Ask about every blocking ambiguity without a stored decision. Returns the number of answers."""
    answered = 0
    pending = [
        (a, key, ambiguous)
        for a, (key, d, ambiguous) in zip(report.ambiguities, store.resolve(report))
        if a.severity in BLOCKING and d is None
    ]
    if not pending:
        return 0
    print_fn(f"\n{report.drawing_no} {report.name}: {len(pending)} założeń do potwierdzenia")
    if any(ambiguous for _, _, ambiguous in pending):
        print_fn("  Uwaga: zapisana wcześniej decyzja dotyczyła kilku jednakowych zastrzeżeń naraz"
                 " albo nie da się jej jednoznacznie przypisać; nie jest stosowana,"
                 " decyzja potrzebna dla każdego osobno.")
    for a, key, _ in pending:
        who = "program" if a.source == "program" else "AI"
        print_fn(f"\n  [{a.id}, zgłosił: {who}] {a.description}")
        print_fn(f"  Przyjęte założenie: {a.assumption}")
        ans = input_fn("  [t] potwierdzam  [n] odrzucam  [Enter] pomiń: ").strip().lower()
        if ans in ("t", "tak", "y"):
            store.set_key(report, key, CONFIRMED)
            answered += 1
        elif ans in ("n", "nie", "no"):
            comment = input_fn("  Co jest nie tak? (opcjonalnie): ").strip()
            store.set_key(report, key, REJECTED, comment)
            answered += 1
    return answered
