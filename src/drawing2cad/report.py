"""HTML report generator and Windows-safe Polish terminal summary."""
from __future__ import annotations

import base64
import html
from pathlib import Path

from drawing2cad.drawing_compare import TOL_MM
from drawing2cad.schema import ExtractedDocument, VerificationReport


def _badge(status: str) -> str:
    cls = {
        "OK": "badge-ok",
        "NIEPEWNE": "badge-warn",
        "BŁĄD": "badge-err",
    }.get(status, "badge-warn")
    return f'<span class="badge {cls}">{html.escape(status)}</span>'


def _img_data_uri(image_path: str | None) -> str:
    if not image_path:
        return ""
    p = Path(image_path)
    if not p.is_file():
        return ""
    b64 = base64.standard_b64encode(p.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def render_html_report(doc: ExtractedDocument, reports: list[VerificationReport]) -> str:
    """Render a self-contained HTML verification report in Polish."""
    n_total = len(reports)
    n_ok = sum(1 for r in reports if r.status == "OK")
    n_warn = sum(1 for r in reports if r.status == "NIEPEWNE")
    n_err = sum(1 for r in reports if r.status == "BŁĄD")

    bom_html = ""
    if doc.bom:
        rows = "\n".join(
            f"<tr><td>{b.poz}</td><td>{b.ilosc}</td><td>{html.escape(b.nazwa)}</td>"
            f"<td>{html.escape(b.nr_rysunku or '—')}</td>"
            f"<td>{'Tak' if b.znormalizowana else 'Nie'}</td></tr>"
            for b in doc.bom
        )
        bom_html = f"""
        <section class="card">
          <h2>Wykaz części (BOM)</h2>
          <table>
            <thead><tr><th>Poz.</th><th>Ilość</th><th>Nazwa</th><th>Nr rysunku</th><th>Znormalizowana</th></tr></thead>
            <tbody>{rows}</tbody>
          </table>
        </section>"""

    parts_html_list: list[str] = []
    for r in reports:
        img_uri = _img_data_uri(r.image_path)
        img_block = (
            f'<div class="part-img"><img src="{img_uri}" alt="{html.escape(r.name)}"/></div>'
            if img_uri
            else ""
        )

        check_rows = "\n".join(
            f"""<tr class="row-{c.status.lower().replace('ł', 'l').replace('ą', 'a')}">
              <td><code>{html.escape(c.dim_id)}</code></td>
              <td><strong>{html.escape(c.raw_text)}</strong></td>
              <td>{html.escape(c.target)}</td>
              <td>{html.escape(c.expected)}</td>
              <td>{html.escape(c.measured)}</td>
              <td>{_badge(c.status)}</td>
              <td>{html.escape(c.message)}</td>
            </tr>"""
            for c in r.checks
        )

        if r.view_checks:
            v_rows = "\n".join(
                f"""<tr class="row-{'ok' if v.ok else ('niepewne' if v.uncertain else 'blad')}">
                  <td>{v.view_index}</td><td>{html.escape(v.projection)}</td>
                  <td>{f'{v.scale:g}' if v.scale else '—'}</td>
                  <td>{_badge('OK' if v.ok else ('NIEPEWNE' if v.uncertain else 'BŁĄD'))}</td><td>{html.escape(v.message)}</td></tr>"""
                for v in r.view_checks
            )
            views_block = f"""
            <h3>Porównanie z widokami rysunku ({len(r.view_checks)})</h3>
            <p class="meta">Każda widoczna linia rysunku musi leżeć na krawędzi rzutu modelu i odwrotnie (tolerancja {str(TOL_MM).replace('.', ',')} mm).</p>
            <table>
              <thead><tr><th>Widok</th><th>Rzut modelu</th><th>Skala</th><th>Status</th><th>Szczegóły</th></tr></thead>
              <tbody>{v_rows}</tbody>
            </table>"""
        else:
            views_block = '<h3>Porównanie z widokami rysunku</h3><p class="meta">Nie wykonano: wynik najwyżej NIEPEWNE.</p>'

        threads_block = ""
        if r.threads:
            t_rows = "\n".join(
                f"<tr><td>{html.escape(t.spec)}</td><td>{html.escape(t.direction)}</td>"
                f"<td>Ø{t.nominal_diameter:g}</td>"
                f"<td>{f'{t.length:g} mm' if t.length else '—'}</td>"
                f"<td>{'wewnętrzny' if t.is_internal else 'zewnętrzny'}</td>"
                f"<td>{html.escape(t.location)}</td></tr>"
                for t in r.threads
            )
            threads_block = f"""
            <h3>Gwinty (metadane — bryła ma średnicę nominalną)</h3>
            <table>
              <thead><tr><th>Oznaczenie</th><th>Kierunek</th><th>Średnica nom.</th><th>Długość</th><th>Typ</th><th>Położenie</th></tr></thead>
              <tbody>{t_rows}</tbody>
            </table>"""

        amb_block = ""
        if r.ambiguities:
            a_items = "\n".join(
                f"""<div class="callout callout-{html.escape(a.severity)}">
                  <strong>[{html.escape(a.id)} / {html.escape(a.severity)} / {'program' if a.source == 'program' else 'AI'}]</strong>
                  {html.escape(a.description)}<br/>
                  <em>Przyjęto:</em> {html.escape(a.assumption)}
                </div>"""
                for a in r.ambiguities
            )
            amb_block = f"<h3>Niejednoznaczności i założenia</h3>{a_items}"

        err_block = ""
        if r.errors:
            e_items = "".join(f"<li>{html.escape(e)}</li>" for e in r.errors)
            err_block = f'<div class="callout callout-blad_rysunku"><strong>Błędy weryfikacji:</strong><ul>{e_items}</ul></div>'

        bb = ", ".join(f"{x:.2f}" for x in r.bounding_box_mm)
        step_label = html.escape(Path(r.step_path).name) if r.step_path else "brak"

        parts_html_list.append(
            f"""
            <section class="card">
              <div class="part-header">
                <div>
                  <h2>{html.escape(r.drawing_no)} — {html.escape(r.name)}</h2>
                  <p class="meta">
                    Gabaryty: <strong>{bb} mm</strong> &middot;
                    Objętość: <strong>{r.volume_mm3:.1f} mm³</strong> &middot;
                    Próby: <strong>{r.attempts}</strong> &middot;
                    STEP: <code>{step_label}</code>
                  </p>
                </div>
                <div>{_badge(r.status)}</div>
              </div>
              {img_block}
              {err_block}
              <h3>Weryfikacja wymiarów ({len(r.checks)})</h3>
              <table>
                <thead>
                  <tr><th>ID</th><th>Z rysunku</th><th>Cecha</th><th>Oczekiwane</th><th>Zmierzone</th><th>Status</th><th>Uwagi</th></tr>
                </thead>
                <tbody>{check_rows}</tbody>
              </table>
              {views_block}
              {threads_block}
              {amb_block}
            </section>"""
        )

    parts_html = "\n".join(parts_html_list)
    src_name = html.escape(Path(doc.source_pdf).name)

    return f"""<!DOCTYPE html>
<html lang="pl">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>drawing2cad — Raport weryfikacji ({src_name})</title>
  <style>
    :root {{
      --bg: #f6f8fa; --card: #ffffff; --border: #d0d7de; --text: #1f2328; --muted: #656d76;
      --ok-bg: #dafbe1; --ok-fg: #116329;
      --warn-bg: #fff8c5; --warn-fg: #9a6700;
      --err-bg: #ffebe9; --err-fg: #cf222e;
    }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: var(--bg); color: var(--text); margin: 0; padding: 24px; line-height: 1.45; }}
    .container {{ max-width: 1120px; margin: 0 auto; }}
    h1 {{ margin-top: 0; font-size: 1.7rem; }}
    .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 20px; }}
    .kpi {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }}
    .kpi .num {{ font-size: 1.8rem; font-weight: 700; }}
    .kpi .lbl {{ color: var(--muted); font-size: 0.9rem; }}
    .card {{ background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-bottom: 20px; }}
    .part-header {{ display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }}
    .part-header h2 {{ margin: 0 0 4px 0; font-size: 1.25rem; }}
    .meta {{ margin: 0 0 12px 0; color: var(--muted); font-size: 0.9rem; }}
    .part-img {{ margin: 12px 0; text-align: center; background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 8px; }}
    .part-img img {{ max-width: 100%; max-height: 360px; height: auto; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; margin-bottom: 12px; }}
    th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--border); }}
    th {{ background: #f3f4f6; font-weight: 600; }}
    .badge {{ display: inline-block; padding: 3px 10px; border-radius: 999px; font-weight: 600; font-size: 0.82rem; }}
    .badge-ok {{ background: var(--ok-bg); color: var(--ok-fg); }}
    .badge-warn {{ background: var(--warn-bg); color: var(--warn-fg); }}
    .badge-err {{ background: var(--err-bg); color: var(--err-fg); }}
    .callout {{ padding: 10px 14px; border-radius: 8px; margin-bottom: 8px; border-left: 4px solid; font-size: 0.9rem; }}
    .callout-blad_rysunku {{ background: var(--err-bg); border-color: var(--err-fg); }}
    .callout-ostrzezenie {{ background: var(--warn-bg); border-color: var(--warn-fg); }}
    .callout-info {{ background: #ddf4ff; border-color: #0969da; }}
    code {{ background: #eff1f3; padding: 2px 5px; border-radius: 4px; font-size: 0.88em; }}
  </style>
</head>
<body>
  <div class="container">
    <h1>drawing2cad — Raport weryfikacji: <code>{src_name}</code></h1>
    <div class="kpi-grid">
      <div class="kpi"><div class="num">{n_total}</div><div class="lbl">Części razem</div></div>
      <div class="kpi"><div class="num" style="color:var(--ok-fg)">{n_ok}</div><div class="lbl">OK</div></div>
      <div class="kpi"><div class="num" style="color:var(--warn-fg)">{n_warn}</div><div class="lbl">NIEPEWNE</div></div>
      <div class="kpi"><div class="num" style="color:var(--err-fg)">{n_err}</div><div class="lbl">BŁĄD</div></div>
    </div>
    {bom_html}
    {parts_html}
  </div>
</body>
</html>
"""


def write_html_report(
    doc: ExtractedDocument,
    reports: list[VerificationReport],
    out_path: str | Path,
) -> Path:
    p = Path(out_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_html_report(doc, reports), encoding="utf-8")
    return p


def format_terminal_summary(reports: list[VerificationReport], html_path: str | None = None) -> str:
    """Format a concise Polish summary table for terminal output."""
    lines = [
        f"{'Nr rysunku':<12} {'Nazwa':<14} {'Status':<10} {'Wymiary':<10} {'Widoki':<8} {'Próby':<6} {'STEP'}",
        "-" * 86,
    ]
    for r in reports:
        ok_cnt = sum(1 for c in r.checks if c.status == "OK")
        warn_cnt = sum(1 for c in r.checks if c.status == "NIEPEWNE")
        tot_cnt = len(r.checks)
        dims_str = f"{ok_cnt + warn_cnt}/{tot_cnt}"
        step_name = Path(r.step_path).name if r.step_path else "—"
        views_str = f"{sum(v.ok for v in r.view_checks)}/{len(r.view_checks)}" if r.view_checks else "brak"
        lines.append(
            f"{r.drawing_no:<12} {r.name:<14} {r.status:<10} {dims_str:<10} {views_str:<8} {r.attempts:<6} {step_name}"
        )
        for amb in r.ambiguities:
            if amb.severity in ("blad_rysunku", "ostrzezenie"):
                lines.append(f"  ! [{amb.severity}, {'program' if amb.source == 'program' else 'AI'}] {amb.description}")
        for err in r.errors:
            lines.append(f"  x {err}")

    lines.append("-" * 86)
    n_ok = sum(1 for r in reports if r.status == "OK")
    n_warn = sum(1 for r in reports if r.status == "NIEPEWNE")
    n_err = sum(1 for r in reports if r.status == "BŁĄD")
    lines.append(f"Podsumowanie: {len(reports)} części | OK: {n_ok} | NIEPEWNE: {n_warn} | BŁĄD: {n_err}")
    if html_path:
        lines.append(f"Raport HTML: {html_path}")
    return "\n".join(lines)
