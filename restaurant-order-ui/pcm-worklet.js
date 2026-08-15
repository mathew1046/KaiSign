class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.targetRate = 16000;
    this.step = sampleRate / this.targetRate;
    this.buffer = [];
    this.position = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0] || !input[0].length) return true;
    const channel = input[0];
    while (this.position < channel.length) {
      const index = Math.floor(this.position);
      const next = Math.min(index + 1, channel.length - 1);
      const fraction = this.position - index;
      const sample = channel[index] + (channel[next] - channel[index]) * fraction;
      const clipped = Math.max(-1, Math.min(1, sample));
      this.buffer.push(clipped < 0 ? clipped * 32768 : clipped * 32767);
      this.position += this.step;
    }
    this.position -= channel.length;
    if (this.buffer.length >= 1600) {
      const pcm = new Int16Array(this.buffer.splice(0, 1600));
      this.port.postMessage(pcm.buffer, [pcm.buffer]);
    }
    return true;
  }
}

registerProcessor("pcm-capture", PcmCaptureProcessor);
