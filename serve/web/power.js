// Harness-only explicit power: keep the page alive while models release GPU/RAM.
"use strict";
(() => {
  let state = {state: "checking", enabled: false}, pending = false, pollTimer, epoch = 0;
  window.harnessPower = state;
  const controls = document.createElement("div");
  controls.className = "h-power";
  controls.innerHTML = `<div class="h-power-row"><div><strong>Model power</strong><small id="h-power-hint">Checking…</small></div><button class="h-power-switch" id="h-power-switch" type="button" role="switch" aria-checked="false" aria-label="Model power" disabled>…</button></div><p class="h-power-error" id="h-power-error" role="alert" hidden></p>`;
  document.querySelector(".h-engine").before(controls);
  function render(next) {
    const was = state.state;
    state = next; window.harnessPower = next;
    const changing = ["checking", "loading", "unloading"].includes(next.state);
    const on = next.state === "on";
    const button = $("h-power-switch");
    button.disabled = changing || pending;
    button.dataset.state = next.state;
    button.setAttribute("aria-checked", String(on || next.state === "loading"));
    button.textContent = changing ? "…" : on ? "On" : "Off";
    $("h-power-hint").textContent = next.state === "loading" ? `Loading · ${Math.floor(next.elapsed_s || 0)} s` :
      next.state === "unloading" ? "Releasing memory…" : on ? "Ready to use" : next.state === "off" ? "GPU & model RAM released" : "Server unavailable";
    $("h-power-error").hidden = !next.error;
    $("h-power-error").textContent = next.error || "";
    $("send-btn").disabled = !!busy || !on;
    $("img-go").disabled = !!window.harnessImageBusy || !on;
    if ($("v-start")) $("v-start").disabled = !on;
    if (was !== next.state) window.dispatchEvent(new CustomEvent("harness-power-change", {detail: next}));
    clearTimeout(pollTimer);
    pollTimer = setTimeout(refresh, changing ? 1000 : 5000);
  }
  async function refresh() {
    if (pending) return;
    const revision = epoch;
    try {
      const response = await fetch("power", {headers: headers()});
      const data = await response.json();
      if (revision !== epoch || pending) return;
      if (!response.ok) throw new Error(data.error?.message || "Could not read model power");
      render(data);
    } catch (error) {
      if (revision !== epoch || pending) return;
      render({enabled: false, state: "error", error: error.message});
    }
  }
  $("h-power-switch").onclick = async () => {
    if (pending) return;
    epoch++;
    const enabled = state.state !== "on";
    // End microphone/playback, but allow an active answer to finish. The backend
    // reports busy rather than killing a generation or a native speech job.
    if (!enabled) window.dispatchEvent(new Event("harness-power-off"));
    pending = true;
    render({...state, state: enabled ? "loading" : "unloading", error: null, elapsed_s: 0});
    try {
      const response = await fetch("power", {method: "POST", headers: headers(true), body: JSON.stringify({enabled})});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error?.message || "Could not change model power");
      pending = false; render(data);
    } catch (error) {
      pending = false;
      try {
        const response = await fetch("power", {headers: headers()});
        const data = await response.json();
        if (!response.ok) throw new Error();
        render({...data, error: error.message});
      } catch (_) { render({enabled: false, state: "error", error: error.message}); }
    }
  };
  render(state); refresh();
})();
