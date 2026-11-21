"""Real HTTP smoke checks without additional testing dependencies."""

import json
import socket
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError
from urllib.request import urlopen

import uvicorn

from backend.app.main import create_app


@contextmanager
def http_server(data_dir: Path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        base_url = f"http://127.0.0.1:{sock.getsockname()[1]}"
        server = uvicorn.Server(uvicorn.Config(create_app(data_dir), log_level="error"))
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [sock]}, daemon=True
        )
        thread.start()
        deadline = time.monotonic() + 5
        try:
            while not server.started:
                if not thread.is_alive() or time.monotonic() >= deadline:
                    raise RuntimeError("Test server did not start")
                time.sleep(0.01)
            yield base_url
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            if thread.is_alive():
                raise RuntimeError("Test server did not stop")


class ScaffoldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        root = cls.enterClassContext(TemporaryDirectory())
        cls.base_url = cls.enterClassContext(http_server(Path(root)))

    def test_health(self) -> None:
        with urlopen(self.base_url + "/api/health", timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(json.load(response), {"status": "ok"})

    def test_frontend(self) -> None:
        with urlopen(self.base_url + "/", timeout=3) as response:
            self.assertIn("text/html", response.headers["Content-Type"])
            self.assertIn("VoXtract", response.read().decode())
        for path, content_type in [
            ("/static/styles.css", "text/css"),
            ("/static/app.js", "javascript"),
        ]:
            with self.subTest(path=path):
                with urlopen(self.base_url + path, timeout=3) as response:
                    self.assertIn(content_type, response.headers["Content-Type"])

    def test_data_is_not_public(self) -> None:
        with self.assertRaises(HTTPError) as error:
            urlopen(self.base_url + "/data/.gitkeep", timeout=3)
        self.assertEqual(error.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
