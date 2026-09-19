class MicCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetSampleRate = 16000;
    this.bufferSize = 32768;
    this.buffer = new Float32Array(this.bufferSize);
    this.writeIndex = 0;
    this.readIndex = 0;
    this.outputChunkSize = 512; // ~32ms at 16kHz
    this.outputBuffer = new Float32Array(this.outputChunkSize);
    this.outputIndex = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;

    const channelData = input[0];
    const len = channelData.length;

    // Push incoming hardware samples into zero-allocation ring buffer
    for (let i = 0; i < len; i++) {
      this.buffer[this.writeIndex % this.bufferSize] = channelData[i];
      this.writeIndex++;
    }

    const ratio = sampleRate / this.targetSampleRate;

    // Downsample via linear interpolation to target 16kHz rate
    while ((this.writeIndex - this.readIndex) >= ratio) {
      const index = Math.floor(this.readIndex);
      const frac = this.readIndex - index;

      const s0 = this.buffer[index % this.bufferSize];
      const s1 = this.buffer[(index + 1) % this.bufferSize];
      const interpolated = s0 + frac * (s1 - s0);

      const clamped = Math.max(-1, Math.min(1, interpolated));
      this.outputBuffer[this.outputIndex++] = clamped;
      this.readIndex += ratio;

      if (this.outputIndex >= this.outputChunkSize) {
        const pcm16 = new Int16Array(this.outputChunkSize);
        for (let j = 0; j < this.outputChunkSize; j++) {
          const s = this.outputBuffer[j];
          pcm16[j] = s < 0 ? s * 32768 : s * 32767;
        }
        this.outputIndex = 0;
        this.port.postMessage(pcm16.buffer, [pcm16.buffer]);
      }
    }

    // Keep ring buffer pointers bounded
    if (this.readIndex > 1000000) {
      const offset = Math.floor(this.readIndex);
      this.readIndex -= offset;
      this.writeIndex -= offset;
    }

    return true;
  }
}

registerProcessor('mic-capture-processor', MicCaptureProcessor);
