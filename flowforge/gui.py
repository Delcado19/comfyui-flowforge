"""
GUI launcher for ComfyUI FlowForge.
Starts the API server and opens the frontend in the default browser.
"""

import threading
import time
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .logger import setup_logger
from .api import create_app

logger = setup_logger(__name__)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
FRONTEND_DIST = FRONTEND_DIR / "dist"
FRONTEND_HOST = "127.0.0.1"
FRONTEND_PORT_SEARCH_LIMIT = 20


def main():
    """Launch the GUI - start API server and open frontend."""
    import sys

    port = 8000
    frontend_port = 5173

    # Start API server in background thread
    logger.info("Starting API server...")
    api_thread = threading.Thread(
        target=_run_api_server,
        args=(port,),
        daemon=True
    )
    api_thread.start()

    # Wait for API server to start
    time.sleep(1)

    # Check if frontend dist exists, if not run dev server
    if FRONTEND_DIST.exists() and (FRONTEND_DIST / "index.html").exists():
        logger.info(f"Serving frontend from {FRONTEND_DIST}")
        frontend_server, frontend_port = _create_frontend_dist_server(frontend_port)
        frontend_thread = threading.Thread(
            target=_serve_frontend_dist,
            args=(frontend_server, frontend_port),
            daemon=True,
        )
        frontend_thread.start()
    else:
        logger.info("Frontend not built. Starting development server...")
        frontend_port = _find_available_frontend_port(frontend_port)
        _run_frontend_dev(frontend_port)

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


def _run_api_server(port: int):
    """Run the aiohttp API server."""
    from aiohttp import web
    app = create_app()
    web.run_app(app, port=port, handle_signals=False)


def _create_frontend_dist_server(preferred_port: int, max_attempts: int = FRONTEND_PORT_SEARCH_LIMIT):
    """Serve the built frontend files."""
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # Suppress logs

    handler = partial(Handler, directory=str(FRONTEND_DIST))
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
    import socket

    for port in _frontend_port_candidates(preferred_port, max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((FRONTEND_HOST, port))
            except OSError as exc:
                logger.warning(f"Frontend port {port} is unavailable: {exc}")
                continue
            return port

    raise RuntimeError(f"No available frontend port found starting at {preferred_port}")


def _frontend_port_candidates(preferred_port: int, max_attempts: int):
    """Yield sequential frontend ports starting with the preferred one."""
    return range(preferred_port, preferred_port + max_attempts)


def _run_frontend_dev(port: int):
    """Run the Vue dev server."""
    import subprocess

    if not (FRONTEND_DIR / "node_modules").exists():
        logger.info("Installing frontend dependencies...")
        subprocess.run(["npm", "install"], cwd=FRONTEND_DIR, check=True)

    logger.info(f"Starting Vue dev server on http://{FRONTEND_HOST}:{port}...")
    subprocess.Popen(
        ["npm", "run", "dev", "--", "--host", FRONTEND_HOST, "--port", str(port)],
        cwd=FRONTEND_DIR
    )
