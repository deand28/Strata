"""Local voice tests with injected STT and Windows speech; no model/GPU required."""
import base64
import http.client
import io
import json
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from serve.frontend import ChatTemplate
from serve.harness import Harness
from serve.server import ByteTokenizer, MockEngine, Service, serve
from serve.voice import LocalVoice, VoiceError, MAX_AUDIO_BYTES, MAX_JSON_BYTES, MAX_SECONDS, RATE, SCRIPT, decode_audio

ROOT = Path(__file__).resolve().parents[1]


def wav_audio(seconds=1):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(RATE)
        wav.writeframes(b"\0\0" * int(RATE * seconds))
    return output.getvalue()


class Local(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.model = Path(self.temp.name) / "model"
        self.model.mkdir(); (self.model / "model.bin").write_bytes(b"test fixture")
        self.engine = Mock()
        self.engine.transcribe.return_value = ([SimpleNamespace(text=" Hello local voice. ")], SimpleNamespace(language="en"))
        self.factory = Mock(return_value=self.engine)
        self.decoder = Mock(return_value=[0.0] * RATE)
        self.calls = []
        def runner(argv, **kwargs):
            self.calls.append((argv, kwargs))
            data = json.loads(kwargs["input"])
            result = json.dumps({"voices": [{"name": "Local Test", "language": "en-US"}]}) if data["op"] == "voices" else base64.b64encode(wav_audio()).decode()
            return SimpleNamespace(returncode=0, stdout=result, stderr="")
        self.v = LocalVoice({"model": str(self.model), "language": "en", "cpu_threads": 2},
                            model_factory=self.factory, decoder=self.decoder, runner=runner)

    def tearDown(self):
        self.temp.cleanup()

    def test_lazy_local_only_int8_model_and_transcript(self):
        self.assertTrue(self.v.status()["stt"]["available"])
        self.factory.assert_not_called()
        result = self.v.transcribe({"audio": base64.b64encode(b"recording").decode()})
        self.assertEqual(result["text"], "Hello local voice.")
        self.assertEqual(result["duration"], 1)
        self.factory.assert_called_once_with(str(self.model), device="cpu", compute_type="int8", cpu_threads=2, local_files_only=True)
        options = self.engine.transcribe.call_args.kwargs
        self.assertTrue(options["vad_filter"])
        self.assertFalse(options["condition_on_previous_text"])
        self.v.transcribe({"audio": "YQ=="})
        self.assertEqual(self.factory.call_count, 1)

    def test_silence_returns_empty_and_no_files_are_retained(self):
        before = sorted(str(p) for p in Path(self.temp.name).rglob("*"))
        self.engine.transcribe.return_value = ([], SimpleNamespace(language="en"))
        self.assertEqual(self.v.transcribe({"audio": "YQ=="})["text"], "")
        self.v.synthesize({"text": "Local reply", "voice": "Local Test"})
        self.assertEqual(before, sorted(str(p) for p in Path(self.temp.name).rglob("*")))

    def test_recording_validation_duration_and_busy(self):
        for request in ([], {}, {"audio": 4}, {"audio": "@@@"}, {"audio": ""}):
            with self.subTest(request=request), self.assertRaises(VoiceError):
                self.v.transcribe(request)
        with self.assertRaises(VoiceError) as limit:
            self.v.transcribe({"audio": "A" * (MAX_AUDIO_BYTES * 2)})
        self.assertEqual(limit.exception.status, 413)
        self.decoder.return_value = range(RATE * MAX_SECONDS + 1)
        with self.assertRaises(VoiceError) as duration:
            self.v.transcribe({"audio": "YQ=="})
        self.assertEqual(duration.exception.status, 413)
        self.v.stt_lock.acquire()
        try:
            with self.assertRaises(VoiceError) as busy:
                self.v.transcribe({"audio": "YQ=="})
            self.assertEqual(busy.exception.status, 409)
        finally:
            self.v.stt_lock.release()

    def test_model_failure_releases_ownership_and_reports_setup(self):
        self.factory.side_effect = RuntimeError("internal details must not leak")
        with self.assertRaises(VoiceError) as error:
            self.v.transcribe({"audio": "YQ=="})
        self.assertEqual(error.exception.status, 503)
        self.assertNotIn("internal details", str(error.exception))
        self.assertFalse(self.v.stt_lock.locked())
        unconfigured = LocalVoice({}, runner=self.v.runner)
        self.assertFalse(unconfigured.status()["stt"]["available"])

    def test_tts_fixed_script_and_stdin_boundary(self):
        text = "Hello $(Write-Output HACKED); `quoted` \"text\" and café"
        output = self.v.synthesize({"text": text, "voice": "Local Test"})
        self.assertEqual(output["mime"], "audio/wav")
        argv, kwargs = self.calls[-1]
        self.assertEqual(argv[1:], ["-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(SCRIPT)])
        self.assertNotIn(text, " ".join(argv))
        self.assertEqual(json.loads(kwargs["input"])["text"], text)
        self.assertEqual(kwargs["timeout"], 30)
        self.assertNotIn("shell", kwargs)
        for request in ({"text": ""}, {"text": "x" * 5001}, {"text": "ok", "voice": "unknown"}, {"text": None}):
            with self.subTest(request=str(request)[:30]), self.assertRaises(VoiceError):
                self.v.synthesize(request)
        self.v.runner = Mock(side_effect=subprocess.TimeoutExpired("speech", 30))
        with self.assertRaises(VoiceError) as timeout:
            self.v.synthesize({"text": "hello"})
        self.assertEqual(timeout.exception.status, 503)
        self.assertFalse(self.v.tts_lock.locked())

    def test_wav_decoder_and_duration_cap(self):
        try:
            import av  # optional dependency; CI tests injected STT without it
        except ImportError:
            self.skipTest("optional PyAV is not installed")
        self.assertEqual(len(decode_audio(wav_audio())), RATE)
        with self.assertRaises(VoiceError):
            decode_audio(b"not a recording")
        with self.assertRaises(VoiceError) as duration:
            decode_audio(wav_audio(MAX_SECONDS + 1))
        self.assertEqual(duration.exception.status, 413)


class Http(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        tok = ByteTokenizer()
        self.svc = Service(MockEngine(tok, "done"), tok, ChatTemplate(ROOT / "serve/chat_template.jinja"))
        self.harness = Harness(self.temp.name)
        self.svc.harness = self.harness
        self.svc.voice = SimpleNamespace(status=lambda: {"local": True}, transcribe=lambda r: {"text": "hello"},
                                         synthesize=lambda r: {"mime": "audio/wav", "audio": "fixture"})
        self.httpd = serve(self.svc, port=0)
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close(); self.harness.close(); self.temp.cleanup()

    def request(self, path, data=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request("GET" if data is None else "POST", path, body=data,
                         headers={"Content-Type": "application/json", **(headers or {})})
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def test_harness_only_same_origin_auth_and_malformed_limits(self):
        self.assertEqual(self.request("/voice/status")[0], 200)
        self.assertEqual(self.request("/voice/transcribe", b"{}", {"Origin": "https://foreign.example"})[0], 403)
        self.assertEqual(self.request("/voice/transcribe", b"{}", {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.request("/voice/transcribe", b"not-json")[0], 400)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.putrequest("POST", "/voice/transcribe"); conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(MAX_JSON_BYTES + 1)); conn.endheaders()
            response = conn.getresponse(); self.assertEqual(response.status, 413); response.read()
        finally:
            conn.close()
        self.svc.api_key = "test-only-key"
        self.assertEqual(self.request("/voice/status")[0], 401)
        self.svc.api_key = ""
        self.svc.harness = None
        self.assertEqual(self.request("/voice/status")[0], 404)
        self.assertEqual(self.request("/voice/transcribe", b"{}")[0], 404)


if __name__ == "__main__":
    unittest.main()
