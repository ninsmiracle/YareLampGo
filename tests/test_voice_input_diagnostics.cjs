const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const reports = [];
let Processor;
const worklet = vm.createContext({
  Float32Array, sampleRate: 16000,
  AudioWorkletProcessor: class { constructor() { this.port = { postMessage: m => reports.push(m) }; } },
  registerProcessor: (_, ctor) => { Processor = ctor; },
});
vm.runInContext(fs.readFileSync('lampgo/web/static/esp32-pcm-worklet.js', 'utf8'), worklet);
const p = new Processor();
// A known signal must pass unchanged, then silence must be counted as underrun.
let total = 0;
for (let i = 0; i < 625; i++) {
  if (i < 125) p.port.onmessage({ data: new Float32Array(128).fill(0.5) });
  const out = new Float32Array(128);
  assert.equal(p.process([], [[out]]), true);
  total += out.reduce((a, b) => a + b, 0);
}
assert.equal(total, 8000);
assert.equal(reports.length, 1);
assert.equal(reports[0].output_samples, 80000);
assert.equal(reports[0].underrun_samples + reports[0].buffering_samples, 64000);
assert.equal(reports[0].overflow_samples, 0);
assert.equal(reports[0].peak, 0.5);
assert.ok(Math.abs(reports[0].rms - Math.sqrt(0.05)) < 1e-6);
assert.equal(reports[0].queued_ms, 0);

// 100 ms network batches separated by alternating 180/20 ms gaps. There must
// be no synthetic silence inside speech and no loss/reordering of samples.
const burst = new Processor(), rendered = [], expected = [];
let packet = 0;
for (let ms = 0; ms < 3000; ms += 8) {
  while (packet < 20 && ms >= Math.floor(packet / 2) * 200 + (packet % 2 ? 180 : 0)) {
    const chunk = new Float32Array(1600).fill((packet + 1) / 32);
    expected.push(...chunk);
    burst.port.onmessage({ data: chunk });
    packet++;
  }
  const out = new Float32Array(128);
  burst.process([], [[out]]);
  rendered.push(...out);
}
const first = rendered.findIndex(v => v !== 0);
const last = rendered.findLastIndex(v => v !== 0);
assert.ok(first <= 1920, 'startup must be bounded by 120 ms');
assert.deepEqual(rendered.slice(first, last + 1), expected, 'jitter must not chop up a sentence');
assert.equal(burst._overflowSamples, 0);

// Quiet/short utterances never wait for a full buffer or disappear at the tail.
const short = new Processor(), quiet = new Float32Array(80).fill(1 / 32768);
short.port.onmessage({ data: quiet });
const shortOut = [];
for (let i = 0; i < 20; i++) {
  const out = new Float32Array(128);
  short.process([], [[out]]);
  shortOut.push(...out);
}
assert.deepEqual(shortOut.filter(v => v !== 0), Array.from(quiet));
assert.ok(shortOut.findIndex(v => v !== 0) <= 1920);

// A stalled tab may deliver a large backlog. Keep memory/latency bounded and
// report the dropped stale samples rather than replaying seconds-old speech.
const overflow = new Processor();
const backlog = Float32Array.from({ length: 16000 }, (_, i) => i + 1);
overflow.port.onmessage({ data: backlog });
assert.equal(overflow._queued, 7680);
assert.equal(overflow._overflowSamples, 8320);
const afterOverflow = new Float32Array(128);
overflow.process([], [[afterOverflow]]);
assert.deepEqual(Array.from(afterOverflow), Array.from(backlog.slice(8320, 8448)));

const app = fs.readFileSync('lampgo/web/static/app.js', 'utf8');
const extract = name => {
  const start = app.indexOf(`  function ${name}(`);
  return app.slice(start, app.indexOf('\n  }', start) + 4);
};
async function main() {
  const sent = [], timers = new Map();
  let timerId = 0, resolveStats;
  const ctx = vm.createContext({
    console: { log() {}, error() {} }, Date, Promise,
    window: { setInterval: fn => { timers.set(++timerId, fn); return timerId; }, clearInterval: id => timers.delete(id) },
    send: m => sent.push(m), addCallSystemNote() {},
  });
  vm.runInContext(`let esp32InputDiagnosticsTimer = null, esp32MicRelayStats = { frames: 200 };
    let esp32WorkletNode = { port: {}, disconnect() {} }, esp32AudioCtx = { state: 'running', close() {} };
    let esp32RelayActive = false, esp32MediaStream = null, lkClientCallId = 'test-call';
    let lkLocalTrack = null;
    ${extract('startEsp32InputDiagnostics')}
    ${extract('cleanupEsp32AudioTrack')}`, ctx);
  ctx.track = { mediaStreamTrack: { readyState: 'live', enabled: true }, getRTCStatsReport: () => new Promise(r => { resolveStats = r; }) };
  vm.runInContext('lkLocalTrack = track; startEsp32InputDiagnostics(esp32WorkletNode, esp32AudioCtx);', ctx);
  timers.get(1)();
  await new Promise(setImmediate);
  assert.equal(sent[0].frames, 200);
  assert.equal(sent[0].ctx, 'running');
  assert.equal(sent[0].track_enabled, 1);
  resolveStats(new Map([['a', { type: 'outbound-rtp', kind: 'audio', packetsSent: 30, bytesSent: 800 }]]));
  await new Promise(setImmediate);
  timers.get(1)();
  assert.equal(sent[1].packets_sent, 30);
  const staleTick = timers.get(1);
  vm.runInContext('cleanupEsp32AudioTrack();', ctx);
  assert.equal(timers.size, 0);
  staleTick();
  assert.equal(sent.length, 2);
  console.log('Voice input diagnostics: PCM preservation, underrun, RTP counters, cleanup and stale timer passed');
}
main().catch(e => { console.error(e); process.exitCode = 1; });
