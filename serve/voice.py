"""Optional local speech: lazy CPU faster-whisper and Windows System.Speech.

No request can select a model path, download a model, or write an audio file.
"""
from __future__ import annotations

import base64
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import threading
from pathlib import Path

MAX_AUDIO_BYTES = 6 * 1024 * 1024
MAX_JSON_BYTES = 9 * 1024 * 1024
MAX_SECONDS = 90
MAX_TEXT = 5000
RATE = 16000
SCRIPT = Path(__file__).with_name("voice_tts.ps1")


class VoiceError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def decode_audio(raw):
    """Decode browser WebM/Ogg/MP4/WAV in memory and bound decoded samples."""
    import av
    import numpy as np
    chunks, count = [], 0
    try:
        with av.open(io.BytesIO(raw)) as container:
            streams = list(container.streams.audio)
            if not streams:
                raise VoiceError("The recording contains no audio")
            resampler = av.AudioResampler(format="s16", layout="mono", rate=RATE)
            for frame in container.decode(streams[0]):
                for block in resampler.resample(frame):
                    samples = block.to_ndarray().reshape(-1)
                    count += len(samples)
                    if count > RATE * MAX_SECONDS:
                        raise VoiceError(f"Keep each spoken turn under {MAX_SECONDS} seconds", 413)
                    chunks.append(samples)
            for block in resampler.resample(None):
                samples = block.to_ndarray().reshape(-1)
                count += len(samples)
                if count > RATE * MAX_SECONDS:
                    raise VoiceError(f"Keep each spoken turn under {MAX_SECONDS} seconds", 413)
                chunks.append(samples)
    except VoiceError:
        raise
    except Exception:
        raise VoiceError("The recording could not be decoded. Try recording again in Chrome or Edge.") from None
    if count < RATE // 5:
        raise VoiceError("The recording was too short. Speak for a moment, then choose Finish turn.")
    return np.concatenate(chunks).astype(np.float32) / 32768.0


class LocalVoice:
    def __init__(self, config=None, *, model_factory=None, decoder=None, runner=None):
        config = config or {}
        model = config.get("model")
        self.model_path = Path(model).resolve() if isinstance(model, str) and model else None
        self.language = config.get("language", "en")
        if not isinstance(self.language, str) or len(self.language) > 16:
            raise VoiceError("voice.language must be a language code")
        threads = config.get("cpu_threads", 4)
        if isinstance(threads, bool) or not isinstance(threads, int) or not 1 <= threads <= 32:
            raise VoiceError("voice.cpu_threads must be between 1 and 32")
        self.threads = threads
        self.model_factory = model_factory
        self.decoder = decoder or decode_audio
        self.runner = runner or subprocess.run
        self.model = None
        self.power = None
        self.stt_lock, self.tts_lock = threading.Lock(), threading.Lock()
        self.state = "unloaded"
        self.error = None
        self._voices = None
        self.tts_error = None
        self.powershell = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"

    def stt_ready(self):
        if not self.model_path or not self.model_path.is_dir() or not (self.model_path / "model.bin").is_file():
            return False, "Local speech model is not configured. Set voice.model to an installed faster-whisper directory."
        if not self.model_factory and importlib.util.find_spec("faster_whisper") is None:
            return False, "Install requirements-voice.txt into the harness Python environment, then restart Strata."
        return True, None

    def _tts(self, payload, timeout=30):
        if not self.powershell.is_file() and self.runner is subprocess.run:
            raise VoiceError("Local Windows speech is unavailable on this computer", 503)
        try:
            result = self.runner([str(self.powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(SCRIPT)],
                                 input=json.dumps(payload, ensure_ascii=False), encoding="utf-8", capture_output=True,
                                 timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.TimeoutExpired):
            raise VoiceError("Windows speech did not respond. Try again or use chat without spoken replies.", 503) from None
        if result.returncode:
            raise VoiceError("Windows speech could not run. Check that System.Speech and an installed voice are available.", 503)
        return result.stdout.lstrip("\ufeff").strip()

    def voices(self):
        if self._voices is None:
            try:
                payload = json.loads(self._tts({"op": "voices"}, timeout=10))
                voices = payload.get("voices", [])
                self._voices = [{"name": v["name"], "language": v.get("language", "")} for v in voices
                                if isinstance(v, dict) and isinstance(v.get("name"), str)]
            except (VoiceError, ValueError, AttributeError):
                self._voices = []
                self.tts_error = "Local Windows speech is unavailable. You can still dictate with spoken replies off."
        return self._voices

    def status(self):
        ready, problem = self.stt_ready()
        voices = self.voices()
        return {"local": True, "max_seconds": MAX_SECONDS, "max_audio_bytes": MAX_AUDIO_BYTES,
                "stt": {"available": ready, "state": self.state if ready else "setup_required", "message": problem or self.error},
                "tts": {"available": bool(voices), "voices": voices, "message": self.tts_error}}

    def busy(self):
        return self.stt_lock.locked() or self.tts_lock.locked()

    def unload(self):
        if not self.stt_lock.acquire(blocking=False):
            raise VoiceError("Local transcription is busy", 409)
        try:
            if not self.tts_lock.acquire(blocking=False):
                raise VoiceError("Local speech is busy", 409)
            try:
                self.model = None
                self.state, self.error = "unloaded", None
                import gc
                gc.collect()
            finally:
                self.tts_lock.release()
        finally:
            self.stt_lock.release()

    def transcribe(self, request):
        with self.power.admit() if self.power else contextlib.nullcontext():
            return self._transcribe(request)

    def _transcribe(self, request):
        if not isinstance(request, dict) or not isinstance(request.get("audio"), str):
            raise VoiceError("Send the recorded audio as base64 JSON")
        encoded = request["audio"]
        if len(encoded) > ((MAX_AUDIO_BYTES + 2) // 3) * 4:
            raise VoiceError("The recording is too large. Use a shorter spoken turn.", 413)
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            raise VoiceError("The recording is not valid base64 audio") from None
        if not raw or len(raw) > MAX_AUDIO_BYTES:
            raise VoiceError("The recording is empty or too large", 413)
        ready, problem = self.stt_ready()
        if not ready:
            raise VoiceError(problem, 503)
        if not self.stt_lock.acquire(blocking=False):
            raise VoiceError("Another spoken turn is being transcribed. Try again when it finishes.", 409)
        try:
            self.state, self.error = "transcribing", None
            audio = self.decoder(raw)
            if len(audio) > RATE * MAX_SECONDS:
                raise VoiceError(f"Keep each spoken turn under {MAX_SECONDS} seconds", 413)
            if self.model is None:
                self.state = "loading"
                factory = self.model_factory
                if factory is None:
                    from faster_whisper import WhisperModel
                    factory = WhisperModel
                self.model = factory(str(self.model_path), device="cpu", compute_type="int8", cpu_threads=self.threads,
                                     local_files_only=True)
            self.state = "transcribing"
            segments, info = self.model.transcribe(audio, language=self.language or None, beam_size=1,
                                                   vad_filter=True, condition_on_previous_text=False)
            parts, size = [], 0
            for segment in segments:
                part = segment.text.strip()
                size += len(part)
                if size > 12000:
                    raise VoiceError("The transcript is too long. Use a shorter spoken turn.", 413)
                parts.append(part)
            return {"text": " ".join(parts).strip(), "language": getattr(info, "language", self.language),
                    "duration": round(len(audio) / RATE, 2)}
        except VoiceError:
            raise
        except Exception:
            self.error = "Local transcription failed. Check the installed model and try a shorter recording."
            raise VoiceError(self.error, 503) from None
        finally:
            self.state = "ready" if self.model is not None else "unloaded"
            self.stt_lock.release()

    def synthesize(self, request):
        with self.power.admit() if self.power else contextlib.nullcontext():
            return self._synthesize(request)

    def _synthesize(self, request):
        if not isinstance(request, dict):
            raise VoiceError("Send a JSON object")
        text, voice = request.get("text"), request.get("voice", "")
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT or "\x00" in text:
            raise VoiceError(f"Speech text must contain 1–{MAX_TEXT} characters")
        if not isinstance(voice, str) or len(voice) > 128:
            raise VoiceError("Choose an installed voice")
        available = self.voices()
        if not available:
            raise VoiceError(self.tts_error or "No local Windows voices are installed", 503)
        if voice and voice not in {v["name"] for v in available}:
            raise VoiceError("That Windows voice is no longer available. Choose another voice.")
        if not self.tts_lock.acquire(blocking=False):
            raise VoiceError("Local speech is busy. Try again when it finishes.", 409)
        try:
            encoded = self._tts({"op": "synthesize", "text": text, "voice": voice})
            if len(encoded) > 32 * 1024 * 1024:
                raise VoiceError("The speech output was too large. Use a shorter reply.", 413)
            try:
                wav = base64.b64decode(encoded, validate=True)
            except ValueError:
                raise VoiceError("Windows speech returned invalid audio", 503) from None
            if not wav.startswith(b"RIFF") or wav[8:12] != b"WAVE":
                raise VoiceError("Windows speech returned invalid audio", 503)
            return {"audio": encoded, "mime": "audio/wav"}
        finally:
            self.tts_lock.release()
