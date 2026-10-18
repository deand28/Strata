"use strict";
// Actual Files script: serial local parsers, stale completions, and Send ownership.
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
const listeners = new Map(), requests = [], elements = new Map(), attachments = [], warnings = [];
function el(id = "") {
  if (id && elements.has(id)) return elements.get(id);
  const e = {hidden: false, events: new Map(), prepend() {}, setAttribute() {}, querySelector() { return el(); }, querySelectorAll() { return []; },
    addEventListener(name, fn) { this.events.set(name, fn); }, classList: {add() {}, remove() {}}, focus() { this.focused = true; }};
  if (id) elements.set(id, e);
  return e;
}
const window = {harnessPower: {state: "off"}, harnessChat: {selection: () => ({roots: [], tools: []}), chooseFiles() {}, showFiles() {}}, addEventListener(name, fn) { listeners.set(name, [...(listeners.get(name) || []), fn]); }};
const context = {window, attachments, busy: false, AbortController, Uint8Array, btoa: s => Buffer.from(s, "binary").toString("base64"), headers: () => ({}), $: el,
  esc: s => s, toast: (...args) => warnings.push(args), renderAttachments() {}, document: {createElement: () => el(), querySelector: () => el()},
  fetch(url, options) { return new Promise(resolve => requests.push({url, options, resolve})); }};
function reply(index, data) { requests[index].resolve({ok: true, json: async () => data}); }
async function flush() { for (let i = 0; i < 30; i++) await Promise.resolve(); }
function changeChat() { for (const fn of listeners.get("harness-workspace-change") || []) fn(); }
function file(name) { return {name, size: 10, arrayBuffer: async () => new TextEncoder().encode("local data").buffer}; }
(async () => {
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "web/files.js"), "utf8"), context);
  reply(0, {roots: [], tools: []}); await flush();
  window.harnessExtractAttachment(file(".DS_Store")); await flush();
  assert.equal(requests.length, 1, "macOS metadata must not be submitted to a parser");
  assert.equal(window.harnessAttachmentPending || 0, 0);
  assert.equal(warnings.at(-1)[1], "Skipped folder metadata");
  const document = file("notes.docx"), zero = {...file("empty.txt"), size: 0};
  const dropped = window.harnessDroppedFiles({files: [document], items: [
    {kind: "file", webkitGetAsEntry: () => ({isDirectory: true, name: "Course pack"}), getAsFile: () => null},
    {kind: "file", webkitGetAsEntry: () => ({isDirectory: false}), getAsFile: () => document}
  ]});
  assert.equal(dropped.length, 1); assert.equal(dropped[0], document);
  assert.equal(warnings.at(-1)[1], "Use Files to allow folders");
  assert.match(warnings.at(-1)[2], /full local path/);
  assert.equal(requests.length, 1, "dropping a folder cannot grant access or upload it");
  const fallback = [zero];
  assert.equal(window.harnessDroppedFiles({files: fallback, items: [{kind: "file", getAsFile: () => zero}]}), fallback,
    "without directory entry information, a zero-byte file remains an ordinary file");
  const first = window.harnessExtractAttachment(file("first.pdf"));
  const second = window.harnessExtractAttachment(file("second.docx"));
  await flush();
  assert.equal(requests.length, 2, "only the first parser request may run");
  assert.equal(window.harnessAttachmentPending, 2);
  assert.equal(el("send-btn").disabled, true);
  reply(1, {name: "first.pdf", text: "Page 1", warning: "", next_offset: null}); await first; await flush();
  assert.equal(requests.length, 3); assert.equal(attachments.length, 1);
  reply(2, {name: "second.docx", text: "Paragraph 1", warning: "", next_offset: null}); await second;
  assert.equal(attachments.length, 2); assert.equal(window.harnessAttachmentPending, 0);
  assert.equal(el("send-btn").disabled, true, "finishing extraction cannot enable Send while Off");
  window.harnessPower.state = "on";
  const stale = window.harnessExtractAttachment(file("old.pdf")); await flush();
  changeChat(); attachments.length = 0;
  assert.equal(requests[3].options.signal.aborted, true);
  reply(3, {text: "must not land in new chat", warning: ""}); await stale;
  assert.equal(attachments.length, 0); assert.equal(window.harnessAttachmentPending, 0);
  assert.equal(el("send-btn").disabled, false);
  const pendingFile = window.harnessExtractAttachment(file("active.pdf"));
  const queuedFile = window.harnessExtractAttachment(file("queued.pdf")); await flush();
  changeChat(); reply(4, {text: "stale", warning: ""}); await pendingFile; await queuedFile;
  assert.equal(requests.length, 5, "queued old-chat files must never be submitted");
  assert.equal(attachments.length, 0);
  // Upstream image FileReader path uses the same workspace epoch guard.
  const app = fs.readFileSync(path.join(__dirname, "web/app.js"), "utf8");
  let attachedByDrop, prevented = false;
  context.addFiles = files => { attachedByDrop = files; };
  vm.runInNewContext(app.slice(app.indexOf("// drop files on the chat"), app.indexOf('$("file").onchange')), context);
  el("chat").events.get("drop")({preventDefault() { prevented = true; }, dataTransfer: {files: [document], items: [
    {kind: "file", webkitGetAsEntry: () => ({isDirectory: true, name: "Course pack"}), getAsFile: () => null},
    {kind: "file", webkitGetAsEntry: () => ({isDirectory: false}), getAsFile: () => document}
  ]}});
  assert.equal(prevented, true); assert.equal(el("input").focused, true);
  assert.equal(attachedByDrop.length, 1); assert.equal(attachedByDrop[0], document,
    "the actual drop handler must attach individual files and omit folder entries");
  let reader;
  Object.assign(context, {health: {images: true}, FileReader: class {constructor() { reader = this; } readAsDataURL() {}}, isTextFile: () => false});
  vm.runInNewContext(app.slice(app.indexOf("function addFiles(files)"), app.indexOf("// a file's text in the message")), context);
  context.addFiles([{type: "image/png", size: 100, name: "old-image.png"}]);
  changeChat(); reader.result = "data:image/png;base64,old"; reader.onload();
  assert.equal(attachments.length, 0, "a late image read cannot attach to another chat");
  console.log("File attachment queue, cancellation, power and image lifecycle checks passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
