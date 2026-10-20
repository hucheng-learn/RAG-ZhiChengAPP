const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function setup({getUserMedia, transcribe = async () => ({text: '识别文字'})} = {}) {
  const tracks = [{stopped: false, stop() {this.stopped = true;}}];
  let recorder;
  class FakeContext {
    constructor() {
      this.sampleRate = 48000;
      this.state = 'running';
      this.audioWorklet = {addModule: async () => {}};
    }
    async resume() {}
    async close() {this.state = 'closed';}
    createMediaStreamSource() {return {connect() {}, disconnect() {}};}
  }
  class FakeNode {
    constructor() {
      recorder = this;
      this.port = {postMessage: () => this.port.onmessage({data: {done: true}})};
    }
    connect() {}
    disconnect() {}
  }
  const context = vm.createContext({
    Blob, Float32Array, DataView, ArrayBuffer, AbortController, Event,
    AudioContext: FakeContext, AudioWorkletNode: FakeNode,
    isSecureContext: true, performance, setTimeout, clearTimeout, setInterval, clearInterval,
    addEventListener() {},
    navigator: {mediaDevices: {getUserMedia: getUserMedia || (async () => ({getTracks: () => tracks}))}}
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../../page/speech.js'), 'utf8'), context);
  const button = {setAttribute() {}}, cancelButton = {}, status = {}, send = {}, errors = [];
  const question = {value: '已有草稿', maxLength: 4000, focus() {}, dispatchEvent() {}};
  const speech = context.ChatSpeech.create({button, cancelButton, status, send, question,
    isBusy: () => false, notify: text => errors.push(text), transcribe});
  speech.configure({available: true, max_seconds: 120});
  return {speech, button, cancelButton, status, send, question, errors, tracks, context,
    recorder: () => recorder};
}

test('WAV header, resampling, clipping, and duration cap match backend contract', async () => {
  const {context} = setup();
  const audio = context.ChatSpeech.encodeWav([new Float32Array(48000).fill(2)], 48000, 0.5);
  const data = new DataView(await audio.arrayBuffer());
  assert.equal(audio.size, 44 + 8000 * 2);
  assert.equal(data.getUint32(24, true), 16000);
  assert.equal(data.getUint16(22, true), 1);
  assert.equal(data.getUint16(34, true), 16);
  assert.equal(data.getInt16(44, true), 32767);
});

test('recording appends to current draft, releases mic, and never submits', async () => {
  const ui = setup();
  await ui.button.onclick();
  assert.equal(ui.speech.busy, true);
  assert.equal(ui.send.disabled, true);
  ui.recorder().port.onmessage({data: {samples: new Float32Array(4800)}});
  ui.question.value = '录音期间修改的草稿';
  await ui.button.onclick();
  assert.equal(ui.question.value, '录音期间修改的草稿\n识别文字');
  assert.equal(ui.speech.busy, false);
  assert.equal(ui.send.disabled, false);
  assert.equal(ui.tracks[0].stopped, true);
  assert.equal(ui.errors.length, 0);
});

test('cancel during pending microphone permission stops late stream', async () => {
  let resolve;
  const ui = setup({getUserMedia: () => new Promise(done => {resolve = done;})});
  const pending = ui.button.onclick();
  ui.cancelButton.onclick();
  const track = {stopped: false, stop() {this.stopped = true;}};
  resolve({getTracks: () => [track]});
  await pending;
  assert.equal(track.stopped, true);
  assert.equal(ui.speech.busy, false);
  assert.equal(ui.question.value, '已有草稿');
});

test('cancel during transcription ignores late result', async () => {
  let resolve;
  const ui = setup({transcribe: () => new Promise(done => {resolve = done;})});
  await ui.button.onclick();
  ui.recorder().port.onmessage({data: {samples: new Float32Array(4800)}});
  const pending = ui.button.onclick();
  await new Promise(done => setImmediate(done));
  ui.cancelButton.onclick();
  resolve({text: '不应填入'});
  await pending;
  assert.equal(ui.question.value, '已有草稿');
  assert.equal(ui.speech.busy, false);
  assert.equal(ui.tracks[0].stopped, true);
});

test('permission rejection restores controls and preserves draft', async () => {
  const ui = setup({getUserMedia: async () => {throw {name: 'NotAllowedError'};}});
  await ui.button.onclick();
  assert.equal(ui.speech.busy, false);
  assert.equal(ui.send.disabled, false);
  assert.match(ui.errors[0], /权限被拒绝/);
  assert.equal(ui.question.value, '已有草稿');
});

test('long transcript preserves draft and shows result for recovery', async () => {
  const ui = setup({transcribe: async () => ({text: '识别'.repeat(3000)})});
  await ui.button.onclick();
  ui.recorder().port.onmessage({data: {samples: new Float32Array(4800)}});
  await ui.button.onclick();
  assert.equal(ui.question.value, '已有草稿');
  assert.match(ui.errors[0], /超出字数限制/);
  assert.match(ui.errors[0], /识别识别/);
});

test('worklet stops at sample limit and flushes final partial buffer', () => {
  let Recorder;
  const messages = [];
  const context = vm.createContext({Float32Array,
    AudioWorkletProcessor: class {constructor() {this.port = {postMessage: data => messages.push(data)};}},
    registerProcessor: (name, processor) => {Recorder = processor;}
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, '../../page/recorder-worklet.js'), 'utf8'), context);
  const recorder = new Recorder({processorOptions: {maxFrames: 100}});
  assert.equal(recorder.process([[new Float32Array(128).fill(0.5)]]), false);
  assert.equal(messages[0].done, true);
  assert.equal(messages[0].samples.length, 100);
});
