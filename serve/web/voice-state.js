// Resource ownership for asynchronous voice turns. Also tested in Node without a DOM.
"use strict";
class VoiceSession {
  constructor() { this.generation = 0; this.active = false; this.resources = new Set(); }
  begin() { this.end(); this.active = true; return this.generation; }
  valid(token) { return this.active && token === this.generation; }
  own(cleanup) {
    let done = false;
    const release = () => {
      if (done) return;
      done = true; this.resources.delete(release);
      try { cleanup(); } catch (e) { /* every other resource must still be released */ }
    };
    this.resources.add(release);
    return release;
  }
  end() {
    this.active = false; this.generation++;
    for (const release of [...this.resources]) release();
  }
}
function voiceProse(text) {
  return String(text || "").replace(/```[\s\S]*?(?:```|$)/g, " ")
    .replace(/<think>[\s\S]*?(?:<\/think>|$)/gi, " ").replace(/`[^`]*`/g, " ")
    .replace(/!\[[^\]]*\]\([^)]*\)/g, " ").replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/^\s*(?:#{1,6}|>|[-*+] |\d+\. )\s*/gm, "").replace(/[*_~]/g, "")
    .replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim().slice(0, 5000);
}
if (typeof module !== "undefined" && module.exports) module.exports = {VoiceSession, voiceProse};
