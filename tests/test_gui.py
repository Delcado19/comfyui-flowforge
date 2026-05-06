import socket

from flowforge import gui


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
