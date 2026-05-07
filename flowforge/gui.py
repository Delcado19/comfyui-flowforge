"""
GUI launcher for ComfyUI FlowForge.
Starts the API server and opens the frontend in the default browser.
"""

import asyncio
import queue
import threading
import time
import webbrowser
import http.client
import socket
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from pathlib import Path
from .logger import setup_logger
from .api import create_app

logger = setup_logger(__name__)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
FRONTEND_DIST = FRONTEND_DIR / "dist"
PACKAGE_FRONTEND_DIST = Path(__file__).parent / "frontend_dist"
FRONTEND_HOST = "127.0.0.1"
FRONTEND_PORT_SEARCH_LIMIT = 20
API_HOST = "127.0.0.1"
API_PORT_SEARCH_LIMIT = 20
PROXY_ROUTES = {"/layout", "/optimize", "/health"}


def main():
    """Launch the GUI - start API server and open frontend."""
    import sys

    api_ready: queue.Queue[int | Exception] = queue.Queue(maxsize=1)
    frontend_port = 5173

    # Start API server in background thread
    api_thread = threading.Thread(
        target=_run_api_server,
        args=(8000, api_ready),
        daemon=True
    )
    api_thread.start()

    api_port = api_ready.get()
    if isinstance(api_port, Exception):
        raise api_port

    logger.info(f"Starting API server on http://{API_HOST}:{api_port}...")

    frontend_dist = _select_frontend_dist()
    if frontend_dist is not None:
        logger.info(f"Serving frontend from {frontend_dist}")
        frontend_server, frontend_port = _create_frontend_dist_server(frontend_port, api_port, frontend_dist)
        frontend_thread = threading.Thread(
            target=_serve_frontend_dist,
            args=(frontend_server, frontend_port),
            daemon=True,
        )
        frontend_thread.start()
    else:
        logger.info("Frontend not built. Starting development server...")
        frontend_port = _find_available_frontend_port(frontend_port)
        _run_frontend_dev(frontend_port, api_port)

    # Open browser
    url = f"http://{FRONTEND_HOST}:{frontend_port}"
    logger.info(f"Opening {url} in browser...")
    webbrowser.open(url)

    try:
        # Keep main thread alive
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        sys.exit(0)


def _run_api_server(preferred_port: int, ready_queue: queue.Queue[int | Exception]):
    """Run the aiohttp API server."""
    from aiohttp import web
    app = create_app()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def serve() -> None:
        runner = web.AppRunner(app)
        await runner.setup()
        try:
            for candidate in _port_candidates(preferred_port, API_PORT_SEARCH_LIMIT):
                try:
                    site = web.TCPSite(runner, host=API_HOST, port=candidate)
                    await site.start()
                except OSError as exc:
                    logger.warning(f"API port {candidate} is unavailable: {exc}")
                    continue

                ready_queue.put(candidate)
                await asyncio.Event().wait()

            raise RuntimeError(f"No available API port found starting at {preferred_port}")
        finally:
            await runner.cleanup()

    try:
        loop.run_until_complete(serve())
    except Exception as exc:
        if ready_queue.empty():
            ready_queue.put(exc)
        raise
    finally:
        loop.close()


def _select_frontend_dist() -> Path | None:
    """Return the packaged or source-built frontend dist directory if available."""
    for candidate in (PACKAGE_FRONTEND_DIST, FRONTEND_DIST):
        if candidate.exists() and (candidate / "index.html").exists():
            return candidate
    return None


def _create_frontend_dist_server(
    preferred_port: int,
    api_port: int,
    dist_dir: Path | None = None,
    max_attempts: int = FRONTEND_PORT_SEARCH_LIMIT,
):
    """Serve the built frontend files."""
    selected_dist = dist_dir or _select_frontend_dist() or FRONTEND_DIST

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # Suppress logs

        def _proxy_api(self):
            path = urlsplit(self.path).path
            if path not in PROXY_ROUTES:
                self.send_error(404)
                return

            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else None
            conn = http.client.HTTPConnection(API_HOST, api_port, timeout=60)
            headers = {}
            for key, value in self.headers.items():
                lowered = key.lower()
                if lowered in {"host", "content-length", "connection"}:
                    continue
                headers[key] = value
            try:
                conn.request(self.command, self.path, body=body, headers=headers)
                response = conn.getresponse()
                payload = response.read()
                self.send_response(response.status)
                for key, value in response.getheaders():
                    if key.lower() in {"transfer-encoding", "connection", "content-length"}:
                        continue
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                if payload:
                    self.wfile.write(payload)
            finally:
                conn.close()

        def do_GET(self):
            if urlsplit(self.path).path in PROXY_ROUTES:
                self._proxy_api()
                return
            return super().do_GET()

        def do_POST(self):
            if urlsplit(self.path).path in PROXY_ROUTES:
                self._proxy_api()
                return
            self.send_error(405)

        def do_OPTIONS(self):
            if urlsplit(self.path).path in PROXY_ROUTES:
                self.send_response(204)
                self.send_header("Allow", "GET, POST, OPTIONS")
                self.end_headers()
                return
            self.send_error(405)

    handler = partial(Handler, directory=str(selected_dist))
    for port in _frontend_port_candidates(preferred_port, max_attempts):
        try:
            return ThreadingHTTPServer((FRONTEND_HOST, port), handler), port
        except OSError as exc:
            logger.warning(f"Frontend port {port} is unavailable: {exc}")

    raise RuntimeError(f"No available frontend port found starting at {preferred_port}")


def _serve_frontend_dist(server: ThreadingHTTPServer, port: int):
    """Serve an already-bound built frontend server."""
    logger.info(f"Frontend server running on http://{FRONTEND_HOST}:{port}")
    try:
        server.serve_forever()
    finally:
        server.server_close()


def _find_available_frontend_port(preferred_port: int, max_attempts: int = FRONTEND_PORT_SEARCH_LIMIT) -> int:
    """Find a local frontend port that can be bound by the current user."""
    for port in _port_candidates(preferred_port, max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((FRONTEND_HOST, port))
            except OSError as exc:
                logger.warning(f"Frontend port {port} is unavailable: {exc}")
                continue
            return port

    raise RuntimeError(f"No available frontend port found starting at {preferred_port}")


def _find_available_port(preferred_port: int, max_attempts: int) -> int:
    """Find a local TCP port that can be bound by the current user."""
    for port in _port_candidates(preferred_port, max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((API_HOST, port))
            except OSError as exc:
                logger.warning(f"Port {port} is unavailable: {exc}")
                continue
            return port

    raise RuntimeError(f"No available port found starting at {preferred_port}")


def _frontend_port_candidates(preferred_port: int, max_attempts: int):
    """Yield sequential frontend ports starting with the preferred one."""
    return _port_candidates(preferred_port, max_attempts)


def _port_candidates(preferred_port: int, max_attempts: int):
    """Yield sequential TCP ports starting with the preferred one."""
    return range(preferred_port, preferred_port + max_attempts)


def _run_frontend_dev(port: int, api_port: int):
    """Run the Vue dev server."""
    import subprocess
    import os

    if not (FRONTEND_DIR / "node_modules").exists():
        logger.info("Installing frontend dependencies...")
        subprocess.run(["npm", "install"], cwd=FRONTEND_DIR, check=True)

    logger.info(f"Starting Vue dev server on http://{FRONTEND_HOST}:{port}...")
    env = os.environ.copy()
    env["FLOWFORGE_API_PORT"] = str(api_port)
    subprocess.Popen(
        ["npm", "run", "dev", "--", "--host", FRONTEND_HOST, "--port", str(port)],
        cwd=FRONTEND_DIR,
        env=env,
    )
