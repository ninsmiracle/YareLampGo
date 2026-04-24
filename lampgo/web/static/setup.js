/* eslint-disable no-console */
/**
 * ESP32 wireless camera/mic UI glue.
 *
 * Three responsibilities:
 *   1. Poll /api/device/status → render status chip + optional top banner
 *      (cold-fallback → yellow, mid-session drop → red).
 *   2. Wire up the hardware-card action buttons (refresh / push-config /
 *      reboot / forget / open wizard).
 *   3. Drive the WiFi provisioning wizard dialog:
 *        * scan networks (either through the lampgo proxy for a configured
 *          device, or directly against http://192.168.4.1 once the user
 *          joined the SoftAP),
 *        * submit credentials,
 *        * poll until the device reappears on lampgo's mDNS.
 *
 * Independent of app.js — the existing app loads first and owns the main
 * UI, this file attaches listeners on DOMContentLoaded and never mutates
 * anything app.js owns. It only talks to the backend over /api/device/*
 * and (optionally) directly to the ESP32 SoftAP at http://192.168.4.1.
 */

(function () {
  "use strict";

  const STATUS_POLL_MS = 8000;
  const SETUP_WAIT_POLL_MS = 3000;
  const SETUP_WAIT_TIMEOUT_MS = 90_000;

  const dom = {
    banner: document.getElementById("esp32-banner"),
    bannerText: document.getElementById("esp32-banner-text"),
    bannerSetup: document.getElementById("btn-esp32-banner-setup"),
    bannerDismiss: document.getElementById("btn-esp32-banner-dismiss"),

    chip: document.getElementById("esp32-state-chip"),
    host: document.getElementById("esp32-status-host"),
    ip: document.getElementById("esp32-status-ip"),
    health: document.getElementById("esp32-status-health"),
    mic: document.getElementById("esp32-status-mic"),

    btnSetup: document.getElementById("btn-esp32-setup-wifi"),
    btnRefresh: document.getElementById("btn-esp32-refresh"),
    btnPush: document.getElementById("btn-esp32-push-config"),
    btnReboot: document.getElementById("btn-esp32-reboot"),
    btnForget: document.getElementById("btn-esp32-forget"),
    cfgStatus: document.getElementById("cfg-esp32-status"),

    dialog: document.getElementById("esp32-setup-dialog"),
    steps: document.querySelectorAll(".esp32-setup-steps li"),
    stepPanes: document.querySelectorAll(".esp32-setup-step"),
    closeBtn: document.getElementById("btn-esp32-setup-close"),

    next1: document.getElementById("btn-esp32-setup-next-1"),
    back2: document.getElementById("btn-esp32-setup-back-2"),
    submit: document.getElementById("btn-esp32-setup-submit"),
    done: document.getElementById("btn-esp32-setup-done"),

    ssidSelect: document.getElementById("esp32-ssid-select"),
    ssidManual: document.getElementById("esp32-ssid-manual"),
    psk: document.getElementById("esp32-psk"),
    softapBase: document.getElementById("esp32-softap-base"),
    rescan: document.getElementById("btn-esp32-rescan"),
    err: document.getElementById("esp32-setup-error"),
    wait: document.getElementById("esp32-wait-status"),
  };

  const dom2 = {
    hostSelect: document.getElementById("esp32-host-select"),
    hostRefresh: document.getElementById("btn-esp32-host-refresh"),
  };

  if (!dom.chip && !dom.dialog && !dom.banner) return;

  const state = {
    bannerDismissedOnce: false,
    sessionUsedEver: false,
    lastStatus: null,
  };

  // -----------------------------------------------------------------------
  // Status polling
  // -----------------------------------------------------------------------

  async function fetchStatus() {
    try {
      const res = await fetch("/api/device/status");
      if (!res.ok) return null;
      const body = await res.json();
      if (!body.ok) return null;
      return body.result;
    } catch {
      return null;
    }
  }

  function renderStatus(status) {
    state.lastStatus = status;
    const enabled = !!(status && status.enabled);
    const online = !!(status && status.online);
    const sessionUsed = !!(status && status.session_used);
    if (sessionUsed) state.sessionUsedEver = true;

    if (dom.chip) {
      if (!enabled) {
        dom.chip.textContent = "未启用";
        dom.chip.className = "esp32-state-chip is-offline";
      } else if (online) {
        dom.chip.textContent = "在线";
        dom.chip.className = "esp32-state-chip is-online";
      } else if (status && status.configured) {
        dom.chip.textContent = "离线";
        dom.chip.className = "esp32-state-chip is-warn";
      } else {
        dom.chip.textContent = "发现中…";
        dom.chip.className = "esp32-state-chip is-offline";
      }
    }

    if (dom.host || dom.ip || dom.health) {
      const dev = (status && status.device) || null;
      if (dom.host) dom.host.textContent = dev ? (dev.host || dev.hostname || dev.ip || "—") : "未连接";
      if (dom.ip) dom.ip.textContent = dev ? (dev.ip || "—") : "—";
      if (dom.health) {
        if (!enabled) dom.health.textContent = "已禁用";
        else if (online) dom.health.textContent = "✓ 可代理";
        else if (dev) dom.health.textContent = "✗ 健康检查失败";
        else dom.health.textContent = "未发现设备";
      }
      if (dom.mic) {
        const micEnabled = !!(status && status.mic_enabled);
        const micStreaming = !!(status && status.mic_streaming);
        if (!micEnabled) dom.mic.textContent = "未启用";
        else if (micStreaming) dom.mic.textContent = "✓ ESP32 推流中";
        else if (online) dom.mic.textContent = "已启用（等待连接）";
        else dom.mic.textContent = "已启用（设备离线，用本地麦克风）";
      }
    }

    renderBanner(status);
  }

  function renderBanner(status) {
    if (!dom.banner || !dom.bannerText) return;
    if (state.bannerDismissedOnce) {
      dom.banner.classList.add("hidden");
      return;
    }
    const enabled = !!(status && status.enabled);
    if (!enabled) {
      dom.banner.classList.add("hidden");
      return;
    }

    const online = !!status.online;
    const sessionUsed = !!status.session_used || state.sessionUsedEver;

    if (online) {
      dom.banner.classList.add("hidden");
      return;
    }

    if (sessionUsed) {
      dom.banner.classList.remove("hidden", "is-offline-mid");
      dom.banner.classList.add("is-offline-mid");
      dom.bannerText.textContent = 'ESP32 掉线，等待恢复（本轮视觉/语音将由模型自然反馈"我看不到"）。';
    } else {
      dom.banner.classList.remove("hidden", "is-offline-mid");
      dom.bannerText.textContent = 'ESP32 离线，已用本地摄像头/麦克风。去"配置 → 硬件"可以发起配网。';
    }
  }

  function populateHostSelect(status) {
    const sel = dom2.hostSelect;
    if (!sel) return;
    const current = sel.value;
    const devices = (status && status.all_devices) || [];
    sel.innerHTML = "";
    const auto = document.createElement("option");
    auto.value = "";
    auto.textContent = "\u81EA\u52A8\uFF08\u9009\u6700\u8FD1\u53D1\u73B0\u7684\u8BBE\u5907\uFF09";
    sel.appendChild(auto);
    devices.forEach((d) => {
      const opt = document.createElement("option");
      opt.value = d.host || d.ip || "";
      const label = d.hostname || d.host || d.ip || "?";
      const ip = d.ip ? ` (${d.ip})` : "";
      const ok = d.last_health_ok_at ? " \u2713" : "";
      opt.textContent = label + ip + ok;
      sel.appendChild(opt);
    });
    const manual = document.createElement("option");
    manual.value = "__manual__";
    manual.textContent = "\u624B\u52A8\u8F93\u5165 IP / \u4E3B\u673A\u540D\u2026";
    sel.appendChild(manual);
    if (current && ![...sel.options].some((o) => o.value === current)) {
      const keep = document.createElement("option");
      keep.value = current;
      keep.textContent = current + " (\u5DF2\u914D\u7F6E)";
      sel.insertBefore(keep, manual);
    }
    sel.value = current || "";
  }

  if (dom2.hostSelect) {
    dom2.hostSelect.addEventListener("change", () => {
      if (dom2.hostSelect.value === "__manual__") {
        const val = prompt("\u8BF7\u8F93\u5165 ESP32 \u7684 IP \u6216\u4E3B\u673A\u540D\uFF08\u5982 192.168.31.229 \u6216 lampgo-cam-0834.local\uFF09\uFF1A", "");
        if (val && val.trim()) {
          const opt = document.createElement("option");
          opt.value = val.trim();
          opt.textContent = val.trim();
          dom2.hostSelect.insertBefore(opt, dom2.hostSelect.querySelector('[value="__manual__"]'));
          dom2.hostSelect.value = val.trim();
        } else {
          dom2.hostSelect.value = "";
        }
      }
    });
  }

  if (dom2.hostRefresh) {
    dom2.hostRefresh.addEventListener("click", async () => {
      dom2.hostRefresh.disabled = true;
      dom2.hostRefresh.textContent = "\u641C\u7D22\u4E2D\u2026";
      const status = await fetchStatus();
      renderStatus(status);
      populateHostSelect(status);
      dom2.hostRefresh.disabled = false;
      dom2.hostRefresh.textContent = "\u5237\u65B0";
    });
  }

  async function pollStatus() {
    const status = await fetchStatus();
    renderStatus(status);
    populateHostSelect(status);
  }

  setInterval(pollStatus, STATUS_POLL_MS);
  pollStatus();

  // -----------------------------------------------------------------------
  // Hardware-card action buttons
  // -----------------------------------------------------------------------

  function setCfgStatus(text) {
    if (dom.cfgStatus) dom.cfgStatus.textContent = text || "";
  }

  if (dom.btnRefresh) {
    dom.btnRefresh.addEventListener("click", () => {
      setCfgStatus("刷新中…");
      pollStatus().then(() => setCfgStatus("已刷新"));
    });
  }

  if (dom.btnPush) {
    dom.btnPush.addEventListener("click", async () => {
      const fs = document.querySelector('[data-cfg-input="device_esp32.framesize"]');
      const q = document.querySelector('[data-cfg-input="device_esp32.jpeg_quality"]');
      const mic = document.querySelector('[data-cfg-input="device_esp32.mic_enabled"]');
      const payload = {};
      if (fs && fs.value !== "") payload.framesize = Number(fs.value);
      if (q && q.value !== "") payload.jpeg_quality = Number(q.value);
      if (mic) payload.mic_enabled = !!mic.checked;
      setCfgStatus("推送中…");
      try {
        const res = await fetch("/api/device/config", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        const body = await res.json();
        if (!body.ok) throw new Error(body.error || "push failed");
        setCfgStatus("已推送");
      } catch (err) {
        setCfgStatus("推送失败：" + err.message);
      }
    });
  }

  if (dom.btnReboot) {
    dom.btnReboot.addEventListener("click", async () => {
      if (!confirm("确认要重启 ESP32 吗？")) return;
      setCfgStatus("重启中…");
      try {
        const res = await fetch("/api/device/reboot", { method: "POST" });
        const body = await res.json().catch(() => ({}));
        setCfgStatus(body && body.ok ? "设备已重启指令下发" : "重启失败");
      } catch (err) {
        setCfgStatus("重启失败：" + err.message);
      }
    });
  }

  if (dom.btnForget) {
    dom.btnForget.addEventListener("click", async () => {
      if (!confirm("将清除 ESP32 保存的 WiFi 凭据，设备会重新开放 Lampgo-Setup 热点。继续？")) return;
      setCfgStatus("清除中…");
      try {
        const res = await fetch("/api/device/forget-wifi", { method: "POST" });
        const body = await res.json().catch(() => ({}));
        setCfgStatus(body && body.ok ? "已清除，请重新配网" : "清除失败");
      } catch (err) {
        setCfgStatus("清除失败：" + err.message);
      }
    });
  }

  // -----------------------------------------------------------------------
  // Setup wizard
  // -----------------------------------------------------------------------

  function openWizard() {
    if (!dom.dialog) return;
    showStep(1);
    dom.dialog.showModal();
  }

  function closeWizard() {
    if (dom.dialog && dom.dialog.open) dom.dialog.close();
  }

  function showStep(n) {
    dom.steps.forEach((li) => li.classList.toggle("is-active", Number(li.dataset.step) === n));
    dom.stepPanes.forEach((s) => s.classList.toggle("hidden", Number(s.dataset.step) !== n));
    if (dom.err) dom.err.classList.add("hidden");
  }

  function setError(msg) {
    if (!dom.err) return;
    if (!msg) {
      dom.err.classList.add("hidden");
      return;
    }
    dom.err.textContent = msg;
    dom.err.classList.remove("hidden");
  }

  if (dom.btnSetup) dom.btnSetup.addEventListener("click", openWizard);
  if (dom.bannerSetup) dom.bannerSetup.addEventListener("click", openWizard);
  if (dom.closeBtn) dom.closeBtn.addEventListener("click", closeWizard);
  if (dom.bannerDismiss) {
    dom.bannerDismiss.addEventListener("click", () => {
      state.bannerDismissedOnce = true;
      dom.banner.classList.add("hidden");
    });
  }

  if (dom.next1) dom.next1.addEventListener("click", () => {
    showStep(2);
    void runScan();
  });
  if (dom.back2) dom.back2.addEventListener("click", () => showStep(1));
  if (dom.rescan) dom.rescan.addEventListener("click", () => void runScan());
  if (dom.done) dom.done.addEventListener("click", closeWizard);

  // Probe and scan both go through /api/device/probe. The backend accepts a
  // base_url so the browser doesn't have to know whether it's routed via
  // mDNS discovery or via the user-entered SoftAP IP (a direct browser call
  // to 192.168.4.1 would also work but hits CORS when the dev page is on
  // another origin — proxying keeps the wizard origin-agnostic).
  async function deviceProbe(path, init) {
    const base = (dom.softapBase && dom.softapBase.value.trim()) || "";
    const body = {
      base_url: base,
      path: path,
      method: (init && init.method) || "GET",
      body: (init && init.body) || null,
    };
    const res = await fetch("/api/device/probe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await res.json().catch(() => ({ ok: false, error: "bad_json" }));
    if (!payload.ok) throw new Error(payload.error || "probe_failed");
    return payload.result || {};
  }

  async function runScan() {
    if (!dom.ssidSelect) return;
    dom.ssidSelect.innerHTML = '<option value="">正在扫描…</option>';
    setError("");
    try {
      const result = await deviceProbe("/scan", { method: "GET" });
      const body = result.body || {};
      const networks = Array.isArray(body.networks) ? body.networks : [];
      dom.ssidSelect.innerHTML = "";
      if (!networks.length) {
        const opt = document.createElement("option");
        opt.value = "";
        opt.textContent = "（未扫到网络，可在下方手动输入）";
        dom.ssidSelect.appendChild(opt);
        return;
      }
      networks
        .sort((a, b) => (b.rssi || -999) - (a.rssi || -999))
        .forEach((n) => {
          const opt = document.createElement("option");
          opt.value = n.ssid || "";
          const rssi = Number.isFinite(n.rssi) ? ` · ${n.rssi}dBm` : "";
          const enc = n.open ? " · 开放" : "";
          opt.textContent = `${n.ssid || "(隐藏)"}${rssi}${enc}`;
          dom.ssidSelect.appendChild(opt);
        });
    } catch (err) {
      dom.ssidSelect.innerHTML = '<option value="">扫描失败</option>';
      setError("扫描失败：" + err.message + "（请确认已连上 Lampgo-Setup 热点，或填写直连地址）");
    }
  }

  if (dom.submit) dom.submit.addEventListener("click", async () => {
    setError("");
    const manual = (dom.ssidManual && dom.ssidManual.value.trim()) || "";
    const picked = (dom.ssidSelect && dom.ssidSelect.value) || "";
    const ssid = manual || picked;
    const psk = (dom.psk && dom.psk.value) || "";
    if (!ssid) {
      setError("请选择或手动输入 SSID。");
      return;
    }
    try {
      const result = await deviceProbe("/connect", {
        method: "POST",
        body: { ssid, password: psk },
      });
      const body = result.body || {};
      if (!body.ok) {
        setError("ESP32 返回失败：" + (body.error || JSON.stringify(body)));
        return;
      }
      showStep(3);
      void waitUntilDiscovered();
    } catch (err) {
      setError("发送失败：" + err.message);
    }
  });

  async function waitUntilDiscovered() {
    if (!dom.wait) return;
    dom.wait.classList.remove("is-ok", "is-err");
    dom.wait.textContent = "等待 mDNS 发现设备…";
    const start = Date.now();
    while (Date.now() - start < SETUP_WAIT_TIMEOUT_MS) {
      await new Promise((r) => setTimeout(r, SETUP_WAIT_POLL_MS));
      const status = await fetchStatus();
      renderStatus(status);
      if (status && status.online) {
        dom.wait.classList.add("is-ok");
        dom.wait.textContent = "✓ 已发现设备" + (status.device && status.device.host ? `：${status.device.host}` : "") + "。";
        return;
      }
    }
    dom.wait.classList.add("is-err");
    dom.wait.textContent = "超时未发现设备。请检查 ESP32 指示灯是否已连上目标 WiFi，或去路由器后台确认是否分配到 IP。";
  }

  // -----------------------------------------------------------------------
  // Auto-open wizard on first visit when enabled + never-online-before.
  //
  // "Never-online-before" = we've been polling for >5s and device.online is
  // still false AND status.device is null. If the device was online earlier
  // this session (state.sessionUsedEver), skip auto-open — user probably
  // just has a flaky link.
  // -----------------------------------------------------------------------
  let autoOpened = false;
  setTimeout(async () => {
    if (autoOpened) return;
    const status = state.lastStatus || (await fetchStatus());
    renderStatus(status);
    if (!status) return;
    if (!status.enabled) return;
    if (status.online) return;
    if (status.device) return;
    if (state.sessionUsedEver) return;
    const key = "esp32.wizardAutoOpened.v1";
    if (sessionStorage.getItem(key)) return;
    sessionStorage.setItem(key, "1");
    autoOpened = true;
    openWizard();
  }, 6000);
})();
