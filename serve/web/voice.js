// Local microphone -> faster-whisper -> existing chat -> local Windows WAV speech.
"use strict";
(() => {
  const session = new VoiceSession();
  let state = "off", capability = null, recorder = null, finishRecording = null;
  let pendingChat = null, modelController = null, stopPlayback = null;
  const prefs = store.get("voice", {read: true, voice: "", auto: true});
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  const trigger = document.createElement("button");
  trigger.type = "button"; trigger.className = "st-btn st-btn--secondary v-trigger";
  trigger.textContent = "Voice"; trigger.setAttribute("aria-expanded", "false"); trigger.setAttribute("aria-controls", "v-panel");
  document.querySelector(".st-composer__bar .spacer").before(trigger);
  const panel = document.createElement("section");
  panel.id = "v-panel"; panel.className = "v-panel"; panel.hidden = true; panel.setAttribute("aria-label", "Local voice conversation");
  panel.innerHTML = `<div class="v-head"><strong>Local voice</strong><span class="v-state" id="v-state" role="status" aria-live="polite">Ready</span><button class="st-btn st-btn--icon" id="v-close" aria-label="Close voice">×</button></div>
    <p class="v-help" id="v-help">Speak, choose Finish turn, then hear the answer. The microphone pauses while Strata replies.</p>
    <div class="v-settings"><label><input type="checkbox" id="v-auto"> Auto finish on pause</label><label><input type="checkbox" id="v-read"> Read replies</label><label>Voice <select class="st-input" id="v-voice" aria-label="Local Windows voice"></select></label></div>
    <div class="v-actions"><button class="st-btn st-btn--primary" id="v-start">Start conversation</button><button class="st-btn st-btn--primary" id="v-finish" hidden>Finish turn</button><button class="st-btn st-btn--secondary" id="v-interrupt" hidden>Interrupt</button><button class="st-btn st-btn--secondary" id="v-play" hidden>Play reply</button><button class="st-btn st-btn--secondary" id="v-end" hidden>End</button></div>
    <div class="v-transcript" id="v-transcript" aria-live="polite"></div><p class="v-fallback" id="v-fallback" hidden>Microphone unavailable here. <a id="v-browser" target="_blank" rel="noopener">Open this workspace in your normal browser</a>, or keep typing.</p>`;
  document.querySelector(".composer-wrap").prepend(panel);
  $("v-browser").href = location.origin + "/#chat";
  $("v-read").checked = prefs.read !== false;
  $("v-auto").checked = prefs.auto !== false && !!AudioContextClass;
  $("v-auto").disabled = !AudioContextClass;
  function status(next, detail = "") {
    state = next; panel.dataset.state = next;
    $("v-state").textContent = {off: "Ready", checking: "Checking local voice…", listening: "Listening", transcribing: "Transcribing on this PC…", thinking: "Strata is replying…", speaking: "Speaking", stopping: "Stopping…", error: "Voice paused"}[next] || next;
    $("v-help").textContent = detail || "Speak, choose Finish turn, then hear the answer. The microphone pauses while Strata replies.";
    $("v-start").hidden = session.active || next === "checking";
    $("v-start").disabled = next === "checking";
    $("v-finish").hidden = next !== "listening";
    $("v-interrupt").hidden = !session.active || next === "listening" || next === "stopping";
    $("v-end").hidden = !session.active;
    trigger.setAttribute("aria-pressed", String(session.active));
  }
  function end(cancelModel = true) {
    session.end(); finishRecording = null; recorder = null;
    if (cancelModel && modelController) modelController.abort();
    if (stopPlayback) { stopPlayback(); stopPlayback = null; }
    $("v-play").hidden = true;
    status("off", "Voice ended. Your microphone is off.");
  }
  function fail(error, token) {
    if (token != null && !session.valid(token)) return;
    end(); status("error", error.message || String(error));
  }
  async function request(path, body, token) {
    const controller = new AbortController();
    const release = session.own(() => controller.abort());
    try {
      const r = await fetch(path, {method: body ? "POST" : "GET", headers: headers(!!body), signal: controller.signal,
        ...(body ? {body: JSON.stringify(body)} : {})});
      const result = await r.json();
      if (!r.ok) throw new Error(result.error?.message || "Local voice request failed");
      if (token != null && !session.valid(token)) throw new DOMException("Voice ended", "AbortError");
      return result;
    } finally { release(); }
  }
  async function refresh() {
    const result = await fetch("voice/status", {headers: headers()});
    const data = await result.json();
    if (!result.ok) throw new Error(data.error?.message || "Local voice is not configured");
    capability = data;
    $("v-voice").innerHTML = '<option value="">Windows default</option>' + data.tts.voices.map(v => `<option value="${esc(v.name)}">${esc(v.name)} · ${esc(v.language)}</option>`).join("");
    $("v-voice").value = prefs.voice || "";
    if (!data.tts.available) { $("v-read").checked = false; $("v-voice").disabled = true; }
    return data;
  }
  trigger.onclick = async () => {
    if (!panel.hidden) { end(); panel.hidden = true; trigger.setAttribute("aria-expanded", "false"); return; }
    panel.hidden = false; trigger.setAttribute("aria-expanded", "true");
    status("checking");
    try {
      const c = await refresh();
      if (panel.hidden) return;
      status("off", c.stt.available ? "Speech is processed on this PC. Start when you are ready to allow the microphone." : c.stt.message);
    } catch (e) { if (!panel.hidden) status("error", e.message); }
  };
  $("v-close").onclick = () => { end(); panel.hidden = true; trigger.setAttribute("aria-expanded", "false"); };
  $("v-end").onclick = () => end();
  $("v-read").onchange = () => {
    prefs.read = $("v-read").checked; store.set("voice", prefs);
    if (!prefs.read && stopPlayback) stopPlayback();
  };
  $("v-voice").onchange = () => { prefs.voice = $("v-voice").value; store.set("voice", prefs); };
  $("v-auto").onchange = () => { prefs.auto = $("v-auto").checked; store.set("voice", prefs); };
  $("v-finish").onclick = () => finishRecording?.();
  $("v-start").onclick = async () => {
    if (busy) return status("off", "Wait for the current answer, or stop it before starting voice.");
    if ($("input").value.trim()) return status("off", "Send or clear your typed draft before starting voice.");
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      $("v-fallback").hidden = false;
      return status("error", "This browser does not provide microphone recording. Open localhost in Chrome or Edge.");
    }
    const token = session.begin();
    status("checking");
    try {
      const c = await refresh();
      if (!session.valid(token)) return;
      if (!c.stt.available) throw new Error(c.stt.message);
      await listen(token);
    } catch (e) { fail(e, token); }
  };
  async function listen(token) {
    if (!session.valid(token)) return;
    status("listening", "Allow the microphone, then speak and choose Finish turn.");
    let stream;
    try { stream = await navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true}, video: false}); }
    catch (e) {
      const message = {NotAllowedError: "Microphone permission was denied. Allow it in this browser's site settings, then try again.", NotFoundError: "No microphone was found. Connect one, then try again.", NotReadableError: "The microphone is busy or unavailable. Close other recording apps and try again."}[e.name] || "The microphone could not start. Try this localhost workspace in Chrome or Edge.";
      $("v-fallback").hidden = false; throw new Error(message);
    }
    if (!session.valid(token)) { stream.getTracks().forEach(t => t.stop()); return; }
    const closeMic = session.own(() => stream.getTracks().forEach(t => t.stop()));
    const types = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/mp4"];
    const mimeType = types.find(t => MediaRecorder.isTypeSupported(t));
    let rec;
    try { rec = new MediaRecorder(stream, mimeType ? {mimeType} : {}); }
    catch (e) { closeMic(); throw new Error("This browser could not record audio. Try Chrome or Edge."); }
    recorder = rec;
    let chunks = [], size = 0, timer, elapsed;
    const release = session.own(() => {
      clearTimeout(timer); clearInterval(elapsed);
      rec.onstop = null; rec.ondataavailable = null; rec.onerror = null;
      if (rec.state !== "inactive") rec.stop();
      chunks = []; closeMic();
    });
    rec.ondataavailable = e => {
      if (!session.valid(token)) return;
      size += e.data.size;
      if (size > (capability?.max_audio_bytes || 6291456)) return fail(new Error("That recording was too large. Use a shorter turn."), token);
      if (e.data.size) chunks.push(e.data);
    };
    rec.onerror = () => fail(new Error("Microphone recording failed. Try again in your normal browser."), token);
    rec.onstop = () => {
      if (!session.valid(token)) { release(); return; }
      const blob = new Blob(chunks, {type: rec.mimeType});
      release(); finishRecording = null; recorder = null;
      transcribe(blob, token).catch(e => fail(e, token));
    };
    finishRecording = () => { if (session.valid(token) && rec.state === "recording") { status("transcribing"); rec.stop(); } };
    rec.start(1000);
    const started = Date.now();
    // Local RMS only: no speech service, no microphone during answer playback.
    if (AudioContextClass) {
      try {
        const context = new AudioContextClass();
        let source, analyser, frame = 0;
        const closeAnalysis = session.own(() => { cancelAnimationFrame(frame); source?.disconnect(); analyser?.disconnect(); context.close().catch(() => {}); });
        source = context.createMediaStreamSource(stream); analyser = context.createAnalyser();
        analyser.fftSize = 1024; source.connect(analyser);
        const samples = new Float32Array(analyser.fftSize);
        let last = performance.now(), speechMs = 0, speechSeen = false, lastSpeech = last;
        const analyze = now => {
          if (!session.valid(token) || rec.state !== "recording") { closeAnalysis(); return; }
          if ($("v-auto").checked && context.state === "running") {
            analyser.getFloatTimeDomainData(samples);
            const rms = Math.sqrt(samples.reduce((sum, n) => sum + n * n, 0) / samples.length);
            if (rms > 0.018) { speechMs += Math.min(now - last, 100); lastSpeech = now; if (speechMs >= 180) speechSeen = true; }
            else if (!speechSeen) speechMs = 0;
            if (speechSeen && now - lastSpeech > 1200 && Date.now() - started > 1000) { closeAnalysis(); finishRecording?.(); return; }
          }
          last = now; frame = requestAnimationFrame(analyze);
        };
        context.resume().catch(() => { $("v-help").textContent = "Automatic pause detection is unavailable. Choose Finish turn when done."; });
        frame = requestAnimationFrame(analyze);
        // Recording cleanup also releases analysis before the next turn.
        const priorOnStop = rec.onstop;
        rec.onstop = () => { closeAnalysis(); priorOnStop(); };
      } catch (e) { $("v-help").textContent = "Choose Finish turn when you are done speaking."; }
    }
    elapsed = setInterval(() => {
      if (session.valid(token) && state === "listening") $("v-help").textContent = `Listening · ${Math.floor((Date.now() - started) / 1000)} s. Choose Finish turn when you are done.`;
    }, 1000);
    timer = setTimeout(() => finishRecording?.(), Math.min(capability.max_seconds - 3, 85) * 1000);
  }
  async function transcribe(blob, token) {
    if (!session.valid(token)) return;
    status("transcribing");
    const bytes = new Uint8Array(await blob.arrayBuffer());
    if (!session.valid(token)) return;
    let binary = "";
    for (let i = 0; i < bytes.length; i += 16384) binary += String.fromCharCode(...bytes.subarray(i, i + 16384));
    const result = await request("voice/transcribe", {audio: btoa(binary)}, token);
    if (!session.valid(token)) return;
    if (!result.text.trim()) { $("v-transcript").textContent = "No speech detected. Try again."; return listen(token); }
    $("v-transcript").textContent = `You: ${result.text}`;
    $("input").value = result.text; autosize();
    status("thinking", "Your spoken turn uses this chat's selected skills and tools.");
    const task = send({voiceToken: token});
    const controller = busy?.controller;
    modelController = controller; pendingChat = task;
    let answer;
    try { answer = await task; }
    finally { if (modelController === controller) modelController = null; if (pendingChat === task) pendingChat = null; }
    if (!session.valid(token)) return;
    if (!answer || answer.error || answer.stopped) throw new Error(answer?.error || "The answer stopped. Start voice again when you are ready.");
    const prose = voiceProse(answer.text);
    if ($("v-read").checked && prose) await speak(prose, token);
    if (session.valid(token)) await listen(token);
  }
  async function speak(prose, token) {
    status("speaking", "Preparing a local Windows voice…");
    const reply = await request("voice/synthesize", {text: prose, voice: $("v-voice").value}, token);
    if (!session.valid(token) || !$("v-read").checked) return;
    const raw = Uint8Array.from(atob(reply.audio), c => c.charCodeAt(0));
    const url = URL.createObjectURL(new Blob([raw], {type: "audio/wav"}));
    const audio = new Audio(url);
    await new Promise((resolve, reject) => {
      const release = session.own(() => {
        audio.onended = null; audio.onerror = null; audio.pause(); audio.removeAttribute("src"); audio.load();
        URL.revokeObjectURL(url); $("v-play").hidden = true; resolve();
      });
      stopPlayback = release;
      audio.onended = release;
      audio.onerror = () => { reject(new Error("The spoken reply could not play. Turn Read replies off to keep dictating.")); release(); };
      const play = () => audio.play().then(() => { if (!session.valid(token)) return; $("v-play").hidden = true; status("speaking", "Listening resumes after the reply. Interrupt to speak sooner."); }).catch(() => {
        if (!session.valid(token)) return;
        $("v-play").hidden = false; $("v-help").textContent = "Your browser paused playback. Choose Play reply, or turn Read replies off.";
      });
      $("v-play").onclick = play; play();
    });
    stopPlayback = null;
  }
  $("v-interrupt").onclick = async () => {
    const previous = pendingChat;
    end();
    const token = session.begin();
    status("stopping", "Stopping the current reply before listening again…");
    if (previous) await previous.catch(() => {});
    if (session.valid(token)) listen(token).catch(e => fail(e, token));
  };
  window.addEventListener("harness-chat-start", e => { if (session.active && e.detail.voiceToken !== session.generation) end(false); });
  $("input").addEventListener("input", () => { if (session.active) end(); });
  window.addEventListener("harness-workspace-change", () => end());
  $("stop-btn").addEventListener("click", () => { if (session.active) end(); });
  window.addEventListener("pagehide", () => end());
  document.addEventListener("visibilitychange", () => { if (document.hidden) end(); });
  window.addEventListener("hashchange", () => { if (location.hash && location.hash !== "#chat") end(); });
})();
