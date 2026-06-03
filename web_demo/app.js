"use strict";

const WS_URL = "ws://127.0.0.1:8766/realtime";
const TARGET_SAMPLE_RATE = 16000;

const toggleBtn = document.querySelector("#toggleBtn");
const clearBtn = document.querySelector("#clearBtn");
const statusText = document.querySelector("#statusText");
const transcript = document.querySelector("#transcript");
const levelBar = document.querySelector("#levelBar");

let audioContext = null;
let mediaStream = null;
let sourceNode = null;
let processorNode = null;
let socket = null;
let downsampler = null;
let running = false;
let speaking = false;

setEmpty();

toggleBtn.addEventListener("click", async () => {
  if (running) {
    await stop();
    return;
  }
  await start();
});

clearBtn.addEventListener("click", () => {
  transcript.innerHTML = "";
  setEmpty();
});

async function start() {
  toggleBtn.disabled = true;
  setStatus("starting");
  try {
    socket = await connectSocket();
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
      video: false,
    });
    audioContext = new AudioContext();
    downsampler = new Downsampler(audioContext.sampleRate, TARGET_SAMPLE_RATE);
    sourceNode = audioContext.createMediaStreamSource(mediaStream);
    processorNode = audioContext.createScriptProcessor(4096, 1, 1);
    processorNode.onaudioprocess = handleAudio;
    sourceNode.connect(processorNode);
    processorNode.connect(audioContext.destination);
    running = true;
    toggleBtn.textContent = "Stop";
    setStatus("listening");
  } catch (error) {
    appendLine(String(error.message || error), "error");
    await cleanup();
    setStatus("idle");
  } finally {
    toggleBtn.disabled = false;
  }
}

async function stop() {
  toggleBtn.disabled = true;
  setStatus("stopping");
  if (socket && socket.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "end" }));
  }
  await cleanup();
  setStatus("idle");
  toggleBtn.disabled = false;
}

function connectSocket() {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(WS_URL);
    ws.binaryType = "arraybuffer";

    const timer = window.setTimeout(() => {
      ws.close();
      reject(new Error("WebSocket timeout"));
    }, 5000);

    ws.addEventListener("open", () => {
      window.clearTimeout(timer);
      resolve(ws);
    });

    ws.addEventListener("message", (event) => {
      handleServerEvent(event.data);
    });

    ws.addEventListener("close", () => {
      if (running) {
        appendLine("connection closed", "event");
        cleanup();
        setStatus("idle");
      }
    });

    ws.addEventListener("error", () => {
      reject(new Error(`Cannot connect to ${WS_URL}`));
    });
  });
}

function handleAudio(event) {
  if (!running || !socket || socket.readyState !== WebSocket.OPEN) {
    return;
  }
  const input = event.inputBuffer.getChannelData(0);
  updateLevel(input);
  const pcm = floatToPCM16(downsampler.process(input));
  if (pcm.byteLength > 0) {
    socket.send(pcm);
  }
}

function handleServerEvent(raw) {
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch {
    appendLine(String(raw), "event");
    return;
  }

  if (payload.type === "ready") {
    setStatus("ready");
    return;
  }
  if (payload.type === "speech_start") {
    speaking = true;
    setStatus("speaking");
    return;
  }
  if (payload.type === "speech_end") {
    speaking = false;
    setStatus("transcribing");
    return;
  }
  if (payload.type === "transcript_final") {
    speaking = false;
    setStatus(running ? "listening" : "idle");
    const text = normalizeTranscript(payload.text);
    if (payload.status === "ok" && text) {
      appendTranscriptSegment(text);
    }
    return;
  }
  if (payload.type === "error") {
    appendLine(payload.error || "unknown error", "error");
    setStatus(running ? "listening" : "idle");
    return;
  }

  appendLine(JSON.stringify(payload), "event");
}

async function cleanup() {
  running = false;
  speaking = false;

  if (processorNode) {
    processorNode.disconnect();
    processorNode.onaudioprocess = null;
    processorNode = null;
  }
  if (sourceNode) {
    sourceNode.disconnect();
    sourceNode = null;
  }
  if (mediaStream) {
    for (const track of mediaStream.getTracks()) {
      track.stop();
    }
    mediaStream = null;
  }
  if (audioContext) {
    await audioContext.close();
    audioContext = null;
  }
  if (socket) {
    if (socket.readyState === WebSocket.OPEN) {
      socket.close();
    }
    socket = null;
  }
  downsampler = null;
  levelBar.style.width = "0%";
  toggleBtn.textContent = "Start";
}

function setStatus(value) {
  statusText.textContent = speaking ? "speaking" : value;
}

function appendLine(text, kind) {
  const empty = transcript.querySelector(".empty");
  if (empty) {
    empty.remove();
  }
  const row = document.createElement("div");
  row.className = `line ${kind}`;
  const time = document.createElement("div");
  time.className = "time";
  time.textContent = new Date().toLocaleTimeString();
  const body = document.createElement("div");
  body.className = "text";
  body.textContent = text;
  row.append(time, body);
  transcript.append(row);
  transcript.scrollTop = transcript.scrollHeight;
}

function appendTranscriptSegment(text) {
  const empty = transcript.querySelector(".empty");
  if (empty) {
    empty.remove();
  }

  const row = document.createElement("div");
  row.className = "line text";

  const time = document.createElement("div");
  time.className = "time";
  time.textContent = new Date().toLocaleTimeString();

  const body = document.createElement("div");
  body.className = "text";

  const content = document.createElement("div");
  content.className = "transcript-content";
  content.textContent = text;
  body.append(content);

  row.append(time, body);
  transcript.append(row);
  transcript.scrollTop = transcript.scrollHeight;
}

function normalizeTranscript(text) {
  return String(text || "")
    .trim()
    .replace(/\s+/g, " ");
}

function setEmpty() {
  if (transcript.children.length === 0) {
    const div = document.createElement("div");
    div.className = "empty";
    div.textContent = "No transcript";
    transcript.append(div);
  }
}

function updateLevel(samples) {
  let sum = 0;
  for (let i = 0; i < samples.length; i += 1) {
    sum += samples[i] * samples[i];
  }
  const rms = Math.sqrt(sum / samples.length);
  const level = Math.min(100, Math.round(rms * 280));
  levelBar.style.width = `${level}%`;
}

function floatToPCM16(samples) {
  const buffer = new ArrayBuffer(samples.length * 2);
  const view = new DataView(buffer);
  for (let i = 0; i < samples.length; i += 1) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    const value = clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff;
    view.setInt16(i * 2, value, true);
  }
  return buffer;
}

class Downsampler {
  constructor(inputRate, outputRate) {
    this.inputRate = inputRate;
    this.outputRate = outputRate;
    this.ratio = inputRate / outputRate;
    this.tail = new Float32Array(0);
    this.offset = 0;
  }

  process(input) {
    const data = concatFloat32(this.tail, input);
    const output = [];
    let index = 0;

    while (true) {
      const pos = this.offset + index * this.ratio;
      const left = Math.floor(pos);
      const right = left + 1;
      if (right >= data.length) {
        const retain = Math.max(0, left);
        this.tail = data.slice(retain);
        this.offset = pos - retain;
        break;
      }
      const frac = pos - left;
      output.push(data[left] + (data[right] - data[left]) * frac);
      index += 1;
    }

    return Float32Array.from(output);
  }
}

function concatFloat32(a, b) {
  if (a.length === 0) {
    return b;
  }
  const out = new Float32Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}
