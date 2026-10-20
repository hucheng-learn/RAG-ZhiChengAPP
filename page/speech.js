'use strict';
(() => {
  // 输出与供应商约定一致的 16 kHz、16 bit、单声道 PCM WAV。
  function encodeWav(chunks, inputRate, maxSeconds) {
    const length = Math.min(chunks.reduce((sum, chunk) => sum + chunk.length, 0),
      Math.floor(inputRate * maxSeconds));
    const samples = new Float32Array(length);
    let offset = 0;
    for (const chunk of chunks) {
      const count = Math.min(chunk.length, length - offset);
      if (count <= 0) break;
      samples.set(chunk.subarray(0, count), offset);
      offset += count;
    }
    const rate = 16000, count = Math.floor(length * rate / inputRate);
    const buffer = new ArrayBuffer(44 + count * 2), view = new DataView(buffer);
    const ascii = (at, text) => [...text].forEach((char, i) => view.setUint8(at + i, char.charCodeAt(0)));
    ascii(0, 'RIFF'); view.setUint32(4, buffer.byteLength - 8, true); ascii(8, 'WAVE');
    ascii(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
    view.setUint16(22, 1, true); view.setUint32(24, rate, true); view.setUint32(28, rate * 2, true);
    view.setUint16(32, 2, true); view.setUint16(34, 16, true); ascii(36, 'data');
    view.setUint32(40, count * 2, true);
    // 对采样窗口求均值，降低从 44.1/48 kHz 降采样时的高频混叠。
    for (let i = 0; i < count; i++) {
      const start = i * inputRate / rate, end = (i + 1) * inputRate / rate;
      let sum = 0;
      for (let j = Math.floor(start); j < Math.ceil(end) && j < length; j++) {
        sum += samples[j] * (Math.min(j + 1, end) - Math.max(j, start));
      }
      const sample = Math.max(-1, Math.min(1, sum / (end - start)));
      view.setInt16(44 + i * 2, Math.round(sample * (sample < 0 ? 32768 : 32767)), true);
    }
    return new Blob([buffer], {type: 'audio/wav'});
  }

  function create({button, cancelButton, status, question, send, isBusy, notify, transcribe}) {
    let active = null, available = false, maxSeconds = 120;
    const supported = !!(globalThis.isSecureContext && navigator.mediaDevices?.getUserMedia &&
      globalThis.AudioContext && globalThis.AudioWorkletNode);
    function render() {
      const phase = active?.phase;
      button.disabled = !available || (!!phase && phase !== 'recording');
      button.textContent = phase === 'recording' ? '停止并识别' : phase === 'transcribing' ? '识别中…' :
        phase === 'opening' ? '等待麦克风…' : '语音输入';
      button.setAttribute('aria-pressed', String(phase === 'recording'));
      cancelButton.hidden = !active;
      send.disabled = !!active || isBusy();
    }
    function release(session) {
      clearInterval(session.timer);
      session.stream?.getTracks().forEach(track => track.stop());
      session.source?.disconnect();
      session.node?.disconnect();
      if (session.context && session.context.state !== 'closed') session.context.close().catch(() => {});
    }
    function cancel() {
      if (!active) return;
      const session = active;
      active = null;
      session.controller?.abort();
      session.finish?.(new Error('cancelled'));
      release(session);
      session.chunks = [];
      status.textContent = '已取消语音输入';
      render();
    }
    async function stop() {
      const session = active;
      if (!session || session.phase !== 'recording') return;
      session.phase = 'transcribing';
      clearInterval(session.timer);
      status.textContent = '正在识别录音，请稍候…';
      render();
      try {
        if (!session.drained) await new Promise((resolve, reject) => {
          const timeout = setTimeout(() => reject(new Error('录音读取超时，请重新录音。')), 3000);
          session.finish = error => { clearTimeout(timeout); error ? reject(error) : resolve(); };
          session.node.port.postMessage('finish');
        });
        const audio = encodeWav(session.chunks, session.context.sampleRate, maxSeconds);
        session.chunks = [];
        release(session);
        if (active !== session) return;
        if (audio.size <= 44) throw new Error('未录到声音，请重新录音。');
        session.controller = new AbortController();
        const {text} = await transcribe(audio, session.controller.signal);
        if (active !== session) return;
        const draft = question.value, combined = draft + (draft && !/\s$/.test(draft) ? '\n' : '') + text;
        if (combined.length > question.maxLength) {
          notify('识别完成，但草稿超出字数限制，请缩短后复制以下文字：' + text, 'warning');
          status.textContent = '文字超出输入框限制，识别结果已显示在提示中';
        } else {
          question.value = combined;
          question.dispatchEvent(new Event('input', {bubbles: true}));
          question.focus();
          status.textContent = '已填入识别文字，请核对后发送';
        }
      } catch (error) {
        if (active === session) {
          status.textContent = '语音识别失败，可重新录音';
          notify(error.message || '语音识别失败，请重试。', 'error');
        }
      } finally {
        release(session);
        if (active === session) { active = null; render(); }
      }
    }
    async function start() {
      if (active || !available) return;
      if (isBusy()) { notify('请等待当前操作结束后录音。', 'warning'); return; }
      const session = {phase: 'opening', chunks: []};
      active = session;
      status.textContent = '请允许浏览器使用麦克风';
      render();
      try {
        const stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1}, video: false});
        if (active !== session) { stream.getTracks().forEach(track => track.stop()); return; }
        session.stream = stream;
        session.context = new AudioContext();
        await session.context.audioWorklet.addModule('/assets/recorder-worklet.js?v=20261020-asr');
        if (active !== session) return;
        await session.context.resume();
        if (active !== session) return;
        session.node = new AudioWorkletNode(session.context, 'pcm-recorder',
          {numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1], channelCount: 1,
            processorOptions: {maxFrames: Math.floor(session.context.sampleRate * maxSeconds)}});
        session.node.port.onmessage = ({data}) => {
          if (active !== session) return;
          if (data.samples?.length) session.chunks.push(data.samples);
          if (data.done) {
            session.drained = true;
            if (session.phase === 'recording') void stop();
            else session.finish?.();
          }
        };
        session.node.onprocessorerror = () => {
          if (active === session) { cancel(); notify('录音设备异常，请重试。', 'error'); }
        };
        session.source = session.context.createMediaStreamSource(stream);
        session.source.connect(session.node);
        session.node.connect(session.context.destination); // 处理器输出静音，避免回声。
        for (const track of stream.getTracks()) track.onended = () => {
          if (active === session && session.phase === 'recording') {
            cancel(); notify('麦克风已断开，请重新录音。', 'error');
          }
        };
        session.phase = 'recording';
        const started = performance.now();
        const tick = () => {
          const elapsed = Math.floor((performance.now() - started) / 1000);
          status.textContent = `录音中 ${elapsed} 秒 / ${maxSeconds} 秒 · 停止后转文字`;
          if (elapsed >= maxSeconds) void stop();
        };
        session.timer = setInterval(tick, 250);
        tick(); render();
      } catch (error) {
        release(session);
        if (active === session) {
          active = null;
          status.textContent = '无法开始录音';
          notify(error.name === 'NotAllowedError' ? '麦克风权限被拒绝，请在浏览器设置中允许后重试。' :
            error.name === 'NotFoundError' ? '未找到麦克风，请连接设备后重试。' : '无法使用麦克风，请检查设备或浏览器设置。', 'error');
          render();
        }
      }
    }
    button.onclick = () => active?.phase === 'recording' ? stop() : start();
    cancelButton.onclick = cancel;
    globalThis.addEventListener('pagehide', cancel);
    return {
      get busy() { return !!active; },
      cancel,
      configure(config) {
        available = supported && !!config?.available;
        maxSeconds = config?.max_seconds || 120;
        button.title = !supported ? '语音输入需要 HTTPS 或本机地址，以及支持录音的浏览器' :
          !available ? '请先在服务端配置共享 API Key 和语音模型' : '录音后转文字，核对后发送';
        status.textContent = !supported ? '当前浏览器或访问地址不支持录音' :
          !available ? '语音输入待配置' : '';
        render();
      }
    };
  }
  globalThis.ChatSpeech = {create, encodeWav};
})();
