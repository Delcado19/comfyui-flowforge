import http.client
from pathlib import Path
import shutil
import socket
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from flowforge import gui


def make_local_test_dir() -> Path:
    root = Path.cwd() / ".test-tmp"
    root.mkdir(exist_ok=True)
    path = root / uuid.uuid4().hex
    path.mkdir()
    return path


def test_create_frontend_dist_server_skips_unavailable_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reserved:
        reserved.bind((gui.FRONTEND_HOST, 0))
        reserved.listen(1)
        preferred_port = reserved.getsockname()[1]

        server, selected_port = gui._create_frontend_dist_server(preferred_port, 9999)

    try:
        assert selected_port != preferred_port
    finally:
        server.server_close()


def test_select_frontend_dist_prefers_packaged_assets(monkeypatch):
    tmp_dir = make_local_test_dir()
    try:
        source_dist = tmp_dir / "source_dist"
        package_dist = tmp_dir / "package_dist"
        source_dist.mkdir()
        package_dist.mkdir()
        (source_dist / "index.html").write_text("source", encoding="utf-8")
        (package_dist / "index.html").write_text("package", encoding="utf-8")

        monkeypatch.setattr(gui, "FRONTEND_DIST", source_dist)
        monkeypatch.setattr(gui, "PACKAGE_FRONTEND_DIST", package_dist)

        assert gui._select_frontend_dist() == package_dist
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_frontend_dist_server_serves_index_and_proxies_health():
    tmp_dir = make_local_test_dir()
    dist = tmp_dir / "dist"
    try:
        dist.mkdir()
        (dist / "index.html").write_text("<h1>FlowForge</h1>", encoding="utf-8")

        class HealthHandler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):
                pass

            def do_GET(self):
                if self.path != "/health":
                    self.send_error(404)
                    return
                payload = b'{"status":"ok"}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        api_server = ThreadingHTTPServer((gui.API_HOST, 0), HealthHandler)
        api_thread = threading.Thread(target=api_server.serve_forever, daemon=True)
        api_thread.start()

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as suggested:
            suggested.bind((gui.FRONTEND_HOST, 0))
            frontend_preferred_port = suggested.getsockname()[1]

        frontend_server, frontend_port = gui._create_frontend_dist_server(
            frontend_preferred_port,
            api_server.server_port,
            dist,
        )
        frontend_thread = threading.Thread(target=frontend_server.serve_forever, daemon=True)
        frontend_thread.start()

        conn = http.client.HTTPConnection(gui.FRONTEND_HOST, frontend_port, timeout=5)
        conn.request("GET", "/")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
        conn.close()

        assert response.status == 200
        assert "FlowForge" in body

        conn = http.client.HTTPConnection(gui.FRONTEND_HOST, frontend_port, timeout=5)
        conn.request("GET", "/health")
        response = conn.getresponse()
        body = response.read().decode("utf-8")
        conn.close()

        assert response.status == 200
        assert body == '{"status":"ok"}'
    finally:
        if "frontend_server" in locals():
            frontend_server.shutdown()
            frontend_server.server_close()
        if "api_server" in locals():
            api_server.shutdown()
            api_server.server_close()
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_find_available_frontend_port_returns_bindable_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as suggested:
        suggested.bind((gui.FRONTEND_HOST, 0))
        preferred_port = suggested.getsockname()[1]

    selected_port = gui._find_available_frontend_port(preferred_port)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((gui.FRONTEND_HOST, selected_port))


def test_find_available_api_port_returns_bindable_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as suggested:
        suggested.bind((gui.API_HOST, 0))
        preferred_port = suggested.getsockname()[1]

    selected_port = gui._find_available_port(preferred_port, 5)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((gui.API_HOST, selected_port))


def test_run_frontend_dev_passes_selected_ports(monkeypatch):
    tmp_dir = make_local_test_dir()
    try:
        (tmp_dir / "node_modules").mkdir()
        monkeypatch.setattr(gui, "FRONTEND_DIR", tmp_dir)

        captured = {}

        def fake_popen(command, cwd, env):
            captured["command"] = command
            captured["cwd"] = cwd
            captured["env"] = env

        monkeypatch.setattr(subprocess, "Popen", fake_popen)

        gui._run_frontend_dev(5199, 8123)

        assert captured["command"] == [
            "npm",
            "run",
            "dev",
            "--",
            "--host",
            gui.FRONTEND_HOST,
            "--port",
            "5199",
        ]
        assert captured["cwd"] == tmp_dir
        assert captured["env"]["FLOWFORGE_API_PORT"] == "8123"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
