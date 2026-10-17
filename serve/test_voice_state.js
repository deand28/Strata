"use strict";
const assert = require("node:assert/strict");
const {VoiceSession, voiceProse} = require("./web/voice-state.js");
(async () => {
  const voice = new VoiceSession();
  const first = voice.begin();
  let closedMic = 0, stoppedAudio = 0, revokedURL = 0, submitted = 0;
  voice.own(() => closedMic++);
  voice.own(() => { stoppedAudio++; revokedURL++; });
  const lateTranscript = Promise.resolve().then(() => { if (voice.valid(first)) submitted++; });
  voice.end(); voice.end();
  await lateTranscript;
  assert.equal(closedMic, 1); assert.equal(stoppedAudio, 1); assert.equal(revokedURL, 1); assert.equal(submitted, 0);
  const second = voice.begin();
  assert.equal(voice.valid(first), false); assert.equal(voice.valid(second), true);
  const controller = new AbortController(); voice.own(() => controller.abort());
  const third = voice.begin(); // interrupt/new session invalidates in-flight requests
  assert.equal(controller.signal.aborted, true); assert.equal(voice.valid(second), false); assert.equal(voice.valid(third), true);
  const release = voice.own(() => closedMic++); release(); release(); voice.end();
  assert.equal(closedMic, 2); assert.equal(voice.resources.size, 0);
  assert.equal(voiceProse("Hello.\n```js\nsecret code();\n```\n[Read this](https://example.com). `inlineCode` <think>hidden reasoning</think>"), "Hello. Read this. ".trim());
  assert.equal(voiceProse("x".repeat(6000)).length, 5000);
  console.log("Voice lifecycle and prose checks passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
