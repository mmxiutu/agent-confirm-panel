<template>
  <view class="page" :class="status">
    <view class="header">
      <view class="topline">
        <text class="link" :class="{ down: !connected }">{{ connected ? "已连接" : "连接断开" }}</text>
        <text class="gear" @tap="openSettings">设置</text>
      </view>
      <text class="status">{{ statusLabel }}</text>
      <text class="summary">{{ summary || "等待 Agent 任务" }}</text>
      <text v-if="requestId" class="request">{{ requestId }}</text>
    </view>

    <view v-if="detailOpen" class="detail">
      <text class="detail-line">详情 {{ detail || "无" }}</text>
      <text class="detail-line">命令 {{ extra.command || "无" }}</text>
      <text class="detail-line">路径 {{ extra.file_path || "无" }}</text>
      <text class="detail-line" :class="{ danger: extra.risk_level === 'high' }">
        风险 {{ riskLabel }}
      </text>
    </view>

    <view class="keys">
      <view class="key approve" @tap="onApprove">
        <text class="mark">✓</text>
        <text class="name">同意</text>
      </view>
      <view class="key reject" @tap="onReject">
        <text class="mark">×</text>
        <text class="name">拒绝</text>
      </view>
      <view class="key" @tap="onDetail">
        <text class="mark">☰</text>
        <text class="name">详情</text>
      </view>
      <view class="key stop" @tap="onStop">
        <text class="mark">■</text>
        <text class="name">终止</text>
      </view>
      <view class="key" @tap="onVoice">
        <text class="mark">♪</text>
        <text class="name">语音</text>
      </view>
    </view>
  </view>
</template>

<script>
import { ensure, playClick, sendAction, subscribe, vibrateShort } from "../../common/panel.js";

const RISK_LABELS = {
  normal: "普通",
  low: "低",
  high: "高",
};

export default {
  data() {
    return {
      connected: false,
      status: "idle",
      statusLabel: "空闲",
      summary: "等待 Agent 任务",
      detail: "",
      requestId: "",
      extra: {
        command: "",
        file_path: "",
        risk_level: "normal",
        tool: "",
      },
      detailOpen: false,
      unsubscribe: null,
    };
  },
  computed: {
    riskLabel() {
      return RISK_LABELS[this.extra.risk_level] || this.extra.risk_level || "普通";
    },
  },
  onLoad() {
    this.unsubscribe = subscribe((current) => {
      this.connected = current.connected;
      this.status = current.status;
      this.statusLabel = current.statusLabel;
      this.summary = current.summary;
      this.detail = current.detail;
      this.requestId = current.requestId;
      this.extra = current.extra;
    });
  },
  onShow() {
    // #ifdef APP-PLUS
    plus.navigator.setFullscreen(true);
    // #endif
    ensure();
  },
  onHide() {
    // #ifdef APP-PLUS
    plus.navigator.setFullscreen(false);
    // #endif
  },
  onUnload() {
    if (this.unsubscribe) {
      this.unsubscribe();
    }
  },
  methods: {
    feedback() {
      playClick();
      vibrateShort();
    },
    requirePending() {
      if (this.connected && this.status === "waiting_confirm" && this.requestId) {
        return true;
      }
      uni.showToast({
        title: "当前没有待审批事项",
        icon: "none",
      });
      return false;
    },
    onApprove() {
      this.feedback();
      if (!this.requirePending()) {
        return;
      }
      sendAction("approve");
    },
    onReject() {
      this.feedback();
      if (!this.requirePending()) {
        return;
      }
      sendAction("reject");
    },
    onStop() {
      this.feedback();
      if (!this.requirePending()) {
        return;
      }
      sendAction("stop_task");
    },
    onDetail() {
      this.feedback();
      this.detailOpen = !this.detailOpen;
      if (this.requestId) {
        sendAction("view_detail");
      }
    },
    onVoice() {
      this.feedback();
      uni.showToast({
        title: "语音监听尚未实现",
        icon: "none",
      });
    },
    openSettings() {
      uni.navigateTo({ url: "/pages/settings/settings" });
    },
  },
};
</script>

<style>
.page {
  height: 100vh;
  display: flex;
  flex-direction: column;
  background: #2a2a2a;
  color: #f5f5f5;
}

.page.waiting_confirm {
  background: #8a2e12;
}

.page.error,
.page.timeout,
.page.rejected {
  background: #7a1d1d;
}

.page.done {
  background: #1d6b3a;
}

.page.thinking {
  background: #1a3f78;
}

.page.outputting,
.page.running {
  background: #0e5c66;
}

.page.idle {
  background: #2a2a2a;
}

.header {
  flex: 1;
  padding: 48rpx 36rpx 24rpx;
  display: flex;
  flex-direction: column;
}

.topline {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.link {
  font-size: 28rpx;
  letter-spacing: 2rpx;
}

.link.down {
  color: #ffd0d0;
  font-weight: 700;
}

.gear {
  font-size: 28rpx;
  padding: 12rpx 8rpx;
}

.status {
  margin-top: 48rpx;
  font-size: 84rpx;
  font-weight: 700;
  line-height: 1.1;
}

.summary {
  margin-top: 28rpx;
  font-size: 36rpx;
  line-height: 1.4;
}

.request {
  margin-top: 24rpx;
  font-size: 22rpx;
  opacity: 0.7;
}

.detail {
  margin: 0 24rpx 24rpx;
  padding: 24rpx;
  background: rgba(0, 0, 0, 0.35);
  border: 2rpx solid #000;
}

.detail-line {
  display: block;
  font-size: 28rpx;
  line-height: 1.6;
  word-break: break-all;
}

.detail-line.danger {
  color: #ffd27a;
  font-weight: 700;
}

.keys {
  height: 220rpx;
  display: flex;
  border-top: 4rpx solid #000;
}

.key {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  border-right: 4rpx solid #000;
  background: rgba(0, 0, 0, 0.18);
}

.key:last-child {
  border-right: none;
}

.mark {
  font-size: 48rpx;
  line-height: 1;
}

.name {
  margin-top: 10rpx;
  font-size: 26rpx;
}
</style>
