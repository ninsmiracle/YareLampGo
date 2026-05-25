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

    host: document.getElementById("esp32-status-host"),
    ip: document.getElementById("esp32-status-ip"),
    manualIp: document.getElementById("esp32-manual-ip"),
    refreshStatus: document.getElementById("esp32-refresh-status"),

    btnSetup: document.getElementById("btn-esp32-setup-wifi"),
    btnRefresh: document.getElementById("btn-esp32-refresh"),
    btnReboot: document.getElementById("btn-esp32-reboot"),
    btnForget: document.getElementById("btn-esp32-forget"),
    cfgStatus: document.getElementById("cfg-esp32-status"),
    actionStatus: document.getElementById("esp32-action-status"),

    dialog: document.getElementById("esp32-setup-dialog"),
    steps: document.querySelectorAll(".esp32-setup-steps li"),
    stepPanes: document.querySelectorAll(".esp32-setup-step"),
    closeBtn: document.getElementById("btn-esp32-setup-close"),

    next1: document.getElementById("btn-esp32-setup-next-1"),
    back2: document.getElementById("btn-esp32-setup-back-2"),
    submit: document.getElementById("btn-esp32-setup-submit"),
    done: document.getElementById("btn-esp32-setup-done"),
    forgetAndGo: document.getElementById("btn-esp32-setup-forget-and-go"),

    ssidSelect: document.getElementById("esp32-ssid-select"),
    ssidManual: document.getElementById("esp32-ssid-manual"),
    psk: document.getElementById("esp32-psk"),
    togglePsk: document.getElementById("btn-esp32-toggle-psk"),
    softapBase: document.getElementById("esp32-softap-base"),
    rescan: document.getElementById("btn-esp32-rescan"),
    err: document.getElementById("esp32-setup-error"),
    wait: document.getElementById("esp32-wait-status"),
  };

  const dom2 = {
    hostSelect: document.getElementById("esp32-host-select"),
  };

  if (!dom.host && !dom.dialog && !dom.banner) return;

  const state = {
    bannerDismissedOnce: false,
    sessionUsedEver: false,
    lastStatus: null,
    manualMode: false,
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
    const configured = !!(status && status.configured);
    const sessionUsed = !!(status && status.session_used);
    if (sessionUsed) state.sessionUsedEver = true;

    const dev = (status && status.device) || null;
    if (dom.ip && !state.manualMode) {
      dom.ip.textContent = dev ? (dev.ip || "—") : "—";
    }
    if (dom.host) {
      if (status && status.blocked_devices_count && !configured) {
        dom.host.textContent = "无权限设备已隐藏";
        dom.host.className = "esp32-status-val esp32-state-chip is-warn";
      } else if (dev && dev.needs_firmware_update) {
        dom.host.textContent = "固件需更新";
        dom.host.className = "esp32-status-val esp32-state-chip is-warn";
      } else if (dev && dev.paired === false) {
        dom.host.textContent = "未配对";
        dom.host.className = "esp32-status-val esp32-state-chip is-warn";
      } else if (online) {
        dom.host.textContent = "在线";
        dom.host.className = "esp32-status-val esp32-state-chip is-online";
      } else if (configured || enabled) {
        dom.host.textContent = "离线";
        dom.host.className = "esp32-status-val esp32-state-chip is-warn";
      } else {
        dom.host.textContent = "未配网";
        dom.host.className = "esp32-status-val esp32-state-chip is-offline";
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
    const online = !!(status && status.online);

    if (!enabled || online) {
      dom.banner.classList.add("hidden");
      return;
    }

    const sessionUsed = !!status.session_used || state.sessionUsedEver;
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
      const unsupported = d.pairing_supported === false || d.needs_firmware_update === true;
      const unpaired = d.paired === false;
      opt.dataset.needsPair = unpaired ? "1" : "";
      opt.dataset.unsupported = unsupported ? "1" : "";
      opt.textContent = label + ip + (unsupported ? " · 固件需更新" : (unpaired ? " · 未配对" : "")) + ok;
      if (unsupported) opt.disabled = true;
      sel.appendChild(opt);
    });
    const manual = document.createElement("option");
    manual.value = "__manual__";
    manual.textContent = "\u624B\u52A8\u8F93\u5165 IP / \u4E3B\u673A\u540D\u2026";
    sel.appendChild(manual);
    const preferred = (status && status.preferred_host) || "";
    const active = current || preferred;
    if (active && ![...sel.options].some((o) => o.value === active)) {
      const keep = document.createElement("option");
      keep.value = active;
      keep.textContent = active + " (\u5DF2\u914D\u7F6E)";
      sel.insertBefore(keep, manual);
    }
    sel.value = active || "";
  }

  function setManualMode(on) {
    state.manualMode = on;
    if (dom.ip) dom.ip.classList.toggle("hidden", on);
    if (dom.manualIp) dom.manualIp.classList.toggle("hidden", !on);
    if (on && dom.manualIp) dom.manualIp.focus();
  }

  if (dom2.hostSelect) {
    dom2.hostSelect.addEventListener("change", async () => {
      if (dom2.hostSelect.value === "__manual__") {
        setManualMode(true);
        return;
      }
      setManualMode(false);
      const val = dom2.hostSelect.value;
      const opt = dom2.hostSelect.selectedOptions && dom2.hostSelect.selectedOptions[0];
      if (opt && opt.dataset.unsupported) {
        setActionStatus("这台 ESP32 固件需要更新后才能配对");
        await pollStatus();
        return;
      }
      try {
        if (val && opt && opt.dataset.needsPair) {
          await pairDevice(val);
        }
        await fetch("/api/config/device_esp32", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            "device_esp32.preferred_host": val,
            "device_esp32.enabled": true,
            "device_esp32.mic_enabled": true,
          }),
        });
      } catch (_) {}
      await pollStatus();
    });
  }

  async function pairDevice(host) {
    const res = await fetch("/api/device/pair", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ host: host || "" }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok || !body.ok) {
      throw new Error(body.error || (body.result && body.result.error) || "pair_failed");
    }
    setActionStatus("已配对设备");
    return body;
  }

  async function applyManualHost(val) {
    const opt = document.createElement("option");
    opt.value = val;
    opt.textContent = val;
    dom2.hostSelect.insertBefore(opt, dom2.hostSelect.querySelector('[value="__manual__"]'));
    dom2.hostSelect.value = val;
    if (dom.ip) dom.ip.textContent = val;
    setManualMode(false);
    try {
      await pairDevice(val);
      await fetch("/api/config/device_esp32", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          "device_esp32.preferred_host": val,
          "device_esp32.enabled": true,
          "device_esp32.mic_enabled": true,
        }),
      });
    } catch (err) {
      setActionStatus("配对失败：" + (err.message || err));
    }
    await pollStatus();
  }

  if (dom.manualIp) {
    dom.manualIp.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      const val = dom.manualIp.value.trim();
      if (!val) {
        dom2.hostSelect.value = "";
        setManualMode(false);
        return;
      }
      void applyManualHost(val);
    });
    dom.manualIp.addEventListener("blur", () => {
      const val = (dom.manualIp.value || "").trim();
      if (val) {
        void applyManualHost(val);
      } else if (state.manualMode) {
        dom2.hostSelect.value = "";
        setManualMode(false);
      }
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

  function setActionStatus(text) {
    if (dom.actionStatus) dom.actionStatus.textContent = text || "";
  }

  if (dom.btnRefresh) {
    dom.btnRefresh.addEventListener("click", () => {
      if (dom.refreshStatus) dom.refreshStatus.textContent = "刷新中…";
      pollStatus()
        .then(() => {
          if (dom.refreshStatus) dom.refreshStatus.textContent = "已刷新";
        })
        .catch((err) => {
          if (dom.refreshStatus) dom.refreshStatus.textContent = "刷新失败";
          setCfgStatus("刷新失败：" + (err.message || err));
        });
    });
  }

  if (dom.btnReboot) {
    dom.btnReboot.addEventListener("click", async () => {
      if (!confirm("确认要重启 ESP32 吗？")) return;
      setActionStatus("重启中…");
      try {
        const res = await fetch("/api/device/reboot", { method: "POST" });
        const body = await res.json().catch(() => ({}));
        setActionStatus(body && body.ok ? "已发送重启指令" : "重启失败");
      } catch (err) {
        setActionStatus("重启失败：" + err.message);
      }
    });
  }

  if (dom.btnForget) {
    dom.btnForget.addEventListener("click", async () => {
      if (!confirm("解绑设备会清除当前配对和设备 WiFi，ESP32 将重启并重新开启 Lampgo-Setup 热点。继续？")) return;
      setActionStatus("解绑并重置 WiFi 中…");
      try {
        const res = await fetch("/api/device/forget-wifi", { method: "POST" });
        const body = await res.json().catch(() => ({}));
        if (body && body.ok) {
          setActionStatus("已清除设备 WiFi，等待 ESP32 重启并开启 Lampgo-Setup 热点");
          const status = await fetchStatus();
          renderStatus(status);
        } else {
          setActionStatus("重置失败：" + (body.error || "无权限或设备离线"));
        }
      } catch (err) {
        setActionStatus("重置失败：" + err.message);
      }
    });
  }

  if (dom.togglePsk && dom.psk) {
    dom.togglePsk.addEventListener("click", () => {
      const show = dom.psk.type === "password";
      dom.psk.type = show ? "text" : "password";
      dom.togglePsk.setAttribute("aria-label", show ? "隐藏密码" : "显示密码");
      dom.togglePsk.title = show ? "隐藏密码" : "显示密码";
      dom.togglePsk.textContent = show ? "🙈" : "👁";
    });
  }

  // -----------------------------------------------------------------------
  // Setup wizard
  // -----------------------------------------------------------------------

  function openWizard() {
    openWizardAt(1);
  }

  function openWizardAt(step) {
    if (!dom.dialog) return;
    const step0Li = document.querySelector('.esp32-setup-steps li[data-step="0"]');
    if (step0Li) step0Li.classList.toggle("hidden", step !== 0);
    showStep(step);
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

  if (dom.btnSetup) dom.btnSetup.addEventListener("click", () => {
    const s = state.lastStatus;
    if (s && (s.online || s.enabled)) {
      openWizardAt(0);
    } else {
      openWizard();
    }
  });
  if (dom.bannerSetup) dom.bannerSetup.addEventListener("click", openWizard);

  if (dom.forgetAndGo) {
    dom.forgetAndGo.addEventListener("click", async () => {
      dom.forgetAndGo.disabled = true;
      dom.forgetAndGo.textContent = "正在重置设备…";
      try {
        await fetch("/api/device/unpair", { method: "POST" }).catch(() => {});
        await fetch("/api/device/forget-wifi", { method: "POST" });
        await fetch("/api/config/device_esp32", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ "device_esp32.enabled": false, "device_esp32.mic_enabled": false }),
        }).catch(() => {});
        const status = await fetchStatus();
        renderStatus(status);
      } catch (_) {}
      dom.forgetAndGo.disabled = false;
      dom.forgetAndGo.textContent = "重置设备并继续 →";
      showStep(1);
    });
  }
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
      if (Number.isFinite(body.scan_result) && body.scan_result < 0) {
        throw new Error(`ESP32 扫描失败：${body.error || "scan_failed"} (${body.scan_result})`);
      }
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
      await enableEsp32AfterProvisioning();
      void waitUntilDiscovered();
    } catch (err) {
      setError("发送失败：" + err.message);
    }
  });

  async function enableEsp32AfterProvisioning() {
    try {
      await fetch("/api/config/device_esp32", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          "device_esp32.enabled": true,
          "device_esp32.mic_enabled": true,
        }),
      });
    } catch (_) { /* best-effort */ }
  }

  async function waitUntilDiscovered() {
    if (!dom.wait) return;
    dom.wait.classList.remove("is-ok", "is-err");
    dom.wait.textContent = "已发送 WiFi 信息，ESP32 正在重启并连接…\n请将电脑切回家庭 WiFi（如仍连着 Lampgo-Setup 热点）";
    const start = Date.now();
    let phase2 = false;
    let lastDiscoveryRestartAt = 0;
    while (Date.now() - start < SETUP_WAIT_TIMEOUT_MS) {
      await new Promise((r) => setTimeout(r, SETUP_WAIT_POLL_MS));
      if (!phase2 && Date.now() - start > 8000) {
        phase2 = true;
        dom.wait.textContent = "正在通过 mDNS 搜索设备…（请确认电脑已连回家庭 WiFi）";
      }
      if (phase2 && Date.now() - lastDiscoveryRestartAt > 10000) {
        lastDiscoveryRestartAt = Date.now();
        void fetch("/api/device/discovery/restart", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ clear_devices: false }),
        }).catch(() => {});
      }
      try {
        const status = await fetchStatus();
        renderStatus(status);
        if (status && status.online) {
          dom.wait.classList.add("is-ok");
          dom.wait.textContent = "✓ 已发现设备" + (status.device && status.device.host ? `：${status.device.host}` : "") + "，配网完成！";
          return;
        }
      } catch (_) { /* network may flicker during WiFi switch */ }
    }
    dom.wait.classList.add("is-err");
    dom.wait.textContent = "超时未发现设备。请检查：1) 电脑已连回家庭 WiFi；2) ESP32 串口是否显示 WiFi connected；3) 路由器是否分配了 IP。";
  }

  // No auto-open: user launches the wizard explicitly via the button.
})();
