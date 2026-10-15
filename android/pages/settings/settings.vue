<template>
  <view class="settings">
    <text class="hint">手机和电脑要在同一个局域网。这里填电脑的局域网 IP，不要填 127.0.0.1。</text>
    <text class="label">电脑 IP</text>
    <input class="field" v-model="host" placeholder="例如 192.168.1.20" />
    <text class="label">端口</text>
    <input class="field" v-model="port" type="number" placeholder="8765" />
    <view class="save" @tap="save">保存</view>
  </view>
</template>

<script>
import { getServer, setServer } from "../../common/config.js";
import { ensure } from "../../common/panel.js";

export default {
  data() {
    return {
      host: "",
      port: "8765",
    };
  },
  onShow() {
    // #ifdef APP-PLUS
    plus.navigator.setFullscreen(false);
    // #endif
    const server = getServer();
    this.host = server.host;
    this.port = server.port;
  },
  methods: {
    save() {
      const host = this.host.trim();
      const port = String(this.port).trim();
      const portNumber = Number(port);
      if (!host || !Number.isInteger(portNumber) || portNumber < 1 || portNumber > 65535) {
        uni.showToast({
          title: "请填写有效的 IP 和端口",
          icon: "none",
        });
        return;
      }
      setServer(host, port);
      ensure();
      uni.showToast({
        title: "已保存",
        icon: "none",
      });
      setTimeout(() => {
        uni.navigateBack();
      }, 250);
    },
  },
};
</script>

<style>
.settings {
  min-height: 100vh;
  padding: 40rpx 36rpx;
  background: #050505;
  color: #f3f3f3;
}

.hint {
  display: block;
  font-size: 28rpx;
  line-height: 1.6;
  color: #c8c8c8;
}

.label {
  display: block;
  margin-top: 36rpx;
  margin-bottom: 12rpx;
  font-size: 28rpx;
}

.field {
  height: 88rpx;
  padding: 0 24rpx;
  background: #161616;
  border: 2rpx solid #333;
  color: #f3f3f3;
  font-size: 32rpx;
}

.save {
  margin-top: 48rpx;
  height: 96rpx;
  line-height: 96rpx;
  text-align: center;
  background: #1d6b3a;
  font-size: 32rpx;
}
</style>
