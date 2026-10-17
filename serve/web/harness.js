// Loaded only by --harness. Reuses the upstream chat, streaming, attachments and monitor.
"use strict";
(() => {
  let library = {skills: [], connections: []};
  let view = "chat", operation = false;
  let chats = store.get("harness.chats", []);
  if (!Array.isArray(chats)) chats = [];
  let activeId = store.get("harness.active", "");
  let current = chats.find(c => c.id === activeId);
  if (!current) {
    current = {id: crypto.randomUUID(), title: "New chat", skills: [], tools: [], messages: store.get("chat", [])};
    chats.unshift(current);
    activeId = current.id;
  }
  current.skills = current.skills || [];
  current.tools = current.tools || [];
  document.title = "Strata · Personal harness";
  const sidebar = document.createElement("aside");
  sidebar.className = "h-sidebar";
  sidebar.setAttribute("aria-label", "Workspace");
  sidebar.innerHTML = `<a class="h-brand" href="#chat"><span class="h-mark">S</span><span>Strata<small>PERSONAL HARNESS</small></span></a>
    <button class="st-btn st-btn--primary h-new" id="h-new">${icon("new-chat")} New chat</button>
    <nav class="h-nav" aria-label="Workspace views">
      <button data-view="chat">${icon("chat")} Chat</button>
      <button data-view="skills">${icon("layers")} Skills <span id="h-skill-count">0</span></button>
      <button data-view="connections">${icon("link")} Connections <span id="h-connection-count">0</span></button>
    </nav>
    <div class="h-section-label">RECENT CHATS</div><div id="h-history" class="h-history"></div>
    <nav class="h-nav h-bottom" aria-label="Model controls">
      <button data-view="monitor">${icon("activity")} Monitor</button>
      <button data-view="about">${icon("settings")} Model settings</button>
    </nav>
    <div class="h-engine"><span class="h-engine-dot" id="h-engine-dot"></span><div><strong id="h-model">Connecting…</strong>
      <small id="h-model-info">Local engine</small></div><button class="st-btn st-btn--icon" id="h-health" aria-label="Refresh model status" title="Refresh model status">↻</button></div>`;
  document.body.prepend(sidebar);
  const title = document.createElement("div");
  title.className = "h-top-title";
  title.innerHTML = '<strong id="h-title">Chat</strong><span id="h-subtitle">Your local workspace</span>';
  document.querySelector(".st-header").prepend(title);
  const panel = document.createElement("section");
  panel.className = "view h-library";
  panel.hidden = true;
  panel.id = "h-library";
  document.querySelector("main").append(panel);
  const choices = document.createElement("details");
  choices.className = "h-choices";
  choices.innerHTML = '<summary id="h-choice-summary">Skills & tools for this chat</summary><div class="h-choice-body" id="h-choice-body"></div>';
  document.querySelector(".composer-wrap").prepend(choices);
  const footer = document.createElement("p");
  footer.className = "h-local-note";
  footer.textContent = "Model on this PC · Connections may contact services you configure.";
  document.querySelector(".composer-wrap").append(footer);
  const dialog = document.createElement("dialog");
  dialog.className = "h-dialog";
  document.body.append(dialog);
  dialog.addEventListener("close", () => { dialog.replaceChildren(); });

  function persist() {
    store.set("harness.chats", chats);
    store.set("harness.active", activeId);
  }
  function saveCurrent() {
    current.messages = store.get("chat", []);
    current.title = (messages.find(m => m.role === "user")?.text || "New chat").slice(0, 72);
    current.updated = Date.now();
    persist();
    renderHistory();
  }
  window.addEventListener("harness-chat-saved", saveCurrent);
  // Restore the active conversation using the same sanitised browser persistence
  // as the upstream single chat. Image bytes and attached-file contents stay transient.
  messages = current.messages || [];
  store.set("chat", messages);
  renderChat();
  function renderHistory() {
    $("h-history").innerHTML = chats.map(c => `<div class="h-history-row ${c.id === activeId ? "is-active" : ""}">
      <button data-chat="${esc(c.id)}" ${c.id === activeId ? 'aria-current="true"' : ""} title="${esc(c.title)}">${esc(c.title)}</button>
      <button class="h-delete-chat" data-delete-chat="${esc(c.id)}" aria-label="Delete ${esc(c.title)}" title="Delete chat">×</button></div>`).join("");
    $("h-history").querySelectorAll("[data-chat]").forEach(b => b.onclick = () => switchChat(b.dataset.chat));
    $("h-history").querySelectorAll("[data-delete-chat]").forEach(b => b.onclick = () => {
      if (busy) return toast("warn", "Stop the answer first");
      if (!confirm("Delete this saved chat from this browser?")) return;
      chats = chats.filter(c => c.id !== b.dataset.deleteChat);
      if (b.dataset.deleteChat === activeId) {
        if (chats.length) switchChat(chats[0].id); else newChat();
      }
      persist(); renderHistory();
    });
  }
  function switchChat(id) {
    window.dispatchEvent(new Event("harness-workspace-change"));
    if (busy) return toast("warn", "Stop the answer first");
    const next = chats.find(c => c.id === id);
    if (!next) return;
    current = next; activeId = id;
    current.skills = current.skills || []; current.tools = current.tools || [];
    messages = current.messages || [];
    attachments = []; renderAttachments();
    store.set("chat", messages); persist(); renderChat(); renderHistory(); renderChoices(); navigate("chat");
  }
  function newChat() {
    window.dispatchEvent(new Event("harness-workspace-change"));
    if (busy) return toast("warn", "Stop the answer first");
    if (!messages.length && !current.skills.length && !current.tools.length && chats.some(c => c.id === activeId)) return navigate("chat");
    const c = {id: crypto.randomUUID(), title: "New chat", skills: [], tools: [], messages: []};
    chats.unshift(c); switchChat(c.id);
  }
  $("h-new").onclick = newChat;
  $("new-btn").onclick = newChat;
  sidebar.querySelector(".h-brand").onclick = e => { e.preventDefault(); navigate("chat"); };
  sidebar.querySelectorAll("[data-view]").forEach(b => b.onclick = () => navigate(b.dataset.view));
  function navigate(name) {
    if (name !== "chat") window.dispatchEvent(new Event("harness-workspace-change"));
    view = name;
    const custom = name === "skills" || name === "connections";
    showTab(custom ? "chat" : name);
    panel.hidden = !custom;
    if (custom) { $("view-chat").hidden = true; renderLibrary(); }
    sidebar.querySelectorAll("[data-view]").forEach(b => b.setAttribute("aria-current", String(b.dataset.view === name)));
    $("h-title").textContent = {chat: "Chat", skills: "Skills", connections: "Connections", monitor: "Monitor", about: "Model settings"}[name] || "Chat";
    $("h-subtitle").textContent = custom ? "Make this workspace your own" : "Your local workspace";
  }
  window.harnessRequest = body => {
    delete body.strata_mcp;
    body.strata_harness = {skills: [...current.skills], tools: [...current.tools]};
  };

  async function api(body) {
    const r = await fetch("harness", {method: body ? "POST" : "GET", headers: headers(!!body),
      ...(body ? {body: JSON.stringify(body)} : {})});
    const result = await r.json();
    if (!r.ok) throw new Error(result.error?.message || `HTTP ${r.status}`);
    library = result;
    $("h-skill-count").textContent = library.skills.length;
    $("h-connection-count").textContent = library.connections.length;
    renderChoices();
    if (view === "skills" || view === "connections") renderLibrary();
    return result;
  }
  async function action(body) {
    if (operation) return;
    operation = true;
    panel.setAttribute("aria-busy", "true");
    try { await api(body); }
    catch (e) { toast("error", "Could not update workspace", e.message, 8000); }
    finally { operation = false; panel.removeAttribute("aria-busy"); }
  }
  function choose(kind, id, checked) {
    if (busy) { renderChoices(); return toast("warn", "Stop the answer before changing its choices"); }
    current[kind] = checked ? [...new Set([...current[kind], id])] : current[kind].filter(x => x !== id);
    persist(); renderChoices();
    if (view === "skills") renderLibrary();
  }
  function renderChoices() {
    const allTools = library.connections.flatMap(c => c.tools.map(t => ({...t, connection: c})));
    const missing = current.skills.filter(id => !library.skills.some(s => s.id === id)).length +
      current.tools.filter(id => !allTools.some(t => t.name === id && t.connection.enabled && t.connection.status === "ready")).length;
    $("h-choice-summary").textContent = `Skills & tools for this chat · ${current.skills.length} skills · ${current.tools.length} tools${missing ? " · review choices" : ""}`;
    const skillRows = library.skills.map(s => `<label class="h-check"><input type="checkbox" data-kind="skills" value="${esc(s.id)}" ${current.skills.includes(s.id) ? "checked" : ""} ${busy ? "disabled" : ""}><span><strong>${esc(s.name)}</strong><small>${esc(s.description || "Reusable model instructions")}</small></span></label>`).join("");
    const toolRows = library.connections.map(c => `<div class="h-tool-group"><strong>${esc(c.name)}</strong><span class="muted small"> · ${esc(c.status)}</span>${c.tools.map(t => {
      const ready = c.enabled && c.status === "ready";
      return `<label class="h-check"><input type="checkbox" data-kind="tools" value="${esc(t.name)}" ${current.tools.includes(t.name) ? "checked" : ""} ${busy || !ready ? "disabled" : ""}><span><strong>${esc(t.tool)}</strong><small>${esc(t.description || "No description provided")}</small></span></label>`;
    }).join("")}</div>`).join("");
    $("h-choice-body").innerHTML = `<p class="h-permission">Selected skills enter the model's instructions. Selected tools may run while it answers, with the access provided by their server.</p>
      ${missing ? '<p class="h-error">Some choices are unavailable. Reconnect their servers, or clear unavailable choices.</p><button class="st-btn st-btn--secondary" id="h-clear-stale">Clear unavailable choices</button>' : ""}
      <div class="h-choice-grid"><div><h3>Skills</h3>${skillRows || '<p class="muted">Add your first skill in Skills.</p>'}</div><div><h3>Tools</h3>${toolRows || '<p class="muted">Configure a connection, test it, then enable and choose tools here.</p>'}</div></div>`;
    $("h-choice-body").querySelectorAll("input[data-kind]").forEach(el => el.onchange = () => choose(el.dataset.kind, el.value, el.checked));
    const clear = $("h-clear-stale");
    if (clear) clear.onclick = () => {
      if (busy) return toast("warn", "Stop the answer first");
      current.skills = current.skills.filter(id => library.skills.some(s => s.id === id));
      current.tools = current.tools.filter(id => allTools.some(t => t.name === id && t.connection.enabled && t.connection.status === "ready"));
      persist(); renderChoices();
    };
  }
  function renderLibrary() {
    const skills = view === "skills";
    const items = skills ? library.skills : library.connections;
    panel.innerHTML = `<div class="h-library-inner"><div class="h-library-head"><div><span class="h-eyebrow">YOUR WORKSPACE</span><h1>${skills ? "Reusable instructions." : "Tools, on your terms."}</h1>
      <p>${skills ? "Teach the model a workflow, a writing style, or a way to approach a task." : "Connect the services you choose. Enable a connection, then select its tools per chat."}</p></div>
      <div class="h-actions"><button class="st-btn st-btn--secondary" id="h-refresh">Refresh</button>${skills ? '<button class="st-btn st-btn--secondary" id="h-import">Import SKILL.md</button><input type="file" id="h-import-file" accept=".md,text/markdown,text/plain" hidden>' : ""}<button class="st-btn st-btn--primary" id="h-add">${skills ? "Create skill" : "Add connection"}</button></div></div>
      ${!skills ? '<div class="h-notice">A stdio connection starts a local program with your account’s rights. Only add servers you trust. A test starts the server and reads its tools; it does not call a tool. Credentials belong in environment variables or headers.</div>' : ""}
      <div class="h-cards">${items.map(item => skills ? skillCard(item) : connectionCard(item)).join("") || `<div class="h-empty"><div class="h-empty-mark">${icon(skills ? "layers" : "link")}</div><h2>${skills ? "A place for your know-how" : "Your tools belong here"}</h2><p>${skills ? "Import a SKILL.md you already use, or write your first reusable instruction." : "Add a local stdio server or a Streamable HTTP endpoint. New connections start disabled."}</p></div>`}</div>
      <p class="h-storage-note">Skills and connection settings are saved locally on this PC. Chats are saved in this browser. <span title="Local data folder">${esc(library.data_dir || "")}</span></p></div>`;
    $("h-add").onclick = () => skills ? skillForm() : connectionForm();
    $("h-refresh").onclick = () => action();
    if (skills) {
      $("h-import").onclick = () => $("h-import-file").click();
      $("h-import-file").onchange = async e => {
        const f = e.target.files[0];
        if (!f) return;
        if (f.size > 65536) return toast("error", "Skill too large", "Use a SKILL.md smaller than 64 KiB.");
        try { skillForm({content: await f.text()}); } catch (err) { toast("error", "Could not read file", err.message); }
      };
    }
    panel.querySelectorAll("[data-edit]").forEach(b => b.onclick = () => {
      const item = items.find(s => s.id === b.dataset.edit);
      skills ? skillForm(item) : connectionForm(item);
    });
    panel.querySelectorAll("[data-remove]").forEach(b => b.onclick = () => {
      if (confirm(`Remove this ${skills ? "skill" : "connection"}?`)) action({kind: skills ? "skills" : "connections", action: "delete", id: b.dataset.remove});
    });
    panel.querySelectorAll("[data-use]").forEach(b => b.onclick = () => choose("skills", b.dataset.use, !current.skills.includes(b.dataset.use)));
    panel.querySelectorAll("[data-test]").forEach(b => b.onclick = async () => {
      b.disabled = true; b.textContent = "Testing…";
      await action({kind: "connections", action: "test", id: b.dataset.test});
    });
    panel.querySelectorAll("[data-enable]").forEach(b => b.onclick = () => {
      const item = library.connections.find(c => c.id === b.dataset.enable);
      action({kind: "connections", action: "enable", id: item.id, enabled: !item.enabled});
    });
  }
  function skillCard(s) {
    return `<article class="h-card"><div class="h-card-top"><span class="h-card-icon">${icon("layers")}</span><span class="h-tag">SKILL.md</span></div><h2>${esc(s.name)}</h2><p>${esc(s.description || "Reusable instructions for the model.")}</p><small class="muted">${fmt(s.content.length)} characters</small><div class="h-card-actions"><button class="st-btn st-btn--secondary" data-use="${esc(s.id)}" aria-pressed="${current.skills.includes(s.id)}">${current.skills.includes(s.id) ? "✓ Selected for chat" : "Use in this chat"}</button><button class="st-btn st-btn--secondary" data-edit="${esc(s.id)}">Edit</button><button class="st-btn st-btn--icon" data-remove="${esc(s.id)}" aria-label="Remove ${esc(s.name)}" title="Remove skill">×</button></div></article>`;
  }
  function connectionCard(c) {
    return `<article class="h-card"><div class="h-card-top"><span class="h-card-icon">${icon("link")}</span><span class="h-tag" data-status="${esc(c.status)}">${esc(c.status)}</span></div><h2>${esc(c.name)}</h2><p class="h-endpoint">${esc(c.transport === "http" ? c.url : c.command)}</p><small class="muted">${c.transport === "http" ? "Streamable HTTP" : "Local stdio"} · ${c.tools.length} tools · ${c.enabled ? "enabled" : "disabled"}</small>${c.error ? `<p class="h-error">${esc(c.error)}</p>` : ""}<details class="h-inventory"><summary>Tool inventory (${c.tools.length})</summary>${c.tools.map(t => `<p><strong>${esc(t.tool)}</strong><br><span class="muted">${esc(t.description)}</span></p>`).join("") || '<p class="muted">Test the connection to read its tools.</p>'}</details><div class="h-card-actions"><button class="st-btn st-btn--secondary" data-test="${esc(c.id)}">Test</button><button class="st-btn st-btn--secondary" data-enable="${esc(c.id)}" aria-pressed="${c.enabled}">${c.enabled ? "Disable" : "Enable"}</button><button class="st-btn st-btn--secondary" data-edit="${esc(c.id)}">Edit</button><button class="st-btn st-btn--icon" data-remove="${esc(c.id)}" aria-label="Remove ${esc(c.name)}" title="Remove connection">×</button></div></article>`;
  }
  function openForm(title, contents) {
    dialog.innerHTML = `<form id="h-form"><div class="h-dialog-head"><h2>${esc(title)}</h2><button type="button" class="st-btn st-btn--icon" id="h-cancel-x" aria-label="Close">×</button></div>${contents}<p id="h-form-error" class="h-error" role="alert"></p><div class="h-dialog-actions"><button type="button" class="st-btn st-btn--secondary" id="h-cancel">Cancel</button><button class="st-btn st-btn--primary" id="h-save">Save</button></div></form>`;
    $("h-cancel").onclick = $("h-cancel-x").onclick = () => dialog.close();
    dialog.showModal();
  }
  async function submit(body) {
    $("h-save").disabled = true;
    $("h-form-error").textContent = "";
    try { await api(body); dialog.close(); }
    catch (e) { $("h-form-error").textContent = e.message; $("h-save").disabled = false; }
  }
  function skillForm(item = {}) {
    openForm(item.id ? "Edit skill" : "Create a skill", `<p class="muted">Selected skills become model instructions. They do not grant access to tools or files.</p><label class="st-field">Name <small class="muted">Optional when frontmatter includes a name</small><input class="st-input" id="h-skill-name" maxlength="100" value="${esc(item.name || "")}"></label><label class="st-field">Description <input class="st-input" id="h-skill-description" maxlength="1000" value="${esc(item.description || "")}"></label><label class="st-field">SKILL.md<textarea class="st-input h-code" id="h-skill-content" rows="13" required maxlength="65536" placeholder="---&#10;name: my-workflow&#10;description: When to use this skill&#10;---&#10;Write your instructions here.">${esc(item.content || "")}</textarea></label>`);
    $("h-form").onsubmit = e => {
      e.preventDefault();
      const body = {kind: "skills", action: "save", id: item.id, name: $("h-skill-name").value, content: $("h-skill-content").value};
      if (item.id || $("h-skill-description").value) body.description = $("h-skill-description").value;
      submit(body);
    };
  }
  function connectionForm(item = {}) {
    openForm(item.id ? "Edit connection" : "Add a connection", `<p class="muted">Save configuration first, then test and enable it. Commands, arguments and URLs are visible; use environment variables or headers for credentials.</p><label class="st-field">Name<input class="st-input" id="h-conn-name" required maxlength="80" value="${esc(item.name || "")}"></label><label class="st-field">Transport<select class="st-input" id="h-transport"><option value="stdio">Local program · stdio</option><option value="http">Streamable HTTP</option></select></label><div id="h-stdio-fields"><label class="st-field">Command<input class="st-input h-code" id="h-command" value="${esc(item.command || "")}" placeholder="e.g. npx or an absolute executable path"></label><label class="st-field">Arguments (JSON array)<textarea class="st-input h-code" id="h-args" rows="3">${esc(JSON.stringify(item.args || [], null, 2))}</textarea></label><label class="st-field">Working directory (optional)<input class="st-input h-code" id="h-cwd" value="${esc(item.cwd || "")}"></label></div><div id="h-http-fields"><label class="st-field">Endpoint URL<input class="st-input h-code" id="h-url" type="url" value="${esc(item.url || "")}" placeholder="https://example.com/mcp"></label></div><div class="h-secret-head"><strong id="h-secret-label">Environment variables</strong><button type="button" class="st-btn st-btn--secondary" id="h-add-secret">Add field</button></div><p class="small muted">Saved values are hidden. Leave a saved value blank to keep it; remove its row to delete it.</p><div id="h-secrets"></div><p class="h-notice">Enabling or testing a local server starts its program with your account’s rights. Select only the tools you intend to allow in each chat.</p>`);
    $("h-transport").value = item.transport || "stdio";
    const rowsByKind = {stdio: Object.entries(item.env || {}).map(([key]) => ({key, saved: true})), http: Object.entries(item.headers || {}).map(([key]) => ({key, saved: true}))};
    let previous = $("h-transport").value;
    function capture() {
      rowsByKind[previous] = [...$("h-secrets").children].map(row => ({key: row.querySelector(".h-secret-key").value, value: row.querySelector(".h-secret-value").value, saved: row.dataset.saved === "true"}));
    }
    function addRow(row = {}) {
      const el = document.createElement("div"); el.className = "h-secret-row"; el.dataset.saved = String(!!row.saved);
      el.innerHTML = `<input class="st-input h-secret-key" aria-label="Credential field name" placeholder="${previous === "stdio" ? "VARIABLE_NAME" : "Authorization"}" value="${esc(row.key || "")}"><input class="st-input h-secret-value" aria-label="Credential value" type="password" autocomplete="new-password" placeholder="${row.saved ? "Saved value · blank keeps it" : "Value"}" value="${esc(row.value || "")}"><button type="button" class="st-btn st-btn--icon" aria-label="Remove credential field">×</button>`;
      el.querySelector("button").onclick = () => el.remove(); $("h-secrets").append(el);
    }
    function transport() {
      const kind = $("h-transport").value;
      $("h-stdio-fields").hidden = kind !== "stdio"; $("h-http-fields").hidden = kind !== "http";
      $("h-command").required = kind === "stdio"; $("h-url").required = kind === "http";
      $("h-secret-label").textContent = kind === "stdio" ? "Environment variables" : "Headers";
      $("h-secrets").replaceChildren(); previous = kind; rowsByKind[kind].forEach(addRow);
    }
    $("h-transport").onchange = () => { capture(); transport(); };
    $("h-add-secret").onclick = () => addRow(); transport();
    $("h-form").onsubmit = e => {
      e.preventDefault(); capture();
      try {
        const kind = $("h-transport").value, credentials = {};
        for (const row of rowsByKind[kind]) {
          if (!row.key.trim()) throw new Error("Give each credential field a name, or remove its row.");
          if (Object.hasOwn(credentials, row.key.trim())) throw new Error("Credential field names must be unique.");
          const originallySaved = Object.hasOwn(kind === "stdio" ? item.env || {} : item.headers || {}, row.key.trim());
          credentials[row.key.trim()] = row.saved && originallySaved && !row.value ? null : row.value || "";
        }
        const body = {kind: "connections", action: "save", id: item.id, name: $("h-conn-name").value, transport: kind, enabled: item.enabled || false};
        if (kind === "stdio") Object.assign(body, {command: $("h-command").value, args: JSON.parse($("h-args").value), cwd: $("h-cwd").value, env: credentials});
        else Object.assign(body, {url: $("h-url").value, headers: credentials});
        submit(body);
      } catch (err) { $("h-form-error").textContent = err.message; }
    };
  }
  async function modelStatus() {
    try {
      const r = await fetch("health", {headers: headers()});
      if (!r.ok) throw new Error();
      const h = await r.json();
      $("h-model").textContent = h.model;
      $("h-model-info").textContent = `${ctxfmt(h.max_context)} context · ${h.loaded ? "ready" : "unloaded"}`;
      $("h-engine-dot").dataset.state = h.loaded ? "ready" : "idle";
    } catch (e) {
      $("h-model-info").textContent = "Cannot reach the local server · retry ↻";
      $("h-engine-dot").dataset.state = "error";
    }
  }
  $("h-health").onclick = () => { modelStatus(); loadHealth(); };
  renderHistory(); persist(); renderChoices(); navigate(["monitor", "about", "images"].includes(location.hash.slice(1)) ? location.hash.slice(1) : "chat");
  api().catch(e => toast("error", "Workspace unavailable", e.message, 8000));
  modelStatus(); setInterval(modelStatus, 15000);
})();
