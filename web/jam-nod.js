const VISION = 'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.21';
const FACE_MODEL = 'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task';
const DEGREES = 180 / Math.PI;

export function addressedCommand(transcript) {
  const address = /^\s*(?:乐队|jam\s*mate\b)[\p{P}\s]*/iu.exec(transcript);
  return address ? transcript.slice(address[0].length).trim() : '';
}

export function facePitch(result) {
  const faces = result.facialTransformationMatrixes;
  if (faces?.length !== 1) return null;
  // The column-major third column is the face's forward direction.
  const data = faces[0]?.data;
  if (data?.length !== 16) return null;
  const [x, y, z] = data.slice(8, 11);
  if (![x, y, z].every(Number.isFinite) || z <= 0
      || Math.abs(Math.atan2(x, Math.hypot(y, z)) * DEGREES) > 30) return null;
  return Math.atan2(-y, z) * DEGREES;
}

export class HeadNod {
  constructor() {
    this.reset();
  }

  reset() {
    this.neutral = null;
    this.stillSince = null;
    this.downSince = null;
    this.deepEnough = false;
  }

  update(angle, now) {
    if (angle === null) {
      this.reset();
      return 'face';
    }
    if (this.neutral == null || (this.downSince == null && now - this.stillSince < 280)) {
      if (this.neutral == null || Math.abs(angle - this.neutral) > 4) {
        this.neutral = angle;
        this.stillSince = now;
      }
      return 'settle';
    }
    const delta = angle - this.neutral;
    if (this.downSince == null) {
      if (delta >= 12) this.downSince = now;
      else if (Math.abs(delta) < 4) this.neutral += delta * .12;
      return 'ready';
    }
    if (now - this.downSince > 1400 || delta < -8) {
      this.reset();
      return 'settle';
    }
    if (delta >= 12 && now - this.downSince >= 85) this.deepEnough = true;
    if (Math.abs(delta) <= 5) {
      const detected = this.deepEnough;
      this.reset();
      return detected ? 'confirmed' : 'settle';
    }
    return 'down';
  }
}

export function initNodConfirmation({reportError}) {
  const toggle = document.getElementById('jam-nod-toggle');
  const panel = document.getElementById('jam-nod');
  const video = document.getElementById('jam-nod-camera');
  const status = document.getElementById('jam-nod-status');
  const review = document.getElementById('jam-nod-review');
  const text = document.getElementById('jam-nod-text');
  const send = document.getElementById('jam-nod-send');
  const cancel = document.getElementById('jam-nod-cancel');
  const messages = {
    face: '请让一张正脸进入画面，或点「发送这句」。',
    settle: '请先面向摄像头保持片刻。',
    ready: '转录正确的话，低头再抬回原位。',
    down: '请把头抬回原位。',
  };
  let mode = null;
  let available = false;
  let pending = null;
  let frame = 0;
  let revision = 0;

  function show(message) {
    if (status.textContent !== message) status.textContent = message;
  }

  function clear() {
    revision += 1;
    pending = null;
    text.textContent = '';
    review.hidden = true;
    if (mode?.tracker) show('等待语音指令。');
  }

  function closeCamera(active) {
    cancelAnimationFrame(frame);
    active.stream?.getTracks().forEach(track => track.stop());
    active.tracker?.close();
    active.stream = active.tracker = null;
    video.srcObject = null;
    video.hidden = true;
  }

  function close() {
    if (mode) closeCamera(mode);
    mode = null;
    clear();
    panel.hidden = true;
    toggle.setAttribute('aria-pressed', 'false');
    toggle.textContent = '开启点头确认';
  }

  async function submit() {
    const item = pending;
    if (!mode || !item) return;
    clear();
    const submission = revision;
    show('正在发送确认的语音指令…');
    if (await item.send()) {
      if (mode === item.mode && submission === revision) show('已发送，等待下一句。');
    } else if (mode === item.mode && submission === revision) {
      offer(item);
      show('发送失败，可以点「发送这句」重试。');
    }
  }

  function offer({transcript, send: sendCommand}) {
    if (!mode) return false;
    revision += 1;
    pending = {transcript, send: sendCommand, mode};
    mode.nod.reset();
    text.textContent = transcript;
    review.hidden = false;
    show(mode.tracker ? messages.settle : '可点「发送这句」手动确认。');
    return true;
  }

  function cameraFailed(active, error) {
    if (mode !== active) return;
    closeCamera(active);
    reportError('点头确认', error);
    show(error.name === 'NotAllowedError'
      ? '摄像头权限未授权，仍可点「发送这句」。'
      : '摄像头或识别模型未能启动，仍可点「发送这句」。');
  }

  function scan(active) {
    let last = -Infinity;
    let videoTime = -1;
    function tick(now) {
      if (mode !== active) return;
      frame = requestAnimationFrame(tick);
      if (!pending || video.readyState < 2 || video.currentTime === videoTime || now - last < 75) return;
      last = now;
      videoTime = video.currentTime;
      try {
        const angle = facePitch(active.tracker.detectForVideo(video, now));
        const step = active.nod.update(angle, now);
        if (step === 'confirmed') submit();
        else show(messages[step]);
      } catch (error) {
        cameraFailed(active, error);
      }
    }
    frame = requestAnimationFrame(tick);
  }

  async function start() {
    const active = {nod: new HeadNod()};
    mode = active;
    panel.hidden = false;
    toggle.setAttribute('aria-pressed', 'true');
    toggle.textContent = '退出点头确认';
    show('正在开启摄像头和加载识别模型…');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: false, video: {width: {ideal: 320}, height: {ideal: 240}, facingMode: 'user'},
      });
      if (mode !== active) {
        stream.getTracks().forEach(track => track.stop());
        return;
      }
      active.stream = stream;
      video.srcObject = stream;
      video.hidden = false;
      await video.play();
      const {FilesetResolver, FaceLandmarker} = await import(VISION + '/vision_bundle.mjs');
      if (mode !== active) return;
      const fileset = await FilesetResolver.forVisionTasks(VISION + '/wasm');
      if (mode !== active) return;
      const tracker = await FaceLandmarker.createFromOptions(fileset, {
        baseOptions: {modelAssetPath: FACE_MODEL, delegate: 'CPU'},
        runningMode: 'VIDEO', numFaces: 2, outputFacialTransformationMatrixes: true,
      });
      if (mode !== active) {
        tracker.close();
        return;
      }
      active.tracker = tracker;
      stream.getVideoTracks().forEach(track => track.addEventListener('ended', () => {
        cameraFailed(active, new Error('摄像头已断开。'));
      }));
      show(pending ? messages.settle : '等待语音指令。');
      scan(active);
    } catch (error) {
      cameraFailed(active, error);
    }
  }

  toggle.addEventListener('click', () => {
    if (mode) close();
    else if (available) start();
  });
  send.addEventListener('click', submit);
  cancel.addEventListener('click', clear);
  return {
    enabled: () => Boolean(mode),
    setAvailable(value) {
      available = value && Boolean(navigator.mediaDevices?.getUserMedia);
      toggle.disabled = !available;
      if (!available && mode) close();
    },
    offer,
    clear,
  };
}
