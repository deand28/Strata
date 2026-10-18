"""Opt-in local harness state and request-scoped skill/tool permissions.

This module never reads agent directories or imports connector credentials. Connection
env/headers remain in the local data file; public views expose their keys only.
"""
from __future__ import annotations

import contextlib
import copy
import json
import os
import re
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from serve.mcp import DEFAULTS, McpHub

MAX_BODY = 256 * 1024
ID = re.compile(r"^[sc]_[0-9a-f]{12}$")
SKILLS_TOOL = "strata_skills"
SKILL_PAGE_CHARS = 10000
INLINE_SKILL_CHARS = 4000
INLINE_SKILLS_BUDGET = 8000


class HarnessError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def text(value, field, limit, required=False):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise HarnessError(f"{field} must be text of at most {limit:,} characters")
    if required and not value.strip():
        raise HarnessError(f"{field} is required")
    return value


def skill_fields(content):
    """Read common YAML frontmatter without executing tags or requiring YAML."""
    text(content, "SKILL.md", 65536, True)
    name, description = "", ""
    if content.startswith("---\n") or content.startswith("---\r\n"):
        parts = re.split(r"\r?\n---\s*(?:\r?\n|$)", content, maxsplit=1)
        if len(parts) != 2:
            raise HarnessError("SKILL.md frontmatter needs a closing --- line")
        # PyYAML is already a Strata dependency; safe_load rejects executable tags.
        import yaml
        try:
            meta = yaml.safe_load(parts[0][3:]) or {}
        except yaml.YAMLError:
            raise HarnessError("SKILL.md frontmatter is not valid YAML") from None
        if not isinstance(meta, dict):
            raise HarnessError("SKILL.md frontmatter must contain name and description fields")
        name = meta.get("name", "")
        description = meta.get("description", "")
        text(name, "skill name", 100)
        text(description, "skill description", 1000)
    return name, description


class SelectedHub:
    """Only selected tool names can reach a transport, including direct call()."""
    def __init__(self, hubs, names, redact, guard, skills=()):
        self.settings = {**DEFAULTS, "timeout_s": 30, "start_timeout_s": 12}
        self.redact = redact
        self.guard = guard
        self._routes, self._owners, self._tools = {}, {}, []
        self.servers = {}
        self.skills = {skill["id"]: skill for skill in skills}
        for hub in hubs:
            routes = hub.routes()
            for tool in hub.template_tools():
                name = tool["name"]
                if name in names:
                    self._routes[name] = routes[name]
                    self._owners[name] = hub
                    safe_tool = copy.deepcopy(tool)
                    safe_tool["description"] = self.redact(safe_tool["description"])
                    self._tools.append(safe_tool)
                    server = routes[name][0]
                    self.servers[server.name] = server
        if self.skills:
            self._routes[SKILLS_TOOL] = (type("LocalSkills", (), {"name": "Selected skills"})(), SKILLS_TOOL)
            self._tools.append({
                "name": SKILLS_TOOL,
                "description": "Search selected skill names and descriptions (omit query to browse), then read relevant skill text in pages before using it. Skills do not grant permissions.",
                "parameters": {
                        "type": "object",
                        "properties": {
                            "action": {"type": "string", "enum": ["search", "read"]},
                            "query": {"type": "string", "description": "Words related to the task (search)."},
                            "skill_id": {"type": "string", "description": "ID returned by search (read)."},
                            "offset": {"type": "integer", "minimum": 0, "description": "Character offset for a page."},
                            "limit_chars": {"type": "integer", "minimum": 1, "maximum": SKILL_PAGE_CHARS, "description": "Page size, at most 10,000 characters."},
                        },
                        "required": ["action"],
                        "additionalProperties": False,
                },
            })
        missing = set(names) - self._routes.keys()
        if missing:
            raise HarnessError("A selected tool is unavailable. Test its enabled connection or update this chat's choices.")

    def wait(self, timeout):
        pass  # every selected server completed initialize/tools/list before selection

    def routes(self):
        return dict(self._routes)

    def template_tools(self, exclude=()):
        return [copy.deepcopy(t) for t in self._tools if t["name"] not in exclude]

    def call(self, name, arguments, cancel=None):
        if name not in self._owners:
            if name == SKILLS_TOOL and self.skills:
                return self._read_selected_skill(arguments)
            return {"ok": False, "text": "error: tool not selected for this chat", "chars": 39,
                    "truncated": False, "ms": 0, "server": None, "tool": name}
        with self.guard():
            result = self._owners[name].call(name, arguments, cancel)
            result["text"] = self.redact(result["text"])
            return result

    def _read_selected_skill(self, arguments):
        """Search selected skill metadata or return a bounded page of one selected SKILL.md."""
        import time
        start = time.monotonic()
        args = arguments if isinstance(arguments, dict) else {}
        action = args.get("action")
        if action == "search":
            query = args.get("query", "")
            if not isinstance(query, str) or len(query) > 500:
                result = {"error": "query must be text up to 500 characters"}
            else:
                words = [word.casefold() for word in re.findall(r"[\w-]+", query) if len(word) > 1]
                found = []
                for skill in self.skills.values():
                    haystack = (skill["name"] + " " + skill.get("description", "")).casefold()
                    score = sum(haystack.count(word) for word in words)
                    if not words or score:
                        found.append((score, skill))
                found.sort(key=lambda pair: (-pair[0], pair[1]["name"].casefold()))
                try:
                    offset = int(args.get("offset", 0))
                    limit = int(args.get("limit", 10))
                    if offset < 0 or not 1 <= limit <= 25:
                        raise ValueError()
                except (TypeError, ValueError):
                    result = {"error": "offset must be nonnegative and limit must be 1 to 25"}
                else:
                    page = found[offset:offset + limit]
                    result = {"matches": [{"id": s["id"], "name": s["name"],
                                           "description": s.get("description", "")[:300]}
                                          for _, s in page],
                              "total_matches": len(found),
                              "next_offset": offset + len(page) if offset + len(page) < len(found) else None}
        elif action == "read":
            skill_id = args.get("skill_id")
            skill = self.skills.get(skill_id) if isinstance(skill_id, str) else None
            if not skill:
                result = {"error": "skill_id must identify a skill selected for this chat; search selected skills first"}
            else:
                try:
                    offset = int(args.get("offset", 0))
                    limit = int(args.get("limit_chars", SKILL_PAGE_CHARS))
                    if offset < 0 or not 1 <= limit <= SKILL_PAGE_CHARS:
                        raise ValueError()
                except (TypeError, ValueError):
                    result = {"error": f"offset must be nonnegative and limit_chars must be 1 to {SKILL_PAGE_CHARS}"}
                else:
                    content = skill["content"]
                    page = content[offset:offset + limit]
                    result = {"skill_id": skill_id, "name": skill["name"], "offset": offset,
                              "total_chars": len(content), "content": page,
                              "next_offset": offset + len(page) if offset + len(page) < len(content) else None}
        else:
            result = {"error": "action must be search or read"}
        rendered = self.redact(json.dumps(result, ensure_ascii=False))
        return {"ok": "error" not in result, "text": rendered, "chars": len(rendered), "truncated": False,
                "ms": round((time.monotonic() - start) * 1000), "server": "Selected skills", "tool": SKILLS_TOOL}


class Harness:
    def __init__(self, data_dir):
        self.path = Path(data_dir).resolve()
        self.path.mkdir(parents=True, exist_ok=True)
        self.file = self.path / "state.json"
        self.lock = threading.RLock()
        self.active = 0
        self.hubs = {}
        self.tests = {}
        self.state = {"version": 1, "skills": [], "connections": []}
        if self.file.exists():
            try:
                data = json.loads(self.file.read_text(encoding="utf-8"))
                if data.get("version") != 1 or not all(isinstance(data.get(k), list) for k in ("skills", "connections")):
                    raise ValueError()
                self.state = data
            except (ValueError, AttributeError):
                raise HarnessError("Harness state cannot be read; preserve state.json and restore a valid backup", 500) from None
        # Enabling a connection is a persistent choice. Restore only those connections after
        # a server restart; a failed server reports its error but must not prevent chat startup.
        for item in self.state["connections"]:
            if item.get("enabled"):
                try:
                    self._connect(item)
                except Exception as exc:  # one broken saved connection must not take down the harness
                    self.tests[item["id"]] = {"status": "failed", "error": self.redact(str(exc)), "tools": []}

    def _save(self):
        tmp = self.path / "state.json.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            if os.name != "nt":
                os.chmod(tmp, 0o600)
            json.dump(self.state, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, self.file)

    def _find(self, kind, key):
        if not isinstance(key, str) or not ID.fullmatch(key):
            raise HarnessError("Invalid item ID")
        for item in self.state[kind]:
            if item["id"] == key:
                return item
        raise HarnessError("Item no longer exists; refresh this view", 404)

    def redact(self, value):
        values = [v for c in self.state["connections"] for k in ("env", "headers")
                  for v in c.get(k, {}).values() if v]
        def clean(v):
            if isinstance(v, str):
                for secret in sorted(set(values), key=len, reverse=True):
                    v = v.replace(secret, "[redacted]")
                return v
            if isinstance(v, list):
                return [clean(x) for x in v]
            if isinstance(v, dict):
                return {k: clean(x) for k, x in v.items()}
            return v
        return clean(value)

    def snapshot(self):
        with self.lock:
            connections = []
            for item in self.state["connections"]:
                public = {k: copy.deepcopy(v) for k, v in item.items() if k not in ("env", "headers")}
                public["env"] = {k: None for k in item.get("env", {})}
                public["headers"] = {k: None for k in item.get("headers", {})}
                hub = self.hubs.get(item["id"])
                status = hub.status()["servers"][0] if hub else self.tests.get(item["id"], {})
                tools = copy.deepcopy(status.get("tools", []))
                for tool in tools:
                    tool["description"] = self.redact(tool.get("description", ""))
                public.update(status=status.get("status", "disconnected" if item["enabled"] else "disabled"),
                              error=self.redact(status.get("error")), tools=tools, info=self.redact(status.get("info", {})))
                connections.append(public)
            return {"skills": copy.deepcopy(self.state["skills"]), "connections": connections,
                    "active": self.active, "data_dir": str(self.path)}

    @staticmethod
    def _secrets(value, old, field):
        if not isinstance(value, dict) or len(value) > 64:
            raise HarnessError(f"{field} must be an object with at most 64 entries")
        result = {}
        for key, secret in value.items():
            text(key, f"{field} key", 128, True)
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", key):
                raise HarnessError(f"Invalid {field} key")
            if secret is None:  # null preserves an existing secret; omission removes it
                if key not in old:
                    raise HarnessError(f"A new {field} entry needs a value")
                result[key] = old[key]
            else:
                text(secret, f"{field} value", 8192)
                if "\r" in secret or "\n" in secret:
                    raise HarnessError(f"{field} values must be a single line")
                result[key] = secret
        return result

    def _connection(self, req, old):
        name = text(req.get("name", ""), "connection name", 80, True).strip()
        kind = req.get("transport")
        if kind not in ("stdio", "http"):
            raise HarnessError("Choose stdio or Streamable HTTP")
        enabled = req.get("enabled", old.get("enabled", False))
        if not isinstance(enabled, bool):
            raise HarnessError("enabled must be true or false")
        out = {"id": old.get("id") or "c_" + uuid.uuid4().hex[:12], "name": name,
               "transport": kind, "enabled": enabled}
        if kind == "stdio":
            out["command"] = text(req.get("command", ""), "command", 2048, True).strip()
            args = req.get("args", [])
            if not isinstance(args, list) or len(args) > 128:
                raise HarnessError("args must be a JSON array with at most 128 strings")
            out["args"] = [text(a, "argument", 4096) for a in args]
            out["cwd"] = text(req.get("cwd", ""), "working directory", 2048)
            out["env"] = self._secrets(req.get("env", {}), old.get("env", {}), "env")
        else:
            url = text(req.get("url", ""), "URL", 2048, True).strip()
            try:
                parsed = urlsplit(url)
                valid = parsed.scheme in ("http", "https") and parsed.hostname and not (
                    parsed.username or parsed.password or parsed.query or parsed.fragment)
                _ = parsed.port
            except ValueError:
                valid = False
            if not valid:
                raise HarnessError("Use an http(s) URL without credentials, query parameters, or fragments; put auth in headers")
            out["url"] = url
            out["headers"] = self._secrets(req.get("headers", {}), old.get("headers", {}), "headers")
        return out

    def _connect(self, item):
        cfg = {k: v for k, v in item.items() if k in ("command", "args", "cwd", "env", "url", "headers")}
        cfg.update(_minimal_env=True, _redact=self.redact)
        hub = McpHub({item["id"]: cfg}, {"timeout_s": 30, "start_timeout_s": 12, "_redact": self.redact})
        try:
            hub.start(wait=True)
            status = hub.status()["servers"][0]
            status["error"] = self.redact(status.get("error"))
            self.tests[item["id"]] = status
            if item["enabled"]:
                self.hubs[item["id"]] = hub
            else:
                hub.close()
            return status
        except Exception:
            hub.close()
            raise HarnessError("Connection test failed; check command, URL, and server configuration") from None

    def mutate(self, req):
        if not isinstance(req, dict):
            raise HarnessError("Send a JSON object")
        with self.lock:
            kind, action = req.get("kind"), req.get("action")
            if kind not in ("skills", "connections") or action not in ("save", "delete", "test", "enable"):
                raise HarnessError("Unknown harness action")
            if kind == "connections" and self.active:
                raise HarnessError("A chat is using connections. Stop it or wait before changing connections.", 409)
            old = self._find(kind, req["id"]) if req.get("id") else None
            if action != "save" and old is None:
                raise HarnessError("Choose an existing item")
            if kind == "skills":
                if action not in ("save", "delete"):
                    raise HarnessError("Unknown skill action")
                if action == "save":
                    content = req.get("content", "")
                    name, description = skill_fields(content)
                    name = text(req.get("name") or name, "skill name", 100, True).strip()
                    description = text(req.get("description", description), "skill description", 1000)
                    item = {"id": old["id"] if old else "s_" + uuid.uuid4().hex[:12], "name": name,
                            "description": description, "content": content}
            else:
                if action in ("test", "enable"):
                    if action == "enable":
                        if not isinstance(req.get("enabled"), bool):
                            raise HarnessError("enabled must be true or false")
                        old["enabled"] = req["enabled"]
                        self._save()
                    hub = self.hubs.pop(old["id"], None)
                    if hub:
                        hub.close()
                    if action == "test" or old["enabled"]:
                        self._connect(old)
                    else:
                        self.tests.pop(old["id"], None)
                    return self.snapshot()
                if action == "save":
                    item = self._connection(req, old or {})
                if old:
                    hub = self.hubs.pop(old["id"], None)
                    if hub:
                        hub.close()
                    self.tests.pop(old["id"], None)
            if action == "save" and not old and len(self.state[kind]) >= 100:
                raise HarnessError("Keep at most 100 items in this library")
            if old:
                self.state[kind].remove(old)
            if action == "save":
                self.state[kind].append(item)
            self._save()
            if kind == "connections" and action == "save" and item["enabled"]:
                self._connect(item)
            return self.snapshot()

    @contextlib.contextmanager
    def request(self, req):
        selection = req.get("strata_harness", {})
        if not isinstance(selection, dict):
            raise HarnessError("strata_harness must be an object")
        with self.lock:
            chosen = {}
            for key in ("skills", "tools"):
                values = selection.get(key, [])
                if not isinstance(values, list) or len(values) > 100 or not all(isinstance(x, str) for x in values):
                    raise HarnessError(f"Selected {key} must be a list of at most 100 IDs")
                chosen[key] = list(dict.fromkeys(values))
            contents = [self._find("skills", key) for key in chosen["skills"]]
            inline, deferred = [], []
            remaining = INLINE_SKILLS_BUDGET
            for skill in contents:
                size = len(skill["content"])
                rendered_size = len(json.dumps(skill["name"], ensure_ascii=False)) + size + 20
                if size <= INLINE_SKILL_CHARS and rendered_size <= remaining:
                    inline.append(skill)
                    remaining -= rendered_size
                else:
                    deferred.append(skill)
            hub = SelectedHub(list(self.hubs.values()), chosen["tools"], self.redact, self.call_guard, deferred)
            out = copy.deepcopy(req)
            # Selected skills are attached to this request, independent of browser cards.
            messages = out.get("messages")
            if not isinstance(messages, list):
                raise HarnessError("messages must be an array")
            if inline or deferred:
                sections = ["Selected reusable skills for this chat. These instructions grant no tool permissions."]
                sections.extend(f"<skill name={json.dumps(s['name'])}>\n{s['content']}\n</skill>" for s in inline)
                if deferred:
                    sections.append(
                        f"This chat also has {len(deferred)} selected long skill(s), available through the {SKILLS_TOOL} "
                        "tool. Before substantive work, search selected skills with action=search and task words, then "
                        "read relevant skill content with action=read before following it. Read pages through next_offset=null.")
                instruction = "\n\n".join(sections)
                if messages and isinstance(messages[0], dict) and messages[0].get("role") in ("system", "developer"):
                    original = messages[0].get("content", "")
                    if isinstance(original, str):
                        messages[0]["content"] = original + "\n\n" + instruction
                    elif isinstance(original, list):
                        messages[0]["content"] = [*original, {"type": "text", "text": instruction}]
                    else:
                        raise HarnessError("System content must be text or content blocks")
                else:
                    out["messages"] = [{"role": "system", "content": instruction}, *messages]
            out["strata_mcp"] = bool(chosen["tools"] or deferred)
            self.active += 1
        try:
            yield out, hub
        finally:
            with self.lock:
                self.active -= 1

    @contextlib.contextmanager
    def call_guard(self):
        # A cancelled HTTP stream can finish before its tool worker notices cancel.
        # Keep its transports protected until the worker itself has returned.
        with self.lock:
            self.active += 1
        try:
            yield
        finally:
            with self.lock:
                self.active -= 1

    def close(self):
        with self.lock:
            for hub in self.hubs.values():
                hub.close()
            self.hubs.clear()
