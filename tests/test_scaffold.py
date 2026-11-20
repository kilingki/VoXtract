"""Real HTTP smoke checks without additional testing dependencies."""

import json
import socket
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

import uvicorn

from backend.app.main import app


class ScaffoldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.sock = socket.socket()
        cls.sock.bind(("127.0.0.1", 0))
        cls.base_url = f"http://127.0.0.1:{cls.sock.getsockname()[1]}"
        cls.server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        cls.thread = threading.Thread(
            target=cls.server.run, kwargs={"sockets": [cls.sock]}, daemon=True
        )
        cls.thread.start()
        deadline = time.monotonic() + 5
        while not cls.server.started:
            if not cls.thread.is_alive() or time.monotonic() >= deadline:
                cls.server.should_exit = True
                cls.thread.join(timeout=5)
                cls.sock.close()
                raise RuntimeError("Smoke-test server did not start")
            time.sleep(0.01)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.should_exit = True
        cls.thread.join(timeout=5)
        cls.sock.close()
        if cls.thread.is_alive():
            raise RuntimeError("Smoke-test server did not stop")

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
