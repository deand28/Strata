# Local voice conversation

The harness Voice button adds local microphone transcription and spoken replies to the
existing chat. Speech recognition uses faster-whisper on CPU with int8 weights. Speech
output uses installed Windows System.Speech voices, played as WAV audio by the browser.
No browser/cloud speech recognition or speech synthesis service is used.

## Setup

Install optional speech dependencies into the harness's own virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-voice.txt
```

Use an already downloaded CTranslate2/faster-whisper model directory. The current PC
uses `Systran/faster-whisper-base.en` under the ignored `.harness-data/voice/model`.
Add this block to the local ignored run config alongside `"harness": true`:

```json
"voice": {
  "model": "C:/absolute/path/to/Strata-harness/.harness-data/voice/model",
  "language": "en",
  "cpu_threads": 4
}
```

Restart the harness with that virtual environment. Models are loaded lazily on the first
spoken turn. Normal server startup does not import faster-whisper or download a model.
Voice requests cannot choose model paths or download models. Installed Windows voices
are discovered on the first voice-status request. Non-Windows hosts can dictate with
spoken replies off; Windows TTS is the current local speech output implementation.

## Conversation controls

1. Open **Voice**, then **Start conversation**. The browser asks for microphone access
   only after this click. Send or clear any typed draft first.
2. Speak. With **Auto finish on pause** selected, a short spoken segment followed by
   about 1.2 seconds of quiet finishes the turn. **Finish turn** remains available.
   If audio analysis is unavailable, use Finish turn. Quiet input alone does not trigger
   pause detection; Whisper's voice-activity filter also discards silent recordings.
3. The transcript appears in the panel and is sent through the existing chat with that
   chat's selected skills and tools. The microphone is off during transcription, model
   generation, and playback. Empty transcripts resume listening without sending chat.
4. The final prose is spoken with the selected installed voice. Code blocks, inline
   code, and reasoning are skipped. Tool activity remains visible in the chat but is not
   read. Turn **Read replies** off to dictate without spoken playback.
5. Listening resumes after playback. **Interrupt** stops the current answer or playback
   and listens again. **End** stops the conversation and releases the microphone.

Voice turns request `reasoning_effort: none` and at most 768 output tokens per model
round, so long high-thinking chat defaults do not delay a spoken reply. Saved typed-chat
settings remain unchanged. Selected skill instructions retain their usual role/context.

The chat Stop button also ends an active voice session. Closing Voice, navigating to
another workspace view, creating/switching a chat, typing, hiding the page, or leaving
the page ends voice and prevents delayed transcript submission or audio playback.
Interrupting a pending native transcription discards its result; the bounded CPU job
may finish on the server before another recording can be transcribed.

## Browser and device support

Use a current Chrome or Edge browser at the loopback address. A secure context and
MediaRecorder microphone support are required. If an embedded browser cannot record,
the panel offers a link to open the same localhost workspace in a normal browser.
Microphone denial, missing/busy devices, server/model setup errors, and unsupported
recording formats produce a message in the panel; typed chat remains available.

If the browser blocks automatic WAV playback, choose **Play reply**, or disable Read
replies. Headphones help pause detection in a noisy room. Finish turn provides a manual
fallback when the microphone signal is too quiet or background noise prevents a pause.

## Local data and bounds

Recorded audio is held in browser/server memory and is not written to disk. Microphone
tracks, audio contexts, playback objects, and object URLs are released at the end of
their turn or session. Only the normal chat transcript persists in browser chat history;
voice/auto-finish/read preferences contain no audio. Configured MCP tools can still
contact the services the user selected.

Each recording is limited to 6 MiB and 90 decoded seconds; the browser finishes by
85 seconds. Transcripts are limited to 12,000 characters and spoken output to 5,000.
One transcription and one TTS generation may run at a time. Windows speech receives
JSON through stdin to a fixed script, with no user text interpolated into shell code,
and has a 30-second generation timeout. WAV playback can be stopped at any time.

Voice endpoints exist only in harness mode: `GET /voice/status`,
`POST /voice/transcribe {"audio":"base64 recording"}`, and
`POST /voice/synthesize {"text":"prose","voice":"installed voice name"}`. Mutations
use the harness's same-origin JSON, Host, API authentication and loopback restrictions.

## Verification

```powershell
.\.venv\Scripts\python.exe -m unittest serve.test_voice serve.test_harness serve.test_mcp serve.test_web_sampling serve.test_frontend serve.test_server
node serve/test_voice_state.js
node --check serve/web/voice.js
```

The voice tests inject a fake transcription model, cover malformed/oversized/foreign
requests, sequential ownership, timeout handling, and the fixed TTS argument/stdin
boundary. The optional PyAV decoder test runs when the voice dependencies are installed;
otherwise it skips. Node tests check stale asynchronous results, resource cleanup,
session changes, and prose-only speech. None needs a GPU, microphone, downloaded model,
or a live Windows speech engine. Test real microphone permission/pause detection and
speaker playback separately in the browser on the target PC.
