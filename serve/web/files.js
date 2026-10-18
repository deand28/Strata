// Harness-only local files. No browser directory grants, cloud parsers or indexing.
"use strict";
(() => {
  $("attach-btn").setAttribute("aria-label", "Attach documents or pictures");
  let state = {roots: [], tools: []}, panel, generation = 0, controllers = new Set(), pending = 0;
  let folder = "", path = "", offset = 0, query = "", last, revision = 0, attachmentQueue = Promise.resolve();
  const choices = document.createElement("details");
  choices.className = "h-choices";
  document.querySelector(".composer-wrap").prepend(choices);
  const progress = document.createElement("p");
  progress.className = "h-file-progress"; progress.setAttribute("role", "status"); progress.hidden = true;
  document.querySelector(".composer-wrap").prepend(progress);
  const names = {files__list: "List folders", files__search: "Search file names", files__read: "Read file contents"};
  async function api(body, extract = false, signal) {
    const r = await fetch(extract ? "files/extract" : "files", {method: body ? "POST" : "GET", headers: headers(!!body), ...(body ? {body: JSON.stringify(body)} : {}), signal});
    const value = await r.json();
    if (!r.ok) throw new Error(value.error?.message || `HTTP ${r.status}`);
    return value;
  }
  function renderChoices() {
    const selected = window.harnessChat.selection();
    const missing = selected.roots.some(id => !state.roots.some(r => r.id === id));
    choices.innerHTML = `<summary>Files for this chat · ${selected.roots.length} folders · ${selected.tools.length} tools${missing ? " · review choices" : ""}</summary><div class="h-choice-body"><p class="h-permission">Choose folders and read-only tools for this chat. Files are read only when requested. Tool excerpts are saved with chat history in this browser.</p><div class="h-choice-grid"><div><h3>Folders</h3>${state.roots.map(r => `<label class="h-check"><input type="checkbox" data-kind="roots" value="${esc(r.id)}" ${selected.roots.includes(r.id) ? "checked" : ""} ${busy ? "disabled" : ""}><span><strong>${esc(r.name)}</strong><small>${esc(r.path)}</small></span></label>`).join("") || '<p class="muted">Add a specific folder in Files.</p>'}</div><div><h3>File tools</h3>${Object.entries(names).map(([id, name]) => `<label class="h-check"><input type="checkbox" data-kind="tools" value="${id}" ${selected.tools.includes(id) ? "checked" : ""} ${busy ? "disabled" : ""}><span>${name}</span></label>`).join("")}</div></div>${missing ? '<p class="h-error">A selected folder was removed. Clear unavailable choices before sending.</p><button id="h-files-clear" class="st-btn st-btn--secondary">Clear unavailable choices</button>' : ""}<button class="st-btn st-btn--secondary" id="h-files-manage">Manage folders</button></div>`;
    choices.querySelectorAll("input").forEach(el => el.onchange = () => {
      if (busy) return renderChoices();
      const next = window.harnessChat.selection();
      next[el.dataset.kind] = el.checked ? [...new Set([...next[el.dataset.kind], el.value])] : next[el.dataset.kind].filter(x => x !== el.value);
      window.harnessChat.chooseFiles(next); renderChoices();
    });
    choices.querySelector("#h-files-manage").onclick = window.harnessChat.showFiles;
    const clear = choices.querySelector("#h-files-clear");
    if (clear) clear.onclick = () => {
      if (busy) return;
      window.harnessChat.chooseFiles({...selected, roots: selected.roots.filter(id => state.roots.some(r => r.id === id))}); renderChoices();
    };
  }
  async function refresh() {
    state = await api();
    if (!state.roots.some(r => r.id === folder)) { folder = state.roots[0]?.id || ""; path = ""; last = null; }
    renderChoices();
    if (panel && !panel.hidden) render();
  }
  function render(target = panel) {
    panel = target;
    if (!panel || panel.hidden || panel.dataset.view !== "files") return;
    panel.innerHTML = `<div class="h-library-inner h-files"><div class="h-library-head"><div><span class="h-eyebrow">LOCAL DOCUMENTS</span><h1>Read what you choose.</h1><p>Add specific folders, then choose their access in each chat. Browsing and attachments work with model power Off.</p></div></div>
      <form id="h-folder-add" class="h-file-add"><label>Local folder path<input id="h-folder-path" placeholder="C:\\Projects\\My project" required autocomplete="off"></label><button class="st-btn st-btn--primary">Allow folder</button></form>
      <p class="muted small">Read-only access · No links or junctions · No macros, executable files or OCR · PDF, DOCX, XLSX, CSV/TSV, PPTX, RTF, LCPL license metadata and text/code. Attach images directly for vision.</p>
      <div class="h-file-roots">${state.roots.map(r => `<div class="h-file-root"><button class="st-btn st-btn--secondary" data-root="${esc(r.id)}" ${folder === r.id ? 'aria-current="true"' : ""}>${esc(r.name)}</button><span>${esc(r.path)}</span><button class="st-btn st-btn--secondary" data-remove="${esc(r.id)}" aria-label="Remove ${esc(r.name)}">Remove access</button></div>`).join("") || '<p class="muted">No folders allowed. Adding a folder does not select it for a chat.</p>'}</div>
      ${folder ? `<div class="h-file-browser"><form id="h-files-browse"><label>Relative folder<input id="h-files-path" value="${esc(path)}" placeholder="Leave empty for folder root"></label><label>Find names in this folder<input id="h-files-query" value="${esc(query)}" placeholder="Filename contains…"></label><button class="st-btn st-btn--secondary">Browse / search</button></form><div id="h-file-results" aria-live="polite"></div><div id="h-file-preview"></div></div>` : ""}
      <p class="h-storage-note">Folder grants are saved on this PC. File contents stay in their folders. Uploaded attachment text is transient; file tool excerpts are saved with this browser's chat history. Read untrusted documents as data, and verify important answers.</p></div>`;
    panel.querySelector("#h-folder-add").onsubmit = async e => {
      e.preventDefault(); const button = e.target.querySelector("button"); button.disabled = true;
      try { await api({action: "add", path: panel.querySelector("#h-folder-path").value}); await refresh(); render(); }
      catch (error) { toast("error", "Could not allow folder", error.message); button.disabled = false; }
    };
    panel.querySelectorAll("[data-root]").forEach(b => b.onclick = () => { folder = b.dataset.root; path = ""; query = ""; offset = 0; last = null; revision++; render(); browse(); });
    panel.querySelectorAll("[data-remove]").forEach(b => b.onclick = async () => {
      if (!confirm("Remove read access to this folder? Files on disk will stay in place.")) return;
      try { await api({action: "remove", root: b.dataset.remove}); revision++; await refresh(); render(); }
      catch (error) { toast("error", "Could not remove access", error.message); }
    });
    const form = panel.querySelector("#h-files-browse");
    if (form) form.onsubmit = e => { e.preventDefault(); path = panel.querySelector("#h-files-path").value; query = panel.querySelector("#h-files-query").value; offset = 0; browse(); };
    if (folder && last) showList(last);
  }
  async function browse() {
    const rev = ++revision;
    try {
      const value = await api({action: query ? "search" : "list", root: folder, path, query, offset, count: 50});
      if (rev !== revision) return;
      last = value; showList(value);
    } catch (error) { if (rev === revision) toast("error", "Could not browse folder", error.message); }
  }
  function showList(value) {
    const box = panel?.querySelector("#h-file-results"); if (!box) return;
    box.innerHTML = `<p class="muted small">${esc(value.path || "Folder root")}${value.warning ? " · " + esc(value.warning) : ""}</p><div class="h-file-list">${value.entries.map((f, i) => `<button class="st-btn st-btn--secondary" data-entry="${i}"><span>${f.directory ? "▸" : "·"} ${esc(f.name)}</span><small>${f.directory ? "Folder" : `${(f.bytes / 1024).toFixed(1)} KiB`}</small></button>`).join("") || '<p class="muted">No matching files in this range.</p>'}</div><div class="h-actions"><button class="st-btn st-btn--secondary" id="h-files-first">First page</button>${value.next_offset != null ? '<button class="st-btn st-btn--secondary" id="h-files-next">Next page</button>' : ""}</div>`;
    box.querySelectorAll("[data-entry]").forEach(b => b.onclick = () => {
      const f = value.entries[Number(b.dataset.entry)];
      if (f.directory) { path = f.path; offset = 0; query = ""; render(); browse(); } else read(f.path);
    });
    box.querySelector("#h-files-first").onclick = () => { offset = 0; browse(); };
    const next = box.querySelector("#h-files-next"); if (next) next.onclick = () => { offset = value.next_offset; browse(); };
  }
  async function read(file, start = 0, sheet) {
    const rev = ++revision;
    try {
      const value = await api({action: "read", root: folder, path: file, offset: start, count: 20, ...(sheet ? {sheet} : {})});
      if (rev !== revision) return;
      const box = panel?.querySelector("#h-file-preview"); if (!box) return;
      box.innerHTML = `<h3>${esc(value.name)}</h3><p class="muted small">${esc(value.warning || "")}</p>${value.sheets ? `<label>Sheet<select id="h-files-sheet">${value.sheets.map(s => `<option ${value.sheet === s ? "selected" : ""}>${esc(s)}</option>`).join("")}</select></label>` : ""}<pre class="h-file-text"></pre><div class="h-actions"><button class="st-btn st-btn--secondary" id="h-files-attach">Attach this excerpt to chat</button>${value.next_offset != null ? '<button class="st-btn st-btn--secondary" id="h-files-more">Read next range</button>' : ""}</div>`;
      box.querySelector("pre").textContent = value.text;
      const select = box.querySelector("select"); if (select) select.onchange = () => read(file, 0, select.value);
      const more = box.querySelector("#h-files-more"); if (more) more.onclick = () => read(file, value.next_offset, value.sheet);
      box.querySelector("#h-files-attach").onclick = () => {
        if (busy) return toast("warn", "Wait for the answer to finish");
        attachments.push({kind: "file", name: value.name, text: value.text + (value.warning ? "\n" + value.warning : "")}); renderAttachments();
        toast("info", "Excerpt attached", "Return to Chat to send it.");
      };
    } catch (error) { if (rev === revision) toast("error", "Could not read file", error.message, 8000); }
  }
  function updatePending() {
    window.harnessAttachmentPending = pending;
    progress.hidden = !pending; progress.textContent = pending ? `Reading ${pending} attachment${pending === 1 ? "" : "s"} locally…` : "";
    $("send-btn").disabled = !!busy || !!pending || (window.harnessPower && window.harnessPower.state !== "on");
  }
  // Every async completion belongs to its original workspace. Navigation clears
  // queued parsers; underlying bounded parser processes finish without retaining audio/files.
  window.addEventListener("harness-workspace-change", () => {
    generation++; revision++; controllers.forEach(c => c.abort()); controllers.clear(); pending = 0; updatePending();
  });
  window.harnessAttachmentEpoch = () => generation;
  window.harnessDroppedFiles = transfer => {
    const items = [...transfer.items || []].filter(item => item.kind === "file");
    const directories = [], files = [];
    for (const item of items) {
      let entry;
      try { entry = item.webkitGetAsEntry?.(); } catch (_) { /* The browser may not expose entries. */ }
      if (entry?.isDirectory) directories.push(entry.name);
      else { const file = item.getAsFile(); if (file) files.push(file); }
    }
    if (!directories.length) return transfer.files;
    toast("info", "Use Files to allow folders", `${directories.join(", ")}: open Files, enter the folder's full local path, then choose Allow folder. Select its folder and read tools under Files for this chat. Individual files dropped alongside it are still attached.`, 10000);
    return files;
  };
  window.harnessExtractAttachment = file => {
    if (file.name.toLowerCase() === ".ds_store") return toast("info", "Skipped folder metadata", `${file.name} is a macOS folder settings file, not document content.`);
    if (file.size > 16 * 1024 * 1024) return toast("warn", "Document too large", "Use a file smaller than 16 MiB, or add its folder for paged text reading.");
    const epoch = generation;
    pending++; updatePending();
    const task = async () => {
    if (epoch !== generation) return;
    const controller = new AbortController(); controllers.add(controller);
    try {
      const bytes = new Uint8Array(await file.arrayBuffer());
      if (epoch !== generation) return;
      let binary = "";
      for (let i = 0; i < bytes.length; i += 32768) binary += String.fromCharCode(...bytes.subarray(i, i + 32768));
      const value = await api({name: file.name, data: btoa(binary), count: 100}, true, controller.signal);
      if (epoch !== generation) return;
      attachments.push({kind: "file", name: file.name, text: value.text + (value.warning ? "\n" + value.warning : "") + (value.next_offset != null ? `\nMore content available from ${value.unit} offset ${value.next_offset}; add its folder to continue reading.` : "")}); renderAttachments();
      if (value.warning || value.truncated) toast("info", "Document excerpt attached", value.warning || "Only the first range was attached. Add its folder to read more.", 8000);
    } catch (error) { if (epoch === generation && error.name !== "AbortError") toast("error", "Could not attach file", error.message, 8000); }
    finally { controllers.delete(controller); if (epoch === generation) { pending--; updatePending(); } }
    };
    attachmentQueue = attachmentQueue.then(task, task);
    return attachmentQueue;
  };
  window.harnessFiles = {render};
  window.addEventListener("harness-choices-change", renderChoices);
  window.addEventListener("harness-chat-start", renderChoices);
  window.addEventListener("harness-chat-saved", renderChoices);
  refresh().catch(error => toast("error", "File access unavailable", error.message));
  renderChoices();
})();
