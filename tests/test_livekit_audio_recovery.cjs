// Run: node tests/test_livekit_audio_recovery.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const app = fs.readFileSync('lampgo/web/static/app.js', 'utf8');
const section = (a, b) => app.slice(app.indexOf(a), app.indexOf(b, app.indexOf(a)));
const fn = (name) => {
  const pos = app.search(new RegExp(`  (?:async )?function ${name}\\(`));
  assert.ok(pos >= 0);
  return app.slice(pos, app.indexOf('\n  }', pos) + 4);
};
const deferred = () => { let resolve; const promise = new Promise(r => { resolve = r; }); return { promise, resolve }; };
async function main() {
  const contexts = [], sockets = [], notes = [];
  let pendingResume = null;
  const node = () => ({ connect() {}, disconnect() {}, gain: {} });
  class AudioContext {
    constructor() { this.sampleRate = 48000; this.state = pendingResume ? 'suspended' : 'running'; this.pending = pendingResume; this.sources = 0; contexts.push(this); }
    async resume() { if (this.pending) await this.pending.promise; if (this.state !== 'closed') this.state = 'running'; }
    close() { this.state = 'closed'; return Promise.resolve(); }
    createMediaStreamSource() { assert.notEqual(this.state, 'closed'); this.sources++; return node(); }
    createScriptProcessor() { return node(); }
    createGain() { return node(); }
  }
  class WebSocket {
    static OPEN = 1;
    constructor() { this.readyState = 0; sockets.push(this); }
    close() { this.readyState = 3; }
  }
  const ctx = vm.createContext({
    AudioContext, WebSocket, MediaStream: class {}, console,
    window: { location: { protocol: 'http:', host: 'localhost' }, setTimeout: () => 1, clearTimeout() {} },
    document: { body: { appendChild() {} } },
    addCallSystemNote: text => notes.push(text), sendEsp32SpeakerSamples() {},
    primeEsp32SpeakerRelay() {}, flushEsp32SpeakerPendingFrames() {},
  });
  vm.runInContext(`${section('  let esp32AudioCtx =', '  function setBrowserCallState(')}
    let lkRoom = null, browserCallJoiningRoom = null, browserCallStopRequested = false, browserCallDisconnectingRoom = null;
    ${['openEsp32CallSpeakerWs', 'closeEsp32CallSpeakerWs', 'startEsp32SpeakerRelay', 'cleanupEsp32SpeakerRelay', 'installCallRecoveryHandlers'].map(fn).join('\n')}`, ctx);
  const track = () => {
    const t = { mediaStreamTrack: {}, attached: [], detached: [], attach() { const el = { style: {}, remove() {} }; t.attached.push(el); return el; }, detach(el) { t.detached.push(el); } };
    return t;
  };
  const a = track(), b = track();
  await ctx.startEsp32SpeakerRelay(a);
  await ctx.startEsp32SpeakerRelay(b);
  const current = contexts.at(-1);
  ctx.cleanupEsp32SpeakerRelay(a); // old unsubscribe arrives after new subscribe
  assert.equal(current.state, 'running');
  assert.equal(a.detached[0], a.attached[0]);
  assert.equal(b.detached.length, 0);
  ctx.cleanupEsp32SpeakerRelay(b);
  assert.equal(current.state, 'closed');
  assert.equal(b.detached[0], b.attached[0]);

  pendingResume = deferred();
  const pending = ctx.startEsp32SpeakerRelay(track());
  const cancelledContext = contexts.at(-1);
  ctx.cleanupEsp32SpeakerRelay(); // hangup while resume is pending
  pendingResume.resolve();
  assert.equal(await pending, false);
  assert.equal(cancelledContext.sources, 0);
  pendingResume = null;

  // An obsolete WS close must not clear the replacement connection promise.
  const first = ctx.openEsp32CallSpeakerWs();
  const firstRejected = assert.rejects(first, /已断开/);
  ctx.closeEsp32CallSpeakerWs();
  const second = ctx.openEsp32CallSpeakerWs();
  sockets[0].onclose();
  await firstRejected;
  assert.equal(ctx.openEsp32CallSpeakerWs(), second);
  sockets[1].readyState = WebSocket.OPEN;
  sockets[1].onopen();
  assert.equal(await second, sockets[1]);

  const handlers = {};
  ctx.room = { on: (name, cb) => { handlers[name] = cb; } };
  ctx.track = track();
  vm.runInContext('lkRoom = room; remoteAudioTrack = track;', ctx);
  await ctx.startEsp32SpeakerRelay(ctx.track);
  ctx.installCallRecoveryHandlers(ctx.room, true);
  handlers.reconnecting();
  assert.match(notes.at(-1), /中断/);
  const before = contexts.length;
  handlers.reconnected();
  assert.equal(contexts.length, before + 1);
  assert.equal(contexts[before - 1].state, 'closed');
  vm.runInContext('lkRoom = null;', ctx);
  const noteCount = notes.length;
  handlers.reconnecting(); handlers.reconnected();
  assert.equal(contexts.length, before + 1);
  assert.equal(notes.length, noteCount);

  // Exercise the actual unsubscribe callback, including old-room events.
  const start = app.indexOf('        room.on("trackUnsubscribed"');
  const stop = app.indexOf('        installCallRecoveryHandlers', start);
  vm.runInContext(`const useEsp32 = true; ${app.slice(start, stop)}`, ctx);
  handlers.trackUnsubscribed(ctx.track); // old room no longer owns current relay
  assert.equal(contexts.at(-1).state, 'running');
  vm.runInContext('lkRoom = room;', ctx);
  handlers.trackUnsubscribed(a); // stale track does not own current relay
  assert.equal(contexts.at(-1).state, 'running');
  handlers.trackUnsubscribed(ctx.track);
  assert.equal(contexts.at(-1).state, 'closed');
  console.log('Audio recovery: track ownership, cancelled initialization, stale WS, reconnect and old-room events passed');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
