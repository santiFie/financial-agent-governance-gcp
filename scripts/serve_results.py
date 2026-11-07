#!/usr/bin/env python3
"""Serves docs/evidence (static HTML viewer + saved runs) on localhost and opens it in the browser.

Needs no LLM, no Docker and no credentials: anyone can inspect the recorded attack runs.
Usage: python3 scripts/serve_results.py [--port 8765] [--no-open]
"""

import argparse
import functools
import http.server
import socketserver
import threading
import webbrowser
from pathlib import Path

RESULTS_DIR = Path(__file__).resolve().parent.parent / "docs" / "evidence"


def main() -> None:
    parser = argparse.ArgumentParser(description="Sirve el visor HTML de resultados del ataque.")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true", help="no abrir el navegador")
    args = parser.parse_args()

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(RESULTS_DIR))
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", args.port), handler) as httpd:
        url = f"http://localhost:{args.port}/"
        print(f"Resultados del ataque 3.1 en {url}  (Ctrl+C para detener)")
        if not args.no_open:
            threading.Timer(0.5, lambda: webbrowser.open(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServidor detenido.")


if __name__ == "__main__":
    main()
