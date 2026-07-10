class Esp32PcmProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buffer = new Float32Array(0);
    this.port.onmessage = (event) => {
      const incoming = event.data;
      const merged = new Float32Array(this._buffer.length + incoming.length);
      merged.set(this._buffer);
      merged.set(incoming, this._buffer.length);
      this._buffer = merged;
    };
  }

  process(inputs, outputs) {
    const out = outputs[0][0];
    if (!out) return true;
    const needed = out.length;
    if (this._buffer.length >= needed) {
      out.set(this._buffer.subarray(0, needed));
      this._buffer = this._buffer.subarray(needed);
    } else {
      out.set(this._buffer);
      out.fill(0, this._buffer.length);
      this._buffer = new Float32Array(0);
    }
    return true;
  }
}

registerProcessor("esp32-pcm-processor", Esp32PcmProcessor);
