"""Explicit harness power state. Off is persistent and denies every wake path."""
from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from pathlib import Path


class PowerError(RuntimeError):
    def __init__(self, message, status=503):
        super().__init__(message)
        self.status = status


class ModelPower:
    def __init__(self, data_dir):
        self.file = Path(data_dir).resolve() / "power.json"
        self.enabled = True
        if self.file.exists():
            try:
                saved = json.loads(self.file.read_text(encoding="utf-8"))
                if saved.get("version") != 1 or not isinstance(saved.get("enabled"), bool):
                    raise ValueError()
                self.enabled = saved["enabled"]
            except (ValueError, AttributeError):
                raise PowerError("Cannot read local power.json; restore its saved power state before starting", 500) from None
        self.state = "on" if self.enabled else "off"
        self.lock = threading.RLock()
        self.active = 0
        self.error = None
        self.started = None
        self.loader = None
        self.svc = None

    def bind(self, svc):
        self.svc = svc
        svc.power = self
        if hasattr(svc.engine, "heal"):
            # Replica recovery can outlive the request that scheduled it. It needs
            # its own lease, including a fresh check if the thread starts after Off.
            svc.engine.power_admission = self.admit
        if svc.voice is not None:
            svc.voice.power = self

    def _save(self, enabled):
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as stream:
            if os.name != "nt":
                os.chmod(tmp, 0o600)
            json.dump({"version": 1, "enabled": enabled}, stream)
            stream.write("\n")
        os.replace(tmp, self.file)

    def _require_on(self):
        # Only the explicit On loader may load while the public state is Off/loading.
        if self.state == "loading" and self.loader == threading.get_ident():
            return
        if not self.enabled or self.state != "on":
            raise PowerError("Model power is off or changing. Turn it on in the sidebar before using chat or voice.")

    def require_on(self):
        with self.lock:
            self._require_on()

    @contextlib.contextmanager
    def admit(self):
        with self.lock:
            self._require_on()
            self.active += 1
        try:
            yield
        finally:
            with self.lock:
                self.active -= 1

    def status(self):
        with self.lock:
            return {"enabled": self.enabled, "state": self.state, "error": self.error,
                    "loaded": self.svc.loaded() if self.svc else False, "active": self.active,
                    "elapsed_s": round(time.monotonic() - self.started, 1) if self.started else 0}

    def off(self):
        with self.lock:
            if self.state in ("loading", "unloading") or self.active:
                raise PowerError("The model or voice is busy. Wait for the current work to finish, or stop it, then turn power off.", 409)
            voice = self.svc.voice
            if voice is not None and voice.busy():
                raise PowerError("Local speech is busy. Wait for the current spoken turn to finish, then turn power off.", 409)
            if not self.enabled and self.state == "off" and not self.error:
                return self.status()
            previous = self.enabled
            self.state, self.error = "unloading", None
            try:
                result = self.svc.unload()
                if result in ("busy", "unsupported"):
                    self.state = "on" if previous else "off"
                    raise PowerError("The model is busy; finish or stop its work before turning power off." if result == "busy"
                                     else "This engine does not support releasing model memory", 409)
                # unload() returns early when the engine was already down; the encoder
                # and Whisper may nevertheless still be resident.
                if result != "unloaded" and self.svc.vision is not None and hasattr(self.svc.vision, "unload"):
                    self.svc.vision.unload()
                if voice is not None:
                    voice.unload()
                self.enabled, self.state = False, "off"
                self._save(False)
            except PowerError:
                raise
            except Exception:
                # Even a failed unload/save must not permit an automatic reload.
                self.enabled, self.state = False, "off"
                self.error = "Power off could not release or save every resource. Retry before closing the server."
                with contextlib.suppress(OSError):
                    self._save(False)
                raise PowerError(self.error, 500) from None
            return self.status()

    def on(self):
        with self.lock:
            if self.state == "loading":
                return self.status()
            if self.state == "unloading" or self.active:
                raise PowerError("The model is busy. Try again when the current work finishes.", 409)
            if self.enabled and self.svc.loaded():
                return self.status()
            try:
                # A restart during loading must stay Off until On actually succeeds.
                self._save(False)
            except OSError:
                raise PowerError("Cannot save the local power state; check the harness data directory", 500) from None
            self.enabled, self.state, self.error = False, "loading", None
            self.started = time.monotonic()
            threading.Thread(target=self._load, daemon=True, name="harness-power-on").start()
            return self.status()

    def _load(self):
        with self.lock:
            self.loader = threading.get_ident()
        try:
            self.svc.load()
            if not self.svc.loaded():
                raise RuntimeError("engine did not become ready")
            with self.lock:
                self._save(True)
                self.enabled, self.state, self.error = True, "on", None
        except Exception:
            # There are no admitted user requests while loading. Reuse graceful
            # unload rather than leaving a failed/partial start resident.
            with contextlib.suppress(Exception):
                self.svc.unload()
            if self.svc.vision is not None:
                with contextlib.suppress(Exception):
                    self.svc.vision.unload()
            with self.lock:
                self.enabled, self.state = False, "off"
                self.error = "The model could not start. It remains off; check the model log, then retry Turn on."
                with contextlib.suppress(OSError):
                    self._save(False)
        finally:
            with self.lock:
                self.loader = None
                self.started = None
