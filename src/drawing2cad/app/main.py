"""Desktop launcher: local server + native window (pywebview) or the system browser."""
from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

PREFERRED_PORT = 8765


def _free_port(preferred: int) -> int:
    for port in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return s.getsockname()[1]
    raise RuntimeError("Brak wolnego portu.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="drawing2cad-app",
        description="Aplikacja okienkowa drawing2cad: rysunek PDF -> zweryfikowany model 3D.",
    )
    p.add_argument("--browser", action="store_true", help="otwórz interfejs w przeglądarce zamiast w oknie")
    p.add_argument("--no-open", action="store_true", help="tylko uruchom serwer (z --browser), nie otwieraj przeglądarki")
    p.add_argument("--port", type=int, default=0, help=f"port serwera (domyślnie {PREFERRED_PORT} albo wolny)")
    p.add_argument("--work-dir", default="wyniki/aplikacja", help="katalog wgranych plików i wyników przebiegów")
    p.add_argument("--cache-dir", default="cache", help="cache odpowiedzi AI dla prawdziwego przebiegu (jak w CLI)")
    p.add_argument("--decyzje-dir", default=None, metavar="KATALOG",
                   help="katalog plików decyzji o założeniach (domyślnie <rysunek>.decyzje.json obok PDF, jak w CLI)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        import uvicorn
    except ImportError:
        print('Brak zależności aplikacji. Zainstaluj: pip install -e ".[app]"', file=sys.stderr)
        return 1

    from drawing2cad.app.jobs import AppConfig
    from drawing2cad.app.server import create_app

    cfg = AppConfig(
        work_dir=Path(args.work_dir).resolve(),
        ai_cache_dir=Path(args.cache_dir).resolve(),
        decisions_dir=Path(args.decyzje_dir).resolve() if args.decyzje_dir else None,
    )
    app = create_app(cfg)
    port = args.port or _free_port(PREFERRED_PORT)
    url = f"http://127.0.0.1:{port}/"
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))

    webview = None
    if not args.browser:
        try:
            import webview  # pywebview
        except ImportError:
            print("Brak pywebview: otwieram interfejs w przeglądarce.", file=sys.stderr)

    print(f"drawing2cad: interfejs pod adresem {url} (wyniki: {cfg.work_dir})")
    if webview is None:
        if not args.no_open:
            threading.Timer(1.0, lambda: webbrowser.open(url)).start()
        print("Zatrzymanie: Ctrl+C")
        server.run()
        return 0

    thread = threading.Thread(target=server.run, name="uvicorn", daemon=True)
    thread.start()
    deadline = time.time() + 30
    while not server.started and thread.is_alive() and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        print("Serwer nie wystartował.", file=sys.stderr)
        return 1
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.create_window(
        "drawing2cad: rysunek PDF → model 3D", url, width=1480, height=940, min_size=(1024, 680),
        background_color="#0d1117",
    )
    try:
        webview.start()
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
