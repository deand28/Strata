# Personal Strata harness

This fork adds an opt-in workspace around Strata's existing Python server and plain
JavaScript chat. It keeps the original engine, streaming/Stop, attachments, Markdown
export, sampling controls, and monitor. MIT attribution and upstream links are retained.

The sidebar contains recent browser chats, Skills, Connections, Monitor, and Model
settings. Each chat starts with zero selected skills and tools. Choices stay with that
chat. Model status shows the model name, context capacity, and whether it is loaded.

Optional [local voice conversation](VOICE.md) adds CPU speech recognition, Windows
spoken replies, pause detection, and interruption around the same chat.

## Start without a model

From the repository root, using Python with Strata's existing Python dependencies:

```powershell
python -m serve.server --engine mock --host 127.0.0.1 --port 18080 --harness
```

Open <http://127.0.0.1:18080/>. Mock mode uses no GPU, model downloads, or engine build.
The mock has scripted responses; it does not follow skill instructions by itself.
Tests inspect the actual prepared prompt and use scripts to exercise real MCP calls.

## Start with an installed model

Use a local, ignored copy of a verified Strata run config. Keep engine executable,
library, tokenizer, vision, and model paths absolute when reusing another installation.
Settings edits apply to this copy. Do not point a customizable harness at the original
installation's config if that installation should remain unchanged.

```powershell
python -m serve.server --engine strata --config strata-harness.json --host 127.0.0.1 --port 8080 --harness
```

Use this fork's own `.venv/Scripts/python.exe` on the current PC; it contains the core
Strata dependencies and optional local voice dependencies. Engine/CUDA library paths
still point to the working installation through the ignored run config. For a checkout
without its own environment, a compatible existing Strata environment can serve as a
fallback for text chat. Run commands from this fork's root.

Only run one real engine at a time on a 12 GB GPU. Stop the existing Strata server before
launching the harness with its model. Do not rerun setup, download the model again, or
recompile the engine to use this interface.

The same options can be set in the ignored run config, so the existing
`tools.strata_mcp` manager can start and stop it with its normal process identity checks:

```json
{
  "harness": true,
  "harness_data_dir": "C:/absolute/path/to/Strata-harness/.harness-data"
}
```

These fields are additions to a complete existing run config, not a replacement for it.
`--harness-data-dir PATH` overrides the config value. The default is `.harness-data`
inside this checkout, independent of the launch directory. Harness mode accepts only
loopback listen addresses. Without `--harness` or `"harness": true`, the upstream page,
health payload, MCP config handling, and ordinary chat behavior stay unchanged.

## Skills

Create a skill or import a `SKILL.md` in the browser. A skill is Markdown, optionally
with YAML frontmatter containing `name` and `description`. The library supports editing
and removal. Each skill is limited to 64 KiB of text.

Open **Skills & tools for this chat** above the composer to choose skills. The backend
loads the selected IDs and adds their complete instructions to the model's system
context. An existing first system or developer prompt keeps its role and content. Skills grant no
tools or filesystem access. Instructions must fit the model's context together with
the conversation and response; Strata reports an error if they do not fit.

## MCP connections and permissions

Connections support a local program over **stdio** or a **Streamable HTTP** URL.
Enter a command, a JSON argument array, optional working directory, and environment
variables for stdio; enter a URL and headers for HTTP. Credentials belong in environment
variables or headers. Commands, arguments, URLs, and names are visible configuration.
URLs containing embedded credentials, query parameters, or fragments are rejected.

New connections save disabled. **Test** starts the configured program or HTTP session,
performs initialize and tools/list, and reports the inventory and connection state.
It does not call a tool. A disabled test closes its session afterward. **Enable** connects
and leaves the transport available; **Disable**, edit, or remove closes it. After a server
restart, saved enabled connections show disconnected until **Test** or a fresh enable.

Enabling a server does not select its tools. Select individual tools above the composer
for each chat. The backend offers and executes only those names. Calls, arguments,
results, errors, and timing appear in the existing chat activity blocks. Connection
changes are rejected while a chat or its cancelled tool worker still uses a transport.

A local MCP process runs with the current OS account's rights. This harness is a
selection boundary, not an OS sandbox: configure trusted servers and their own scope
options, such as a filesystem server's allowed directory. The harness does not add a
shell or broad filesystem server by default. It passes ordinary OS/launcher environment
variables plus explicitly configured variables, excluding unrelated ambient provider
credentials. HTTP connections can contact the endpoint you configure.

No existing MCP config, Codex skill folder, connector, or credential is imported.
In harness mode, `mcp_servers`, `mcpServers`, and `--mcp-config` are not imported or
started. The management endpoint and harness chat tools require JSON and reject foreign
browser origins, including model-config trusted origins.

## Local data

`.harness-data/state.json` holds skills, connection settings, and connection secrets.
It is gitignored, written via atomic replacement, and restricted to the current user on
POSIX. On Windows it inherits the directory's normal user permissions. Secrets are
stored locally in plaintext, so keep this folder private and include it only in trusted
backups. It is not a credential vault.

API views return only environment/header key names with null values. In edits, null
preserves a saved value; a string replaces it; omitting its key removes it. The browser
clears credential form fields when the dialog closes and never puts connection secrets
in browser storage. Known configured values are redacted from connection errors and
tool result text. Structural tool IDs and schemas remain unchanged.

Chats and their selected IDs use `strata.harness.*` browser storage. The existing
`strata.chat` storage is preserved as the active chat. Existing behavior strips image
bytes and attached-file contents from saved conversations; reattach files after reload
or switching saved chats when those contents are needed. Markdown export remains on
the composer toolbar. Browser storage may have a size limit or be unavailable in private
mode; Strata's in-memory fallback still works for that session.

Do not commit `.harness-data`, local `strata-*.json`, `.env` files, model files, engine
binaries, or user skill content. Use a separate empty data directory for testing.

## Development and verification

The implementation is in `serve/harness.py`, with small opt-in hooks in `server.py`,
`mcp.py`, and `web/app.js`. `web/harness.js` and `web/harness.css` load only in harness
mode. No frontend dependency or build step is required.

```powershell
python -m unittest serve.test_harness serve.test_mcp serve.test_web_sampling serve.test_frontend serve.test_server
python -m py_compile serve/harness.py serve/server.py serve/mcp.py
node --check serve/web/app.js
node --check serve/web/harness.js
git diff --check
```

The harness suite uses temporary data directories and the repository's fake stdio and
HTTP MCP servers. It proves backend system-context injection, independent chat choices,
skill CRUD, disabled defaults, secret preservation/removal/redaction, selected tool
enforcement, real MCP calls and streamed activity, origin/body checks, and the unchanged
default HTML/health payload. It needs no GPU or model. The GitHub workflow runs these
checks on Windows and Linux without invoking setup or building an engine.

For browser review, check desktop and 390-pixel mobile layouts; import/edit/delete a
skill, select it, create a new empty chat and verify both counts reset; create/test/enable
a fixture connection, select one tool, and inspect activity during a model request.
Use a real model separately to verify instruction following, streaming, Stop, and images.

For a harmless stdio fixture in Connections, use the current Python executable as the
command and `["C:/absolute/path/to/Strata-harness/serve/mcp_fake_server.py"]` as its
argument array. Select only `echo` or `add` for a demonstration. The fixture also contains
intentional failure/crash/timeout tools for tests; those are not needed for ordinary use.
