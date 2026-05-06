"""
GUI launcher for ComfyUI FlowForge.
Starts the API server and opens the frontend in the default browser.
"""

import threading
import time
import webbrowser
from pathlib import Path
from .logger import setup_logger
from .api import create_app

logger = setup_logger(__name__)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
FRONTEND_DIST = FRONTEND_DIR / "dist"


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
        _serve_frontend_dist(frontend_port)
    else:
        logger.info("Frontend not built. Starting development server...")
        _run_frontend_dev(frontend_port)

    # Open browser
    url = f"http://localhost:{frontend_port}"
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
    web.run_app(app, port=port)


def _serve_frontend_dist(port: int):
    """Serve the built frontend files."""
    from http.server import HTTPServer, SimpleHTTPRequestHandler
    import os

    os.chdir(FRONTEND_DIST)

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, format, *args):
            pass  # Suppress logs

    server = HTTPServer(("localhost", port), Handler)
    logger.info(f"Frontend server running on http://localhost:{port}")
    server.serve_forever()


def _run_frontend_dev(port: int):
    """Run the Vue dev server."""
    import subprocess
    import sys

    if not (FRONTEND_DIR / "node_modules").exists():
        logger.info("Installing frontend dependencies...")
        subprocess.run(["npm", "install"], cwd=FRONTEND_DIR, check=True)

    logger.info(f"Starting Vue dev server on port {port}...")
    subprocess.Popen(
        ["npm", "run", "dev", "--", "--port", str(port)],
        cwd=FRONTEND_DIR
    )
