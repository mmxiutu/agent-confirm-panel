const STORAGE_KEY = "agent_panel_server";

export function getServer() {
  const saved = uni.getStorageSync(STORAGE_KEY);
  if (saved && saved.host && saved.port) {
    return {
      host: String(saved.host).trim(),
      port: String(saved.port).trim(),
    };
  }
  return {
    host: "127.0.0.1",
    port: "8765",
  };
}

export function setServer(host, port) {
  uni.setStorageSync(STORAGE_KEY, {
    host: String(host).trim(),
    port: String(port).trim(),
  });
}
