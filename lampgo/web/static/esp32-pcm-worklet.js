class Esp32PcmProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    // WS packets arrive in bursts (~100-200 ms on the hosted Wi-Fi link).
    // Playing each packet immediately inserts zeroes between syllables. Keep
    // a small jitter reserve, bounded independently from call duration.
    this._buffer = new Float32Array(Math.ceil(sampleRate * 0.48));
    this._targetSamples = Math.ceil(sampleRate * 0.12);
    this._read = 0;
    this._write = 0;
    this._queued = 0;
    this._priming = true;
    this._waitSamples = 0;
    this._bufferingSamples = 0;
    this._overflowSamples = 0;
    this._rebufferCount = 0;
    this._outputSamples = 0;
    this._windowSamples = 0;
    this._underrunSamples = 0;
    this._energy = 0;
    this._peak = 0;
    this.port.onmessage = (event) => {
      let incoming = event.data;
      if (!(incoming instanceof Float32Array) || !incoming.length) return;
      if (incoming.length > this._buffer.length) {
        this._overflowSamples += incoming.length - this._buffer.length;
        incoming = incoming.subarray(incoming.length - this._buffer.length);
      }
      const overflow = Math.max(0, this._queued + incoming.length - this._buffer.length);
      this._read = (this._read + overflow) % this._buffer.length;
      this._queued -= overflow;
      this._overflowSamples += overflow;
      const first = Math.min(incoming.length, this._buffer.length - this._write);
      this._buffer.set(incoming.subarray(0, first), this._write);
      this._buffer.set(incoming.subarray(first), 0);
      this._write = (this._write + incoming.length) % this._buffer.length;
      this._queued += incoming.length;
    };
  }

  process(inputs, outputs) {
    const out = outputs[0][0];
    if (!out) return true;
    const needed = out.length;
    out.fill(0);
    if (this._priming && this._queued > 0) {
      this._waitSamples += needed;
      // Also release a single short final packet after 120 ms; never wait
      // indefinitely for a full reserve or discard a quiet sentence opening.
      if (this._queued >= this._targetSamples || this._waitSamples >= this._targetSamples) {
        this._priming = false;
        this._waitSamples = 0;
      }
    }
    if (!this._priming) {
      const take = Math.min(needed, this._queued);
      const first = Math.min(take, this._buffer.length - this._read);
      out.set(this._buffer.subarray(this._read, this._read + first));
      out.set(this._buffer.subarray(0, take - first), first);
      this._read = (this._read + take) % this._buffer.length;
      this._queued -= take;
      this._underrunSamples += needed - take;
      if (take < needed) {
        this._priming = true;
        this._rebufferCount += 1;
      }
    } else if (this._queued > 0) {
      this._bufferingSamples += needed;
    } else {
      this._underrunSamples += needed;
    }
    this._outputSamples += needed;
    this._windowSamples += needed;
    for (let i = 0; i < out.length; i++) {
      this._energy += out[i] * out[i];
      this._peak = Math.max(this._peak, Math.abs(out[i]));
    }
    if (this._windowSamples >= sampleRate * 5) {
      this.port.postMessage({
        output_samples: this._outputSamples,
        underrun_samples: this._underrunSamples,
        queued_ms: this._queued * 1000 / sampleRate,
        buffering_samples: this._bufferingSamples,
        overflow_samples: this._overflowSamples,
        rebuffer_count: this._rebufferCount,
        rms: Math.sqrt(this._energy / this._windowSamples), peak: this._peak,
      });
      this._windowSamples = 0;
      this._energy = 0;
      this._peak = 0;
    }
    return true;
  }
}

registerProcessor("esp32-pcm-processor", Esp32PcmProcessor);
