import { getServer } from "./config.js";

const STATUS_LABELS = {
  idle: "空闲",
  thinking: "思考中",
  outputting: "输出中",
  waiting_confirm: "等待审批",
  running: "执行中",
  done: "已完成",
  error: "异常",
  timeout: "审批超时",
  rejected: "已拒绝",
};

const state = {
  connected: false,
  status: "idle",
  summary: "等待 Agent 任务",
  detail: "",
  requestId: "",
  extra: {
    command: "",
    file_path: "",
    risk_level: "normal",
    tool: "",
  },
};

const listeners = new Set();
let reconnectTimer = null;
let socketOpen = false;
let connecting = false;
let listenersBound = false;
let activeUrl = "";
let wantConnect = false;
let audio = null;

function snapshot() {
  return {
    connected: state.connected,
    status: state.status,
    statusLabel: STATUS_LABELS[state.status] || state.status,
    summary: state.summary,
    detail: state.detail,
    requestId: state.requestId,
    extra: { ...state.extra },
  };
}

function emit() {
  const current = snapshot();
  listeners.forEach((listener) => listener(current));
}

function serverUrl() {
  const server = getServer();
  return `ws://${server.host}:${server.port}`;
}

function setAwake(on) {
  const enabled = !!on;
  try {
    if (typeof plus !== "undefined" && plus.device && plus.device.setWakelock) {
      plus.device.setWakelock(enabled);
    }
  } catch (error) {
    // The standard runtime reports this only after the package declares WAKE_LOCK.
  }
  uni.setKeepScreenOn({ keepScreenOn: enabled });
}

function scheduleReconnect() {
  connecting = false;
  if (!wantConnect || reconnectTimer) {
    return;
  }
  state.connected = false;
  setAwake(false);
  emit();
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    if (wantConnect && !socketOpen) {
      openSocket();
    }
  }, 2000);
}

function applyState(payload) {
  state.status = payload.status || "idle";
  state.summary = payload.summary || "";
  state.detail = payload.detail || "";
  state.requestId = payload.request_id || "";
  const extra = payload.extra || {};
  state.extra = {
    command: extra.command || "",
    file_path: extra.file_path || "",
    risk_level: extra.risk_level || "normal",
    tool: extra.tool || "",
  };
  emit();
}

function bindListeners() {
  if (listenersBound) {
    return;
  }
  listenersBound = true;
  uni.onSocketOpen(() => {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    connecting = false;
    socketOpen = true;
    state.connected = true;
    setAwake(true);
    emit();
  });
  uni.onSocketMessage((event) => {
    try {
      const message = JSON.parse(event.data);
      if (message && message.type === "state_update" && message.payload) {
        applyState(message.payload);
      }
    } catch (error) {
      // Ignore malformed pushes and keep the last good panel state.
    }
  });
  uni.onSocketClose(() => {
    socketOpen = false;
    scheduleReconnect();
  });
  uni.onSocketError(() => {
    socketOpen = false;
    scheduleReconnect();
  });
}

function openSocket() {
  if (connecting || socketOpen) {
    return;
  }
  connecting = true;
  activeUrl = serverUrl();
  uni.connectSocket({
    url: activeUrl,
    fail() {
      scheduleReconnect();
    },
  });
}

export function subscribe(listener) {
  listeners.add(listener);
  listener(snapshot());
  return () => listeners.delete(listener);
}

export function ensure() {
  wantConnect = true;
  bindListeners();
  const nextUrl = serverUrl();
  if ((socketOpen || connecting) && nextUrl === activeUrl) {
    return;
  }
  if (socketOpen) {
    activeUrl = nextUrl;
    uni.closeSocket({});
    return;
  }
  openSocket();
}

export function sendAction(action, remark) {
  if (!socketOpen) {
    return false;
  }
  uni.sendSocketMessage({
    data: JSON.stringify({
      type: "user_action",
      request_id: state.requestId,
      action,
      remark: remark || "",
    }),
  });
  return true;
}

export function playClick() {
  if (!audio) {
    audio = uni.createInnerAudioContext();
    audio.src = "/static/click.wav";
  }
  try {
    audio.seek(0);
  } catch (error) {
    // seek can fail before the first playback prepares the file.
  }
  audio.play();
}

export function vibrateShort() {
  try {
    if (typeof plus !== "undefined" && plus.device && plus.device.vibrate) {
      plus.device.vibrate(50);
      return;
    }
  } catch (error) {
    // Fall through to the cross-platform vibration call.
  }
  uni.vibrateShort({});
}
