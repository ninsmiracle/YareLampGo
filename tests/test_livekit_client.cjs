// Run: node tests/test_livekit_client.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { pathToFileURL } = require('node:url');

async function main() {
  const root = path.resolve(__dirname, '..');
  const app = fs.readFileSync(path.join(root, 'lampgo/web/static/app.js'), 'utf8');
  const start = app.indexOf('  async function loadLiveKitClient() {');
  const end = app.indexOf('  function getEsp32WsUrl(', start);
  const loader = app.slice(start, end);
  const url = loader.match(/import\("([^"]+)"\)/)[1];
  assert.ok(url.startsWith('/vendor/'), 'call client must load from the backend');
  const sdk = await import(pathToFileURL(path.join(root, 'lampgo/web/static', url)));
  assert.equal(sdk.version, '2.22.3');
  for (const symbol of ['Room', 'LocalAudioTrack', 'createLocalAudioTrack']) {
    assert.equal(typeof sdk[symbol], 'function', `missing browser API ${symbol}`);
  }
  assert.equal(sdk.Track.Kind.Audio, 'audio');
  assert.equal(sdk.Track.Source.Microphone, 'microphone');

  // Exercise the actual loader with a transient failure: retries must not
  // reuse a rejected promise, while concurrent/successful loads are shared.
  let attempts = 0;
  const context = vm.createContext({
    console: { error() {} },
    importClient: async (requested) => {
      assert.equal(requested, url);
      if (++attempts === 1) throw new Error('temporary local resource failure');
      return sdk;
    },
  });
  vm.runInContext(`let lkModulePromise = null; ${loader.replace('import(', 'importClient(')}`, context);
  await assert.rejects(context.loadLiveKitClient(), /本地通话组件加载失败/);
  const modules = await Promise.all([context.loadLiveKitClient(), context.loadLiveKitClient()]);
  assert.equal(attempts, 2);
  assert.equal(modules[0], sdk);
  assert.equal(modules[1], sdk);

  const call = app.slice(app.indexOf('  async function startBrowserLiveKitCall('));
  assert.ok(call.indexOf('await loadLiveKitClient()') < call.indexOf('fetch("/api/livekit/token"'),
    'validate client availability before allocating a cloud room');
  assert.ok(call.indexOf('applyCallVoiceConfig(body.voice_config)') < call.indexOf('new Room('),
    'synchronize voice mode before publishing microphone audio');

  const voice = vm.createContext({
    document: { querySelector: () => null },
    Date: { now: () => 2000 },
  });
  const section = (from, to) => app.slice(app.indexOf(from), app.indexOf(to, app.indexOf(from)));
  vm.runInContext(`
    let voiceCallMode = "stable";
    let voiceEchoGateHangoverMs = 1000;
    let voiceEchoTextFilterEnabled = true;
    let esp32SpeakerRelayStats = { lastVoiceAt: 1500 };
    const ESP32_CALL_MIC_ECHO_MUTE_DEFAULT_HANGOVER_MS = 1000;
    ${section('  const ESP32_AUDIO_PROFILE_BY_CALL_MODE =', '  const ESP32_WAKE_AUDIO_PROFILE')}
    ${section('  const VOICE_CALL_MODE_LABELS =', '  function ')}
  `, voice);
  // Extract the functions by their next declaration rather than running the UI.
  for (const name of ['normalizeVoiceCallMode', 'syncCfgSegmentedControl', 'applyCallVoiceConfig', 'applyVoiceRuntimeField', 'shouldMuteEsp32MicForSpeakerEcho']) {
    const pos = app.indexOf(`  function ${name}(`);
    const next = app.indexOf('\n  }', pos) + '\n  }'.length;
    vm.runInContext(app.slice(pos, next), voice);
  }
  assert.equal(voice.shouldMuteEsp32MicForSpeakerEcho(), true);
  for (const mode of ['interruptible', 'esp32_aec']) {
    voice.applyCallVoiceConfig({ call_mode: mode, echo_gate_hangover_ms: 1000, echo_text_filter_enabled: true });
    assert.equal(voice.shouldMuteEsp32MicForSpeakerEcho(), false, 'speech must pass in interruptible modes even during playback');
  }
  voice.applyCallVoiceConfig({ call_mode: 'stable', echo_gate_hangover_ms: 100, echo_text_filter_enabled: false });
  assert.equal(voice.shouldMuteEsp32MicForSpeakerEcho(), false, 'stable mode must resume mic after configured hangover');
  assert.throws(() => voice.applyCallVoiceConfig(undefined), /通话配置未同步/);
  assert.throws(() => voice.applyCallVoiceConfig({ call_mode: 'unknown' }), /通话配置未同步/);
  console.log('LiveKit local bundle, loading, authoritative voice mode and echo gate: passed');
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
