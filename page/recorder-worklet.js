// 按块传递单声道采样；停止时先排空尾部，再通知主线程编码。
class PCMRecorder extends AudioWorkletProcessor {
  constructor(options) {
    super();
    this.remaining = options.processorOptions.maxFrames;
    this.buffer = new Float32Array(2048);
    this.used = 0;
    this.finished = false;
    this.port.onmessage = event => {
      if (event.data === 'finish') {
        this.finished = true;
        this.flush(true);
      }
    };
  }
  flush(done = false) {
    const samples = this.buffer.slice(0, this.used);
    this.port.postMessage({samples, done}, [samples.buffer]);
    this.used = 0;
  }
  process(inputs) {
    if (this.finished) return false;
    const channel = inputs[0]?.[0];
    if (channel) {
      for (const sample of channel) {
        if (this.remaining-- <= 0) {
          this.finished = true;
          this.flush(true);
          return false;
        }
        this.buffer[this.used++] = sample;
        if (this.used === this.buffer.length) this.flush();
      }
    }
    return true;
  }
}
registerProcessor('pcm-recorder', PCMRecorder);
