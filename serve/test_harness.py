"""GPU-free harness proofs: python -m unittest serve.test_harness -v."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from serve import mcp_fake_server as fake
from serve.frontend import ChatTemplate, openai_to_messages
from serve.harness import Harness, HarnessError
from serve.mcp import McpError
from serve.server import ByteTokenizer, Service, serve
from serve.test_mcp import ScriptedEngine, call_script

ROOT = Path(__file__).resolve().parents[1]
FAKE = str(ROOT / "serve/mcp_fake_server.py")


class Store(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.h = Harness(self.temp.name)

    def tearDown(self):
        self.h.close()
        self.temp.cleanup()

    def skill(self, content="Answer with PINEAPPLE exactly."):
        return self.h.mutate({"kind": "skills", "action": "save", "content": content, "name": "My skill"})["skills"][-1]

    def connection(self, **extra):
        req = {"kind": "connections", "action": "save", "name": "Fixture", "transport": "stdio",
               "command": sys.executable, "args": [FAKE], **extra}
        return self.h.mutate(req)["connections"][-1]

    def test_skill_crud_frontmatter_and_persistence(self):
        s = self.h.mutate({"kind": "skills", "action": "save", "content":
                           "---\nname: concise\ndescription: Keep replies brief\n---\nUse five words."})["skills"][0]
        self.assertEqual((s["name"], s["description"]), ("concise", "Keep replies brief"))
        self.h.mutate({"kind": "skills", "action": "save", "id": s["id"], "name": "Updated", "content": "Changed."})
        self.assertEqual(Harness(self.temp.name).snapshot()["skills"][0]["name"], "Updated")
        self.h.mutate({"kind": "skills", "action": "delete", "id": s["id"]})
        self.assertEqual(self.h.snapshot()["skills"], [])
        for content in ("", "---\nname: bad\n", "---\n- list\n---\nbody", "x" * 65537):
            with self.subTest(content=content[:20]), self.assertRaises(HarnessError):
                self.skill(content)
        with self.assertRaises(HarnessError):
            self.h.mutate({"kind": "skills", "action": "delete", "id": "../../private"})

    def test_skills_merge_into_original_system_and_are_request_scoped(self):
        s = self.skill()
        original = {"messages": [{"role": "system", "content": "Caller system stays system."},
                                 {"role": "user", "content": "hello"}], "strata_harness": {"skills": [s["id"]]}}
        with self.h.request(original) as (prepared, _):
            normalized, _, _ = openai_to_messages(prepared)
            self.assertEqual(normalized[0]["role"], "system")
            self.assertIn("Caller system stays system.", normalized[0]["content"])
            self.assertIn("PINEAPPLE", normalized[0]["content"])
            self.assertEqual(normalized[1]["role"], "user")
        self.assertNotIn("PINEAPPLE", original["messages"][0]["content"])
        developer = {**original, "messages": [{"role": "developer", "content": "Developer instruction."},
                                              {"role": "user", "content": "hello"}]}
        with self.h.request(developer) as (prepared, _):
            normalized, _, _ = openai_to_messages(prepared)
            self.assertEqual(normalized[0]["role"], "system")
            self.assertIn("Developer instruction.", normalized[0]["content"])
            self.assertIn("PINEAPPLE", normalized[0]["content"])
            self.assertEqual(normalized[1]["role"], "user")
        with self.h.request({"messages": original["messages"]}) as (other_chat, _):
            self.assertNotIn("PINEAPPLE", json.dumps(other_chat))
        with self.assertRaises(HarnessError):
            with self.h.request({"messages": [], "strata_harness": {"skills": ["s_000000000000"]}}):
                pass

    def test_connections_default_disabled_mask_preserve_remove_and_validate(self):
        c = self.connection(env={"API_TOKEN": "private-test-token"})
        self.assertFalse(c["enabled"])
        self.assertEqual(c["env"], {"API_TOKEN": None})
        self.assertEqual(self.h.hubs, {})
        update = {**c, "kind": "connections", "action": "save", "name": "Renamed"}
        self.h.mutate(update)
        self.assertEqual(self.h.state["connections"][0]["env"]["API_TOKEN"], "private-test-token")
        self.h.mutate({**update, "env": {}})
        self.assertEqual(self.h.state["connections"][0]["env"], {})
        http = self.connection(name="HTTP", transport="http", url="https://example.com/mcp", headers={"Authorization": "Bearer test-secret"})
        self.assertEqual(http["headers"], {"Authorization": None})
        self.assertNotIn("Bearer test-secret", json.dumps(self.h.snapshot()))
        for url in ("file:///private", "https://user:password@example.com/mcp", "https://example.com/mcp?key=secret"):
            with self.subTest(url=url), self.assertRaises(HarnessError):
                self.connection(transport="http", url=url)
        with self.assertRaises(HarnessError):
            self.connection(env={"NEW": None})
        self.h.mutate({"kind": "connections", "action": "delete", "id": c["id"]})
        self.assertEqual(len(self.h.snapshot()["connections"]), 1)

    def test_real_stdio_test_enable_selected_call_and_busy_guard(self):
        with patch("serve.harness.uuid.uuid4", return_value=SimpleNamespace(hex="123456abcdef00000000000000000000")):
            c = self.connection(env={"DEBUG": "1", "API_TOKEN": "test-credential-value"})
        tested = self.h.mutate({"kind": "connections", "action": "test", "id": c["id"]})["connections"][0]
        self.assertEqual(tested["status"], "ready")
        self.assertFalse(tested["enabled"])
        self.assertNotIn(c["id"], self.h.hubs)  # disabled tests always close their process
        with patch.dict(os.environ, {"UNRELATED_API_SECRET": "must-not-inherit"}), patch("serve.mcp.subprocess.Popen", wraps=subprocess.Popen) as popen:
            self.h.mutate({"kind": "connections", "action": "enable", "id": c["id"], "enabled": True})
            self.assertNotIn("UNRELATED_API_SECRET", popen.call_args.kwargs["env"])
            self.assertEqual(popen.call_args.kwargs["env"]["DEBUG"], "1")
            if os.name == "nt" and "SystemDrive" in os.environ:
                child_env = {k.lower(): v for k, v in popen.call_args.kwargs["env"].items()}
                self.assertEqual(child_env["systemdrive"], os.environ["SystemDrive"])
        echo = c["id"] + "__echo"
        self.assertEqual(self.h.snapshot()["connections"][0]["id"], "c_123456abcdef")
        self.assertIn(echo, [t["name"] for t in self.h.snapshot()["connections"][0]["tools"]])
        with self.h.request({"messages": [], "strata_harness": {"tools": [echo]}}) as (_, hub):
            self.assertEqual([t["name"] for t in hub.template_tools()], [echo])
            self.assertEqual(hub.call(echo, {"text": "hello"})["text"], "hello")
            self.assertEqual(hub.call(echo, {"text": "test-credential-value"})["text"], "[redacted]")
            self.assertFalse(hub.call(c["id"] + "__die", {})["ok"])
            with self.assertRaises(HarnessError) as busy:
                self.h.mutate({"kind": "connections", "action": "delete", "id": c["id"]})
            self.assertEqual(busy.exception.status, 409)
        self.h.mutate({"kind": "connections", "action": "enable", "id": c["id"], "enabled": False})
        with self.assertRaises(HarnessError):
            with self.h.request({"messages": [], "strata_harness": {"tools": [echo]}}):
                pass

    def test_restart_restores_only_enabled_connections(self):
        enabled = self.connection(name="Persistent", enabled=True)
        disabled = self.connection(name="Dormant")
        self.h.close()
        with patch("serve.mcp.subprocess.Popen", wraps=subprocess.Popen) as popen:
            self.h = Harness(self.temp.name)
            self.assertEqual(popen.call_count, 1)
        connections = {c["id"]: c for c in self.h.snapshot()["connections"]}
        self.assertEqual(connections[enabled["id"]]["status"], "ready")
        self.assertEqual(connections[disabled["id"]]["status"], "disabled")
        tool = enabled["id"] + "__echo"
        with self.h.request({"messages": [], "strata_harness": {"tools": [tool]}}) as (_, hub):
            self.assertEqual(hub.call(tool, {"text": "restored"})["text"], "restored")

    def test_broken_saved_connection_does_not_block_other_connections(self):
        broken = self.connection(name="Broken", command="does-not-exist-strata-harness", enabled=True)
        working = self.connection(name="Working", enabled=True)
        self.h.close()
        self.h = Harness(self.temp.name)
        connections = {c["id"]: c for c in self.h.snapshot()["connections"]}
        self.assertEqual(connections[broken["id"]]["status"], "failed")
        self.assertEqual(connections[working["id"]]["status"], "ready")

    def test_error_and_log_redaction(self):
        c = self.connection(env={"API_TOKEN": "secret-error-value"})
        log = io.StringIO()
        with patch("serve.mcp.StdioTransport.start", side_effect=McpError("failure: secret-error-value")), contextlib.redirect_stdout(log):
            status = self.h.mutate({"kind": "connections", "action": "test", "id": c["id"]})
        self.assertNotIn("secret-error-value", json.dumps(status))
        self.assertNotIn("secret-error-value", log.getvalue())
        self.assertIn("[redacted]", status["connections"][0]["error"])

    def test_real_streamable_http_selected_call(self):
        httpd = fake.http_server(0)
        try:
            c = self.connection(transport="http", url=f"http://127.0.0.1:{httpd.server_address[1]}/mcp", enabled=True)
            self.assertEqual(c["status"], "ready")
            name = c["id"] + "__add"
            with self.h.request({"messages": [], "strata_harness": {"tools": [name]}}) as (_, hub):
                self.assertEqual(hub.call(name, {"a": 20, "b": 22})["text"], "42")
        finally:
            httpd.shutdown(); httpd.server_close()


class Http(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        tok = ByteTokenizer()
        self.engine = ScriptedEngine(tok, ["</think>\n\nDone."])
        self.svc = Service(self.engine, tok, ChatTemplate(ROOT / "serve/chat_template.jinja"))
        self.h = Harness(self.temp.name)
        self.svc.harness = self.h
        self.httpd = serve(self.svc, port=0)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close()
        self.h.close(); self.temp.cleanup()

    def call(self, path, body=None, headers=None):
        req = urllib.request.Request(self.base + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            with e:
                return e.code, e.read().decode()

    def test_skill_reaches_engine_prompt_and_other_chat_does_not_inherit(self):
        code, raw = self.call("/harness", {"kind": "skills", "action": "save", "name": "Fruit", "content": "Use PINEAPPLE."})
        self.assertEqual(code, 200, raw)
        sid = json.loads(raw)["skills"][0]["id"]
        req = {"messages": [{"role": "system", "content": "Caller system."}, {"role": "user", "content": "hello"}],
               "strata_harness": {"skills": [sid]}, "stream": False}
        self.assertEqual(self.call("/v1/chat/completions", req)[0], 200)
        self.assertIn("Use PINEAPPLE.", self.engine.prompt_text(0))
        self.assertIn("Caller system.", self.engine.prompt_text(0))
        self.assertEqual(self.call("/v1/chat/completions", {**req, "strata_harness": {}})[0], 200)
        self.assertNotIn("PINEAPPLE", self.engine.prompt_text(1))

    def test_actual_tool_loop_stream_activity_and_unselected_tool_absent(self):
        c = self.h.mutate({"kind": "connections", "action": "save", "name": "Fixture", "transport": "stdio",
                           "command": sys.executable, "args": [FAKE], "enabled": True})["connections"][0]
        echo = c["id"] + "__echo"
        tok = self.svc.tok
        self.engine = ScriptedEngine(tok, [call_script(echo, text="proof from a real tool"), "</think>\n\nTool complete."])
        self.svc.engine = self.engine
        code, raw = self.call("/v1/chat/completions", {"messages": [{"role": "user", "content": "echo"}],
                                                     "stream": True, "strata_harness": {"tools": [echo]}})
        self.assertEqual(code, 200, raw)
        events = [json.loads(line[6:])["strata_mcp"] for line in raw.splitlines()
                  if line.startswith("data: {") and "strata_mcp" in json.loads(line[6:])]
        self.assertEqual([e["event"] for e in events], ["start", "call", "result"])
        self.assertEqual(events[-1]["text"], "proof from a real tool")
        self.assertNotIn(c["id"] + "__die", self.engine.prompt_text(0))

    def test_same_origin_body_limit_and_off_default_payload(self):
        self.svc.trusted_origins = ["https://foreign.example"]
        body = {"kind": "skills", "action": "save", "name": "x", "content": "x"}
        self.assertEqual(self.call("/harness", body, {"Origin": "https://foreign.example"})[0], 403)
        self.assertEqual(self.call("/harness", body, {"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.call("/harness", {**body, "content": "x" * (256 * 1024)})[0], 413)
        self.assertEqual(self.call("/v1/chat/completions", {"messages": []}, {"Origin": "https://foreign.example"})[0], 403)
        self.assertTrue(json.loads(self.call("/health")[1])["harness"])
        self.assertIn("web/harness.js", self.call("/")[1])
        self.svc.harness = None
        health = json.loads(self.call("/health")[1])
        self.assertEqual(set(health), {"status", "max_context", "model", "images", "api_key", "image_generation", "loaded", "service"})
        self.assertEqual(self.call("/")[1], (ROOT / "serve/web/index.html").read_bytes().decode("utf-8"))
        self.assertEqual(self.call("/harness")[0], 404)


if __name__ == "__main__":
    unittest.main()
