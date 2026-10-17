"use strict";
// Exercise the actual power script with deferred HTTP responses and a minimal DOM.
const assert = require("node:assert/strict"), fs = require("node:fs"), vm = require("node:vm"), path = require("node:path");
const elements = new Map();
function element(id) {
  if (!elements.has(id)) elements.set(id, {id, dataset: {}, setAttribute(k, v) { this[k] = v; }});
  return elements.get(id);
}
const timers = new Map(), requests = [];
let timerId = 0;
const window = {harnessImageBusy: true, dispatchEvent() {}};
const context = {window, busy: false, $: element, headers: () => ({}),
  document: {createElement: () => ({}), querySelector: () => ({before() {}})},
  setTimeout(fn) { const id = ++timerId; timers.set(id, fn); return id; }, clearTimeout(id) { timers.delete(id); },
  fetch() { return new Promise(resolve => requests.push(resolve)); },
  CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
  Event: class { constructor(type) { this.type = type; } },
};
const respond = (index, state) => requests[index]({ok: true, json: async () => ({enabled: state === "on", state, elapsed_s: 0})});
const flush = async () => { for (let i = 0; i < 6; i++) await Promise.resolve(); };
(async () => {
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "web/power.js"), "utf8"), context);
  respond(0, "on"); await flush();
  assert.equal(window.harnessPower.state, "on");
  assert.equal(element("img-go").disabled, true, "polls must preserve an in-flight image job");
  const poll = [...timers.values()].at(-1); poll(); // stale GET snapshots On
  const toggle = element("h-power-switch").onclick();
  assert.equal(window.harnessPower.state, "unloading");
  respond(2, "off"); await toggle;
  assert.equal(window.harnessPower.state, "off");
  respond(1, "on"); await flush(); // old GET arrives after the completed Off
  assert.equal(window.harnessPower.state, "off", "pre-toggle polling must not overwrite confirmed Off");
  assert.equal(element("send-btn").disabled, true);
  assert.equal(element("v-start").disabled, true);
  console.log("Power polling order and image-busy checks passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
