# Personal Strata harness

This fork adds an opt-in workspace around Strata's existing Python server and plain
JavaScript chat. It keeps the original engine, streaming/Stop, attachments, Markdown
export, sampling controls, and monitor. MIT attribution and upstream links are retained.

The sidebar contains recent browser chats, Skills, Connections, Files, Monitor, and Model
settings. Each chat starts with zero selected skills and tools. Choices stay with that
chat. Model status shows the model name, context capacity, and whether it is loaded.

Optional [local voice conversation](VOICE.md) adds CPU speech recognition, Windows
spoken replies, pause detection, and interruption around the same chat.

Optional [local folder and document reading](FILES.md) adds explicit folder grants,
per-chat read-only tools and PDF, Office, RTF and structured/text attachments. File
management and local extraction also work while model power is Off.

## Reclaim model resources

Use the sidebar **Model power** switch to turn Strata Off when you are done. Off gracefully
unloads the main model, vision encoder, and loaded speech-recognition model, releasing
their GPU/model RAM while the HTTP server and interface remain available. Drafts,
browser history, skills, connection settings, and model configuration are retained.
The engine's native prompt cache is released; the next chat rebuilds it from its history.

Off is explicit and persistent. Chat, OpenAI/Responses/Anthropic generation, direct load
endpoints, image generation, and voice requests cannot wake the model. Send and voice
Start are disabled. Skill/connection management still works; enabled MCP server processes
are not stopped by this model switch. The ordinary temporary unload endpoint remains
available when power is On and retains its upstream auto-reload behavior.

If a generation, queue, tool round, or native speech job is busy, Off reports busy and
keeps that work running. The browser ends its microphone/playback session without
cancelling an active model answer. Wait for the work, or use its Stop control, then retry
Off. No process is force-killed by the switch during an active request.

**On** reloads the existing verified model configuration. The sidebar shows Loading and
elapsed time; loading can take a minute or more. Failed loading leaves power Off and
shows a retry message. Whisper remains lazy and loads only when voice is used again.

The ignored `.harness-data/power.json` stores `{"version":1,"enabled":false}` when Off.
This is read before engine/vision construction, so restarting the server does not wake
an Off model. A missing file preserves the normal initial On behavior. Do not delete
the file to reclaim resources; use the switch so loaded resources are released too.
The same data-directory option that holds skills and connections selects this state file.

`GET /power` reports state. `POST /power {"enabled":false}` requests Off;
`POST /power {"enabled":true}` starts a background On operation and returns Loading
until ready. Power changes use same-origin JSON, Host and API authentication checks.
These endpoints and admission guards exist only in harness mode. Without the harness,
upstream auto-loading and unload behavior are unchanged.

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
python -m unittest serve.test_files serve.test_power serve.test_replicas serve.test_voice serve.test_harness serve.test_mcp serve.test_web_sampling serve.test_frontend serve.test_server
python -m py_compile serve/files.py serve/extract_worker.py serve/power.py serve/replicas.py serve/voice.py serve/harness.py serve/server.py serve/mcp.py
node --check serve/web/app.js
node --check serve/web/harness.js
node --check serve/web/power.js
node serve/test_power_ui.js
node --check serve/web/files.js
node serve/test_files_ui.js
git diff --check
```

The harness suite uses temporary data directories and the repository's fake stdio and
HTTP MCP servers. It proves backend system-context injection, independent chat choices,
skill CRUD, disabled defaults, secret preservation/removal/redaction, selected tool
enforcement, real MCP calls and streamed activity, origin/body checks, and the unchanged
default HTML/health payload. It needs no GPU or model. The GitHub workflow runs these
checks on Windows and Linux without invoking setup or building an engine.

The power tests also cover persistent Off startup, model/vision/Whisper release,
On failure/retry behavior, all generation and speech wake paths, busy/FIFO protection,
and a request delayed between early load and generation admission.
They also prove a delayed background replica healer cannot restart after Off. A Node
test defers status responses to verify polling cannot overwrite a completed toggle or
re-enable Generate during an image job.

For browser review, check desktop and 390-pixel mobile layouts; import/edit/delete a
skill, select it, create a new empty chat and verify both counts reset; create/test/enable
a fixture connection, select one tool, and inspect activity during a model request.
Use a real model separately to verify instruction following, streaming, Stop, and images.

For a harmless stdio fixture in Connections, use the current Python executable as the
command and `["C:/absolute/path/to/Strata-harness/serve/mcp_fake_server.py"]` as its
argument array. Select only `echo` or `add` for a demonstration. The fixture also contains
intentional failure/crash/timeout tools for tests; those are not needed for ordinary use.
