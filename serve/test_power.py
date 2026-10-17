"""GPU-free explicit model power, persistence, admission and race proofs."""
import http.client
import json
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from serve.frontend import ChatTemplate
from serve.harness import Harness
from serve.power import ModelPower, PowerError
from serve.replicas import ReplicaEngine
from serve.server import ByteTokenizer, Service, serve
from serve.test_server import UnloadableEngine
from serve.voice import LocalVoice

ROOT = Path(__file__).resolve().parents[1]


def wait_ready(power):
    deadline = time.monotonic() + 5
    while power.status()["state"] == "loading" and time.monotonic() < deadline:
        time.sleep(0.01)
    return power.status()


class Power(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        tok = ByteTokenizer()
        self.engine = UnloadableEngine(tok, "</think>\n\nHello.", max_context=8192)
        self.svc = Service(self.engine, tok, ChatTemplate(ROOT / "serve/chat_template.jinja"))
        self.svc.harness = Harness(self.temp.name)
        self.svc.voice = LocalVoice({})
        self.svc.voice.model = object()
        self.svc.vision = SimpleNamespace(running=True, elastic=False)
        self.svc.vision.alive = lambda: self.svc.vision.running
        self.svc.vision.unload = lambda: setattr(self.svc.vision, "running", False)
        self.svc.vision.restart = lambda: setattr(self.svc.vision, "running", True)
        self.power = ModelPower(self.temp.name)
        self.power.bind(self.svc)
        self.httpd = None

    def tearDown(self):
        if self.httpd:
            self.httpd.shutdown(); self.httpd.server_close()
        wait_ready(self.power)
        self.svc.harness.close(); self.temp.cleanup()

    def request(self, path, data=None, headers=None):
        if self.httpd is None:
            self.httpd = serve(self.svc, port=0)
        connection = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=10)
        try:
            connection.request("GET" if data is None else "POST", path, body=None if data is None else json.dumps(data),
                               headers={"Content-Type": "application/json", **(headers or {})})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_off_releases_all_models_persists_and_on_restores(self):
        status = self.power.off()
        self.assertEqual(status["state"], "off")
        self.assertFalse(self.engine.alive())
        self.assertFalse(self.svc.vision.alive())
        self.assertIsNone(self.svc.voice.model)
        self.assertFalse(ModelPower(self.temp.name).enabled)
        self.assertEqual(self.power.on()["state"], "loading")
        self.assertEqual(wait_ready(self.power)["state"], "on")
        self.assertTrue(self.engine.alive()); self.assertTrue(self.svc.vision.alive())
        self.assertTrue(ModelPower(self.temp.name).enabled)
        self.assertEqual(self.engine.starts, 1)

    def test_off_also_frees_voice_and_vision_when_engine_was_already_unloaded(self):
        self.engine.unload()
        self.assertIsNotNone(self.svc.voice.model)
        self.assertEqual(self.power.off()["state"], "off")
        self.assertIsNone(self.svc.voice.model)
        self.assertFalse(self.svc.vision.alive())

    def test_direct_load_execution_and_voice_cannot_wake_off(self):
        self.svc.load()  # passed the old early-load check while the engine was alive
        self.power.off()
        operations = [self.svc.load, self.svc.ensure_loaded,
                      lambda: list(self.svc.run([1], False, None, 4, {}, threading.Event())),
                      lambda: self.svc.voice.transcribe({"audio": "YQ=="}),
                      lambda: self.svc.voice.synthesize({"text": "hello"})]
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaises(PowerError):
                operation()
        with self.assertRaises(PowerError):
            with self.svc._turn():
                self.fail("image turn admitted while off")
        self.assertEqual(self.engine.starts, 0)
        self.assertFalse(self.engine.alive())

    def test_busy_requests_fifo_and_voice_do_not_lose_work(self):
        with self.power.admit():
            with self.assertRaises(PowerError) as busy:
                self.power.off()
            self.assertEqual(busy.exception.status, 409)
        self.svc.fifo.acquire()
        try:
            with self.assertRaises(PowerError):
                self.power.off()
        finally:
            self.svc.fifo.release()
        self.svc.status["queued"] = 1
        try:
            with self.assertRaises(PowerError):
                self.power.off()
        finally:
            self.svc.status["queued"] = 0
        self.svc.voice.stt_lock.acquire()
        try:
            with self.assertRaises(PowerError):
                self.power.off()
        finally:
            self.svc.voice.stt_lock.release()
        self.assertTrue(self.engine.alive())
        self.assertTrue(self.power.enabled)
        self.assertIsNotNone(self.svc.voice.model)

    def test_loading_rejects_background_wakes_and_failure_stays_off(self):
        self.power.off()
        entered, finish = threading.Event(), threading.Event()
        original = self.engine.restart
        def blocked_restart():
            entered.set(); finish.wait(5); original()
        self.engine.restart = blocked_restart
        self.power.on(); self.assertTrue(entered.wait(3))
        try:
            with self.assertRaises(PowerError):
                self.svc.load()
            with self.assertRaises(PowerError) as busy:
                self.power.off()
            self.assertEqual(busy.exception.status, 409)
            self.assertFalse(ModelPower(self.temp.name).enabled)
        finally:
            finish.set()
        self.assertEqual(wait_ready(self.power)["state"], "on")
        self.power.off()
        self.engine.restart = Mock(side_effect=RuntimeError("private native error"))
        self.power.on()
        status = wait_ready(self.power)
        self.assertEqual(status["state"], "off")
        self.assertIn("could not start", status["error"])
        self.assertNotIn("private", status["error"])
        self.assertFalse(ModelPower(self.temp.name).enabled)
        self.assertFalse(self.svc.vision.alive())
        self.engine.restart = original
        self.power.on()
        recovered = wait_ready(self.power)
        self.assertEqual(recovered["state"], "on")
        self.assertIsNone(recovered["error"])

    def test_off_blocks_all_generation_protocols_load_and_speech(self):
        self.power.off()
        for path in ("/v1/chat/completions", "/v1/responses", "/v1/messages", "/v1/images/generations",
                     "/load", "/v1/load", "/voice/transcribe", "/voice/synthesize"):
            with self.subTest(path=path):
                code, body = self.request(path, {"messages": [{"role": "user", "content": "hello"}], "audio": "YQ==", "text": "hello"})
                self.assertEqual(code, 503, body)
                self.assertEqual(body["error"]["type"], "model_power")
        self.assertEqual(self.engine.starts, 0)
        self.assertEqual(self.request("/health")[0], 200)
        code, library = self.request("/harness", {"kind": "skills", "action": "save", "name": "Still editable", "content": "Use brief replies."})
        self.assertEqual(code, 200, library)
        self.assertEqual(len(library["skills"]), 1)
        self.assertEqual(self.request("/power")[1]["state"], "off")

    def test_power_endpoint_origin_auth_shape_and_restore(self):
        self.assertEqual(self.request("/power", {"enabled": False}, {"Origin": "https://foreign.example"})[0], 403)
        self.assertEqual(self.request("/power", {"enabled": False}, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("/power", {"enabled": "false"})[0], 400)
        self.svc.api_key = "test-only-key"
        self.assertEqual(self.request("/power", {"enabled": False})[0], 401)
        self.svc.api_key = ""
        self.assertEqual(self.request("/power", {"enabled": False})[1]["state"], "off")
        code, status = self.request("/power", {"enabled": True})
        self.assertIn(code, (200, 202)); self.assertIn(status["state"], ("loading", "on"))
        self.assertEqual(wait_ready(self.power)["state"], "on")
        self.assertEqual(self.request("/v1/chat/completions", {"messages": [{"role": "user", "content": "hello"}]})[0], 200)

    def test_request_between_early_load_and_fifo_prevents_off(self):
        # This was the critical race: load() can return well before generation takes
        # the FIFO. The whole HTTP request keeps power admitted across that gap.
        entered, finish = threading.Event(), threading.Event()
        original = self.svc.prepare
        def delayed_prepare(*args, **kwargs):
            entered.set(); finish.wait(5); return original(*args, **kwargs)
        self.svc.prepare = delayed_prepare
        result = []
        self.request("/power")  # start HTTP server before the worker
        worker = threading.Thread(target=lambda: result.append(self.request("/v1/chat/completions", {"messages": [{"role": "user", "content": "hello"}]})))
        worker.start(); self.assertTrue(entered.wait(3))
        try:
            code, body = self.request("/power", {"enabled": False})
            self.assertEqual(code, 409, body)
            self.assertTrue(self.engine.alive())
        finally:
            finish.set(); worker.join(5)
        self.assertEqual(result[0][0], 200)
        self.assertEqual(self.request("/power", {"enabled": False})[1]["state"], "off")

    def test_no_controller_preserves_upstream_automatic_reload(self):
        self.svc.power = None; self.svc.voice.power = None
        self.svc.unload(); self.svc.load()
        self.assertTrue(self.engine.alive())
        self.assertEqual(self.engine.starts, 1)
        self.assertEqual(self.request("/power")[0], 404)

    def test_off_survives_a_fresh_server_start_without_loading(self):
        self.power.off()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0)); port = sock.getsockname()[1]
        process = subprocess.Popen([sys.executable, "-m", "serve.server", "--engine", "mock", "--harness",
                                    "--port", str(port), "--harness-data-dir", self.temp.name], cwd=ROOT,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        status = None
        try:
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                try:
                    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
                    connection.request("GET", "/power")
                    response = connection.getresponse(); status = json.loads(response.read()); connection.close()
                    break
                except OSError:
                    time.sleep(0.05)
            self.assertIsNotNone(status)
            self.assertEqual(status["state"], "off"); self.assertFalse(status["loaded"])
        finally:
            process.terminate(); process.wait(timeout=5); process.stderr.close()

    def replicas(self):
        second = UnloadableEngine(self.svc.tok, "ok", max_context=8192)
        second.running, second.unloaded = False, False  # a crash, eligible for healing
        pool = ReplicaEngine([self.engine, second])
        self.svc.engine = pool; self.power.bind(self.svc)
        return pool, second

    def test_background_replica_healing_keeps_off_busy_until_finished(self):
        pool, second = self.replicas()
        entered, finish = threading.Event(), threading.Event()
        original = second.restart
        def restart():
            entered.set(); finish.wait(5); original()
        second.restart = restart
        pool.heal(); self.assertTrue(entered.wait(3))
        try:
            with self.assertRaises(PowerError) as busy:
                self.power.off()
            self.assertEqual(busy.exception.status, 409)
        finally:
            finish.set()
        deadline = time.monotonic() + 3
        while pool.healing.get(1) and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(self.power.off()["state"], "off")
        self.assertFalse(second.alive()); self.assertFalse(self.engine.alive())

    def test_replica_healer_scheduled_before_off_cannot_wake_after_off(self):
        pool, second = self.replicas()
        scheduled = []
        def delayed_thread(*args, **kwargs):
            scheduled.append(kwargs["target"])
            return SimpleNamespace(start=lambda: None)
        with patch("serve.replicas.threading.Thread", side_effect=delayed_thread):
            pool.heal()
        self.assertEqual(len(scheduled), 1)
        self.power.off()
        scheduled[0]()  # deterministic late scheduling after all engines unloaded
        self.assertFalse(second.alive()); self.assertEqual(second.starts, 0)
        self.assertFalse(self.engine.alive()); self.assertEqual(self.power.status()["state"], "off")


if __name__ == "__main__":
    unittest.main()
