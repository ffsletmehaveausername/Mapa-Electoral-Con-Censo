"""Serve the isolated map and approved exports on loopback only."""

import argparse
import sys
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        # Rebuilds overwrite site/data in place; stop browsers from mixing stale JS/data.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def translate_path(self, path: str) -> str:
        requested = unquote(urlsplit(path).path)
        if requested.startswith("/downloads/"):
            base = (ROOT / "outputs").resolve()
            candidate = (base / requested.removeprefix("/downloads/")).resolve()
            if not candidate.is_relative_to(base) or candidate.suffix not in (".csv", ".txt"):
                return str(ROOT / "site" / "not-found")
            return str(candidate)
        return super().translate_path(path)


class Server(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second server silently share the port.
    allow_reuse_address = False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--open", action="store_true", help="open the map in a browser once serving")
    arguments = parser.parse_args()
    handler = partial(Handler, directory=str(ROOT / "site"))
    url = f"http://127.0.0.1:{arguments.port}/"
    try:
        server = Server(("127.0.0.1", arguments.port), handler)
    except OSError as error:
        print(f"Cannot start server on port {arguments.port}: {error}", file=sys.stderr)
        print("Another map server may already be running; close it or use --port.", file=sys.stderr)
        sys.exit(1)
    print(f"AREAL PREKEY UNIKEY: {url}", flush=True)
    if arguments.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
