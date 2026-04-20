/* lampgo Web UI — WebSocket chat client */

(function () {
  "use strict";

  const chatMessages = document.getElementById("chat-messages");
  const chatForm = document.getElementById("chat-form");
  const chatInput = document.getElementById("chat-input");
  const connDot = document.getElementById("conn-dot");
  const connText = document.getElementById("conn-text");
  const connBadge = document.getElementById("conn-badge");
  const btnEstop = document.getElementById("btn-estop");
  const btnRecordMotionPanel = document.getElementById("btn-record-motion-panel");
  const btnClearEvents = document.getElementById("btn-clear-events");
  const emptyStateTemplate = document.getElementById("empty-state");
  const skillGrid = document.getElementById("skill-grid");
  const recordingGrid = document.getElementById("recording-grid");
  const expressionGrid = document.getElementById("expression-grid");
  const skillCountEl = document.getElementById("skill-count");
  const recordingCountEl = document.getElementById("recording-count");
  const expressionCountEl = document.getElementById("expression-count");
  const skillSearchEl = document.getElementById("skill-search");
  const recordingSearchEl = document.getElementById("recording-search");
  const expressionSearchEl = document.getElementById("expression-search");
  const openclawTaskList = document.getElementById("openclaw-task-list");
  const chipJoint = document.getElementById("chip-joint");
  const chipJointDot = document.getElementById("chip-joint-dot");
  const chipCamera = document.getElementById("chip-camera");
  const chipCameraDot = document.getElementById("chip-camera-dot");
  const chipMic = document.getElementById("chip-mic");
  const chipMicDot = document.getElementById("chip-mic-dot");
  const chipLed = document.getElementById("chip-led");
  const chipLedDot = document.getElementById("chip-led-dot");
  const btnRefreshOpenclaw = document.getElementById("btn-refresh-openclaw");
  const ocCountQueued = document.getElementById("oc-count-queued");
  const ocCountRunning = document.getElementById("oc-count-running");
  const ocCountAwaiting = document.getElementById("oc-count-awaiting");
  const ocCountPromoted = document.getElementById("oc-count-promoted");
  const ocCountFailed = document.getElementById("oc-count-failed");
  const eventLog = document.getElementById("event-log");
  const btnMic = document.getElementById("btn-mic");
  const btnVoiceCancel = document.getElementById("btn-voice-cancel");
  const btnStop = document.getElementById("btn-stop");
  const voiceWave = document.getElementById("voice-wave");
  const voiceCanvas = document.getElementById("voice-canvas");
  const recordNameDialog = document.getElementById("record-name-dialog");
  const recordNameForm = document.getElementById("record-name-form");
  const recordNameInput = document.getElementById("record-name-input");
  const recordNameError = document.getElementById("record-name-error");
  const btnRecordDiscard = document.getElementById("btn-record-discard");
  const btnRecordRerecord = document.getElementById("btn-record-rerecord");
  const btnRecordSave = document.getElementById("btn-record-save");
  const recordStartDialog = document.getElementById("record-start-dialog");
  const recordStartForm = document.getElementById("record-start-form");
  const btnRecordStartCancel = document.getElementById("btn-record-start-cancel");
  const btnRecordStartConfirm = document.getElementById("btn-record-start-confirm");
  const recordStartDesc = document.getElementById("record-start-desc");
  const recordTimer = document.getElementById("record-timer");
  const recordMetrics = document.getElementById("record-metrics");
  const playbackModeButtons = Array.from(document.querySelectorAll("[data-playback-mode]"));
  const navButtons = Array.from(document.querySelectorAll(".nav-item[data-view]"));
  const viewSections = Array.from(document.querySelectorAll(".view[data-view]"));
  const appShell = document.querySelector(".app-shell");
  const hintChips = Array.from(document.querySelectorAll(".hint-chip"));
  const historyList = document.getElementById("history-list");
  const historySearch = document.getElementById("history-search");
  const btnHistoryClear = document.getElementById("btn-history-clear");

  const RECORDING_EXPRESSIONS = Object.freeze({
    angry_jerk: "angry",
    awkward_pause: "helpless",
    celebrate: "star",
    confused: "question",
    curious: "question",
    dance: "music",
    deep_think: "thinking",
    dislike: "cross",
    dizzy_spin: "rainbow",
    doze_off: "sleep",
    dramatic_faint: "helpless",
    excited: "star",
    flirty_wink: "heart",
    groove_bounce: "music",
    happy_wiggle: "smiley",
    headshake: "cross",
    heartbreak: "heartbreak",
    idle: "white",
    lookout: "right",
    mischief_peek: "blush",
    movebackward: "down",
    moveforward: "up",
    nod: "check",
    nod_small: "check",
    peep: "left",
    push: "exclaim",
    sad: "crying",
    sayhitoboss: "smiley",
    scanning: "question",
    shock: "surprised",
    shy: "blush",
    sneeze: "exclaim",
    startle_recover: "surprised",
    stretch_yawn: "sleep",
    tippy_taps: "music",
    wake_up: "surprised",
    working: "thinking",
  });

  const PLAYBACK_MODE_KEY = "lampgo.playbackMode";
  const SESSION_STORAGE_KEY = "lampgo.sessions";
  const ACTIVE_SESSION_KEY = "lampgo.activeSession";
  const OPENCLAW_TASK_SESSION_KEY = "lampgo.openclawTaskSessions";
  const OPENCLAW_FOLLOWUP_KEY = "lampgo.openclawFollowups";
  const MAX_SESSIONS = 40;
  const PLAYBACK_MODES = new Set(["raw", "cleaned", "expressive"]);
  let playbackMode = "cleaned";

  let ws = null;
  let reqCounter = 0;
  const pendingMessages = new Map();
  const pendingUserMessages = new Map();
  const pendingUserEntries = new Map(); // requestId -> { session, entry } for live-transcribed messages
  const pendingAssistantEntries = new Map(); // requestId -> { session, entry } for live-persisted thinking
  const pendingSnapshotTimers = new Map(); // requestId -> timer id
  const openclawTasks = new Map();
  const openclawPrevStatus = new Map();
  const openclawTaskSessions = loadTaskSessionMap();
  const openclawFollowups = loadFollowupSet();
  const streamingState = new Map();
  let activeAgentRequestId = null;
  let isMotionRecording = false;
  let hasPendingMotionRecording = false;
  let pendingOverwriteSave = false;
  let recordingStartTs = 0;
  let recordTimerTask = null;
  let recordingFps = 30;
  let recordingFrames = 0;

  let sessions = [];
  let activeSessionId = null;
  let currentView = "chat";
  let latestJointPositions = {};
  const JOINT_LABELS = {
    base_yaw: "底座旋转",
    base_pitch: "底座俯仰",
    elbow_pitch: "肘部俯仰",
    wrist_roll: "腕部滚转",
    wrist_pitch: "腕部俯仰",
  };

  /* ---- OpenClaw task <-> session persistence ---- */

  function loadTaskSessionMap() {
    try {
      const raw = localStorage.getItem(OPENCLAW_TASK_SESSION_KEY);
      if (!raw) return new Map();
      const obj = JSON.parse(raw);
      const map = new Map();
      if (obj && typeof obj === "object") {
        Object.entries(obj).forEach(([k, v]) => {
          if (typeof k === "string" && typeof v === "string") map.set(k, v);
        });
      }
      return map;
    } catch (err) {
      console.warn("[openclaw] load task-session map failed:", err);
      return new Map();
    }
  }

  function persistTaskSessionMap() {
    try {
      const obj = {};
      openclawTaskSessions.forEach((v, k) => { obj[k] = v; });
      localStorage.setItem(OPENCLAW_TASK_SESSION_KEY, JSON.stringify(obj));
    } catch (err) {
      console.warn("[openclaw] persist task-session map failed:", err);
    }
  }

  function loadFollowupSet() {
    try {
      const raw = localStorage.getItem(OPENCLAW_FOLLOWUP_KEY);
      if (!raw) return new Set();
      const arr = JSON.parse(raw);
      return new Set(Array.isArray(arr) ? arr.filter((v) => typeof v === "string") : []);
    } catch (err) {
      console.warn("[openclaw] load followup set failed:", err);
      return new Set();
    }
  }

  function persistFollowupSet() {
    try {
      localStorage.setItem(OPENCLAW_FOLLOWUP_KEY, JSON.stringify(Array.from(openclawFollowups)));
    } catch (err) {
      console.warn("[openclaw] persist followup set failed:", err);
    }
  }

  function rememberOpenClawTaskSession(taskId, sessionId) {
    if (!taskId || !sessionId) return;
    openclawTaskSessions.set(taskId, sessionId);
    persistTaskSessionMap();
  }

  /* ---- Session history ---- */

  function loadSessions() {
    try {
      const raw = localStorage.getItem(SESSION_STORAGE_KEY);
      sessions = raw ? JSON.parse(raw) : [];
      if (!Array.isArray(sessions)) sessions = [];
    } catch {
      sessions = [];
    }
    // Mark orphaned pending assistant entries (from previous page/session that crashed) as stale,
    // so they render their snapshot without re-binding to a non-existent in-flight request.
    let mutated = false;
    for (const s of sessions) {
      if (!s || !Array.isArray(s.messages)) continue;
      for (const m of s.messages) {
        if (m && m.role === "assistant" && m.meta && m.meta.pending) {
          delete m.meta.pending;
          delete m.meta.requestId;
          if (!m.text) m.text = "";
          m.meta.interrupted = true;
          mutated = true;
        }
      }
    }
    if (mutated) {
      try {
        localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(sessions));
      } catch {}
    }
    activeSessionId = localStorage.getItem(ACTIVE_SESSION_KEY) || null;
    if (activeSessionId && !sessions.find((s) => s.id === activeSessionId)) {
      activeSessionId = null;
    }
  }

  function persistSessions() {
    try {
      localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(sessions.slice(0, MAX_SESSIONS)));
      if (activeSessionId) {
        localStorage.setItem(ACTIVE_SESSION_KEY, activeSessionId);
      } else {
        localStorage.removeItem(ACTIVE_SESSION_KEY);
      }
    } catch (err) {
      console.warn("[sessions] persist failed:", err);
    }
  }

  function createSession() {
    const id = `s_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
    const session = {
      id,
      title: "新会话",
      messages: [],
      createdAt: Date.now(),
      updatedAt: Date.now(),
    };
    sessions.unshift(session);
    activeSessionId = id;
    persistSessions();
    return session;
  }

  function getActiveSession() {
    if (!activeSessionId) return null;
    return sessions.find((s) => s.id === activeSessionId) || null;
  }

  function ensureActiveSession() {
    let session = getActiveSession();
    if (!session) {
      session = createSession();
    }
    return session;
  }

  function pushMessageToSession(role, text, meta, opts) {
    if (!text) return null;
    const sessionId = (opts && opts.sessionId) || null;
    let session = null;
    if (sessionId) {
      session = sessions.find((s) => s.id === sessionId) || null;
    }
    if (!session) session = ensureActiveSession();
    const entry = { role, text, ts: Date.now() };
    if (meta && typeof meta === "object") entry.meta = meta;
    session.messages.push(entry);
    session.updatedAt = Date.now();
    const isVoicePlaceholder = role === "user" && meta && meta.voice && !meta.voice_transcribed;
    if (session.title === "新会话" && role === "user" && !isVoicePlaceholder) {
      session.title = text.length > 28 ? text.slice(0, 28) + "…" : text;
    }
    persistSessions();
    renderHistory();
    return { session, entry };
  }

  function formatRelativeTime(ts) {
    const diff = Math.max(0, Date.now() - ts);
    const m = Math.floor(diff / 60000);
    if (m < 1) return "刚刚";
    if (m < 60) return `${m}分钟前`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h}小时前`;
    const d = Math.floor(h / 24);
    if (d < 30) return `${d}天前`;
    return new Date(ts).toLocaleDateString();
  }

  function renderHistory() {
    if (!historyList) return;
    const keyword = (historySearch && historySearch.value.trim().toLowerCase()) || "";
    const filtered = sessions.filter((s) => {
      if (!keyword) return true;
      if (s.title.toLowerCase().includes(keyword)) return true;
      return s.messages.some((m) => (m.text || "").toLowerCase().includes(keyword));
    });
    if (!filtered.length) {
      historyList.innerHTML = '<div class="history-empty">暂无历史记录</div>';
      return;
    }
    historyList.innerHTML = filtered
      .map((s) => {
        const isActive = s.id === activeSessionId;
        return `
          <div class="history-item ${isActive ? "is-active" : ""}" data-session-id="${esc(s.id)}">
            <button class="history-item-main" type="button" data-action="open" data-session-id="${esc(s.id)}">
              <span class="history-item-title">${esc(s.title || "未命名")}</span>
              <span class="history-item-meta">${esc(formatRelativeTime(s.updatedAt))}</span>
            </button>
            <button class="history-item-menu" type="button" data-action="menu" data-session-id="${esc(s.id)}" aria-label="更多操作" title="更多操作">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg>
            </button>
          </div>
        `;
      })
      .join("");
    historyList.querySelectorAll(".history-item-main").forEach((btn) => {
      btn.addEventListener("click", (ev) => {
        const parent = btn.closest(".history-item");
        if (parent && parent.classList.contains("is-renaming")) {
          ev.preventDefault();
          return;
        }
        loadSession(btn.dataset.sessionId);
      });
    });
    historyList.querySelectorAll(".history-item-menu").forEach((btn) => {
      btn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        openHistoryMenu(btn, btn.dataset.sessionId);
      });
    });
  }

  /* ---- History item menu (rename / delete) ---- */

  let historyMenuEl = null;
  let historyMenuTarget = null;

  function ensureHistoryMenu() {
    if (historyMenuEl) return historyMenuEl;
    const el = document.createElement("div");
    el.className = "history-popover";
    el.style.display = "none";
    el.innerHTML = `
      <button type="button" data-history-action="rename">重命名</button>
      <button type="button" data-history-action="delete" class="is-danger">删除</button>
    `;
    document.body.appendChild(el);
    el.addEventListener("click", (ev) => {
      const btn = ev.target.closest("[data-history-action]");
      if (!btn) return;
      const action = btn.dataset.historyAction;
      const id = el.dataset.sessionId;
      closeHistoryMenu();
      if (!id) return;
      if (action === "rename") renameSession(id);
      else if (action === "delete") deleteSession(id);
    });
    historyMenuEl = el;
    return el;
  }

  function openHistoryMenu(anchor, sessionId) {
    const el = ensureHistoryMenu();
    if (historyMenuTarget === anchor && el.style.display !== "none") {
      closeHistoryMenu();
      return;
    }
    el.dataset.sessionId = sessionId;
    el.style.visibility = "hidden";
    el.style.display = "flex";
    const rect = anchor.getBoundingClientRect();
    const menuRect = el.getBoundingClientRect();
    let top = rect.bottom + 4;
    let left = rect.right - menuRect.width;
    if (top + menuRect.height > window.innerHeight - 8) {
      top = Math.max(8, rect.top - menuRect.height - 4);
    }
    if (left < 8) left = 8;
    el.style.top = `${top}px`;
    el.style.left = `${left}px`;
    el.style.visibility = "";
    historyMenuTarget = anchor;
    document.querySelectorAll(".history-item.menu-open").forEach((n) => n.classList.remove("menu-open"));
    const parent = anchor.closest(".history-item");
    if (parent) parent.classList.add("menu-open");
  }

  function closeHistoryMenu() {
    if (!historyMenuEl) return;
    historyMenuEl.style.display = "none";
    historyMenuEl.dataset.sessionId = "";
    document.querySelectorAll(".history-item.menu-open").forEach((el) => el.classList.remove("menu-open"));
    historyMenuTarget = null;
  }

  document.addEventListener("click", (ev) => {
    if (!historyMenuEl || historyMenuEl.style.display === "none") return;
    if (historyMenuEl.contains(ev.target)) return;
    if (historyMenuTarget && historyMenuTarget.contains(ev.target)) return;
    closeHistoryMenu();
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") closeHistoryMenu();
  });
  window.addEventListener("resize", closeHistoryMenu);
  window.addEventListener("scroll", closeHistoryMenu, true);

  function renameSession(id) {
    const session = sessions.find((s) => s.id === id);
    if (!session) return;
    const itemEl = historyList.querySelector(`.history-item[data-session-id="${CSS.escape(id)}"]`);
    const titleEl = itemEl ? itemEl.querySelector(".history-item-title") : null;
    if (!itemEl || !titleEl) {
      // Fallback: couldn't find DOM anchor (edge case), use prompt as backup.
      const next = window.prompt("重命名会话", session.title || "");
      if (next === null) return;
      const trimmed = next.trim();
      if (!trimmed) return;
      session.title = trimmed.length > 60 ? trimmed.slice(0, 60) : trimmed;
      session.updatedAt = Date.now();
      persistSessions();
      renderHistory();
      return;
    }

    if (itemEl.classList.contains("is-renaming")) return;
    itemEl.classList.add("is-renaming");

    const input = document.createElement("input");
    input.type = "text";
    input.className = "history-item-rename-input";
    input.value = session.title || "";
    input.maxLength = 60;
    input.setAttribute("aria-label", "重命名会话");
    titleEl.replaceWith(input);

    let committed = false;

    const finish = (save) => {
      if (committed) return;
      committed = true;
      input.removeEventListener("keydown", onKey);
      input.removeEventListener("blur", onBlur);
      if (save) {
        const trimmed = input.value.trim();
        if (trimmed) {
          session.title = trimmed.length > 60 ? trimmed.slice(0, 60) : trimmed;
          session.updatedAt = Date.now();
          persistSessions();
        }
      }
      renderHistory();
    };

    const onKey = (ev) => {
      if (ev.key === "Enter") {
        ev.preventDefault();
        finish(true);
      } else if (ev.key === "Escape") {
        ev.preventDefault();
        finish(false);
      }
    };
    const onBlur = () => finish(true);

    const stopBubble = (ev) => ev.stopPropagation();
    input.addEventListener("click", stopBubble);
    input.addEventListener("mousedown", stopBubble);
    input.addEventListener("keydown", onKey);
    input.addEventListener("blur", onBlur);

    requestAnimationFrame(() => {
      input.focus();
      input.select();
    });
  }

  function deleteSession(id) {
    const session = sessions.find((s) => s.id === id);
    if (!session) return;
    if (!window.confirm(`确认删除会话「${session.title || "未命名"}」？`)) return;
    sessions = sessions.filter((s) => s.id !== id);
    if (activeSessionId === id) {
      activeSessionId = null;
      chatMessages.innerHTML = "";
      ensureEmptyState();
    }
    persistSessions();
    renderHistory();
  }

  function loadSession(id) {
    const session = sessions.find((s) => s.id === id);
    if (!session) return;
    if (id === activeSessionId && chatMessages && chatMessages.childElementCount > 0) {
      // Already showing this session — don't rebuild DOM, preserves in-progress thinking state.
      showView("chat");
      renderHistory();
      scrollChat();
      return;
    }
    activeSessionId = id;
    persistSessions();
    showView("chat");
    chatMessages.innerHTML = "";
    if (!session.messages.length) {
      ensureEmptyState();
    } else {
      session.messages.forEach((m) => {
        if (m.role === "user") {
          renderHistoricalUserBubble(m.text, m.ts);
        } else if (m.role === "assistant") {
          renderHistoricalAssistantBubble(m.text, m.ts, m.meta);
        } else if (m.role === "system") {
          const note = document.createElement("div");
          note.className = "system-note";
          note.textContent = m.text;
          chatMessages.appendChild(note);
        }
      });
    }
    renderHistory();
    scrollChat();
  }

  function startNewSession() {
    createSession();
    showView("chat");
    chatMessages.innerHTML = "";
    ensureEmptyState();
    renderHistory();
  }

  function clearAllHistory() {
    if (!confirm("确认清空全部会话历史？")) return;
    sessions = [];
    activeSessionId = null;
    persistSessions();
    chatMessages.innerHTML = "";
    ensureEmptyState();
    renderHistory();
  }

  /* ---- View routing ---- */

  function showView(name) {
    currentView = name;
    viewSections.forEach((sec) => {
      sec.classList.toggle("hidden", sec.dataset.view !== name);
    });
    navButtons.forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.view === name);
    });
    if (appShell) appShell.dataset.view = name;
    if (name === "openclaw") {
      send({ type: "openclaw_tasks" });
    }
  }

  /* ---- Empty state ---- */

  let emptyStateHost = null;

  function ensureEmptyState() {
    if (!emptyStateTemplate) return;
    if (chatMessages.querySelector("#empty-state")) return;
    emptyStateHost = emptyStateTemplate.cloneNode(true);
    emptyStateHost.id = "empty-state";
    emptyStateHost.classList.remove("hidden");
    chatMessages.appendChild(emptyStateHost);
    emptyStateHost.querySelectorAll(".hint-chip").forEach((chip) => {
      chip.addEventListener("click", () => {
        const prompt = chip.dataset.prompt || chip.textContent || "";
        chatInput.value = prompt;
        chatInput.focus();
      });
    });
  }

  function clearEmptyState() {
    const existing = chatMessages.querySelector("#empty-state");
    if (existing && existing.parentNode) existing.parentNode.removeChild(existing);
    emptyStateHost = null;
  }

  /* ---- Record buttons ---- */

  function eachRecordButton(fn) {
    if (btnRecordMotionPanel) fn(btnRecordMotionPanel);
  }

  function resetRecordStartDialogUI() {
    if (btnRecordStartConfirm) btnRecordStartConfirm.textContent = "开始录制";
    if (btnRecordStartCancel) btnRecordStartCancel.classList.remove("hidden");
    if (recordStartDesc) {
      recordStartDesc.textContent = "点击“开始录制”后将自动关闭电机力矩，你可以手动掰动关节进行录制。";
    }
    if (recordTimer) recordTimer.textContent = "已录制 0.0s";
    if (recordMetrics) recordMetrics.textContent = "采样：-- FPS · 0 帧";
  }

  function startRecordTimer() {
    stopRecordTimer();
    if (!recordTimer) return;
    recordTimerTask = setInterval(() => {
      const elapsed = Math.max(0, (Date.now() / 1000) - recordingStartTs);
      recordTimer.textContent = `已录制 ${elapsed.toFixed(1)}s`;
      if (recordMetrics) {
        const estimatedFrames = Math.max(recordingFrames, Math.round(elapsed * recordingFps));
        recordMetrics.textContent = `采样：${recordingFps} FPS · ${estimatedFrames} 帧`;
      }
    }, 100);
  }

  function stopRecordTimer() {
    if (recordTimerTask) {
      clearInterval(recordTimerTask);
      recordTimerTask = null;
    }
  }

  function setPlaybackMode(mode) {
    const nextMode = PLAYBACK_MODES.has(mode) ? mode : "cleaned";
    playbackMode = nextMode;
    playbackModeButtons.forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.playbackMode === nextMode);
    });
    localStorage.setItem(PLAYBACK_MODE_KEY, nextMode);
  }

  /* ---- WebSocket ---- */

  function connect() {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/ws`);

    ws.onopen = () => {
      setConnected(true);
      ws.send(JSON.stringify({ type: "skills" }));
      ws.send(JSON.stringify({ type: "recordings" }));
      ws.send(JSON.stringify({ type: "expressions" }));
      ws.send(JSON.stringify({ type: "openclaw_tasks" }));
      ws.send(JSON.stringify({ type: "status" }));
    };

    ws.onclose = () => {
      setConnected(false);
      setTimeout(connect, 2000);
    };

    ws.onerror = () => ws.close();

    ws.onmessage = (evt) => {
      try {
        handleMessage(JSON.parse(evt.data));
      } catch (err) {
        console.error("parse error", err);
      }
    };
  }

  function setConnected(ok) {
    connDot.className = `connection-dot ${ok ? "is-online" : "is-offline"}`;
    connText.textContent = ok ? "已连接" : "未连接";
    if (connBadge) {
      connBadge.classList.toggle("is-online", ok);
      connBadge.classList.toggle("is-offline", !ok);
    }
  }

  /* ---- Mic availability ---- */

  function setMicChipState(online, label) {
    if (!chipMic) return;
    chipMic.classList.toggle("is-online", online);
    chipMic.classList.toggle("is-offline", !online);
    if (chipMicDot) {
      chipMicDot.classList.toggle("is-online", online);
      chipMicDot.classList.toggle("is-offline", !online);
    }
    if (label) chipMic.title = label;
  }

  async function probeMicStatus() {
    if (!navigator || !navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) {
      setMicChipState(false, "浏览器不支持麦克风");
      return;
    }
    try {
      const devices = await navigator.mediaDevices.enumerateDevices();
      const mics = devices.filter((d) => d.kind === "audioinput");
      if (!mics.length) {
        setMicChipState(false, "未检测到麦克风");
        return;
      }
      const granted = mics.some((d) => d.label);
      setMicChipState(granted, granted ? `麦克风可用（${mics.length} 个）` : "麦克风可用，等待授权");
    } catch (err) {
      setMicChipState(false, "无法访问麦克风列表");
    }
  }

  probeMicStatus();
  if (navigator && navigator.mediaDevices && navigator.mediaDevices.addEventListener) {
    navigator.mediaDevices.addEventListener("devicechange", probeMicStatus);
  }

  /* ---- Joint popover ---- */

  let jointPopoverEl = null;

  function ensureJointPopover() {
    if (jointPopoverEl) return jointPopoverEl;
    const el = document.createElement("div");
    el.className = "joint-popover";
    el.style.display = "none";
    document.body.appendChild(el);
    jointPopoverEl = el;
    return el;
  }

  function isJointPopoverOpen() {
    return jointPopoverEl && jointPopoverEl.style.display !== "none";
  }

  function renderJointPopoverContent() {
    const el = ensureJointPopover();
    const entries = Object.entries(latestJointPositions || {});
    let body = '<div class="joint-popover-title">关节角度（实时）</div>';
    if (!entries.length) {
      body += '<div class="joint-popover-empty">关节数据待机中</div>';
    } else {
      body += entries
        .map(([k, v]) => {
          const value = typeof v === "number" ? `${v.toFixed(1)}°` : `${v}`;
          const label = JOINT_LABELS[k] || k;
          return `<div class="joint-row"><span class="joint-row-name">${esc(label)} <span style="color: var(--text-faint); font-size: 10.5px;">${esc(k)}</span></span><span class="joint-row-value">${esc(value)}</span></div>`;
        })
        .join("");
    }
    el.innerHTML = body;
  }

  function openJointPopover() {
    const el = ensureJointPopover();
    renderJointPopoverContent();
    el.style.visibility = "hidden";
    el.style.display = "flex";
    if (!chipJoint) return;
    const rect = chipJoint.getBoundingClientRect();
    const menuRect = el.getBoundingClientRect();
    let top = rect.bottom + 6;
    let left = rect.left;
    if (top + menuRect.height > window.innerHeight - 8) {
      top = Math.max(8, rect.top - menuRect.height - 6);
    }
    if (left + menuRect.width > window.innerWidth - 8) {
      left = Math.max(8, window.innerWidth - menuRect.width - 8);
    }
    el.style.top = `${top}px`;
    el.style.left = `${left}px`;
    el.style.visibility = "";
    chipJoint.classList.add("is-active");
  }

  function closeJointPopover() {
    if (jointPopoverEl) jointPopoverEl.style.display = "none";
    if (chipJoint) chipJoint.classList.remove("is-active");
  }

  if (chipJoint) {
    chipJoint.addEventListener("click", (ev) => {
      ev.stopPropagation();
      if (isJointPopoverOpen()) closeJointPopover();
      else openJointPopover();
    });
  }
  document.addEventListener("click", (ev) => {
    if (!isJointPopoverOpen()) return;
    if (jointPopoverEl && jointPopoverEl.contains(ev.target)) return;
    if (chipJoint && chipJoint.contains(ev.target)) return;
    closeJointPopover();
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") closeJointPopover();
  });
  window.addEventListener("resize", closeJointPopover);
  window.addEventListener("scroll", closeJointPopover, true);

  /* ---- Device popovers (mic / camera) ---- */

  const MIC_DEVICE_KEY = "lampgo_mic_device_id";
  const storedMic = typeof localStorage !== "undefined" ? localStorage.getItem(MIC_DEVICE_KEY) || "" : "";
  let preferredMicId = storedMic;

  function buildDevicePopover(className) {
    const el = document.createElement("div");
    el.className = `device-popover ${className}`;
    el.style.display = "none";
    document.body.appendChild(el);
    return el;
  }

  function positionPopover(popover, anchor) {
    if (!popover || !anchor) return;
    popover.style.visibility = "hidden";
    popover.style.display = "flex";
    const rect = anchor.getBoundingClientRect();
    const menu = popover.getBoundingClientRect();
    let top = rect.bottom + 6;
    let left = rect.left;
    if (top + menu.height > window.innerHeight - 8) {
      top = Math.max(8, rect.top - menu.height - 6);
    }
    if (left + menu.width > window.innerWidth - 8) {
      left = Math.max(8, window.innerWidth - menu.width - 8);
    }
    popover.style.top = `${top}px`;
    popover.style.left = `${left}px`;
    popover.style.visibility = "";
  }

  function renderDevicePopover(popover, { title, subtitle, items, activeId, emptyText, onPick, footer }) {
    const parts = [`<div class="device-popover-title">${esc(title)}</div>`];
    if (subtitle) parts.push(`<div class="device-popover-sub">${esc(subtitle)}</div>`);
    if (!items.length) {
      parts.push(`<div class="device-popover-empty">${esc(emptyText || "暂无可用设备")}</div>`);
    } else {
      parts.push('<div class="device-popover-list"></div>');
    }
    if (footer) parts.push(`<div class="device-popover-footer">${footer}</div>`);
    popover.innerHTML = parts.join("");
    const listEl = popover.querySelector(".device-popover-list");
    if (!listEl) return;
    items.forEach((item) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "device-popover-item";
      const active = item.id === activeId;
      if (active) btn.classList.add("is-active");
      btn.innerHTML = `
        <span class="device-popover-dot ${active ? "is-active" : ""}" aria-hidden="true"></span>
        <span class="device-popover-body">
          <span class="device-popover-name">${esc(item.label)}</span>
          ${item.meta ? `<span class="device-popover-meta">${esc(item.meta)}</span>` : ""}
        </span>
        ${active ? '<span class="device-popover-check">✓</span>' : ""}
      `;
      btn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        onPick && onPick(item);
      });
      listEl.appendChild(btn);
    });
  }

  /* ---- Mic popover ---- */

  let micPopoverEl = null;
  let micDevicesCache = [];

  function ensureMicPopover() {
    if (!micPopoverEl) micPopoverEl = buildDevicePopover("device-popover-mic");
    return micPopoverEl;
  }

  function isMicPopoverOpen() {
    return micPopoverEl && micPopoverEl.style.display !== "none";
  }

  function closeMicPopover() {
    if (micPopoverEl) micPopoverEl.style.display = "none";
    if (chipMic) chipMic.classList.remove("is-active");
  }

  async function loadMicDevices() {
    if (!navigator || !navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return [];
    try {
      const list = await navigator.mediaDevices.enumerateDevices();
      return list.filter((d) => d.kind === "audioinput");
    } catch (err) {
      return [];
    }
  }

  async function openMicPopover() {
    const popover = ensureMicPopover();
    micDevicesCache = await loadMicDevices();
    renderMicPopover();
    positionPopover(popover, chipMic);
    if (chipMic) chipMic.classList.add("is-active");
  }

  function renderMicPopover() {
    if (!micPopoverEl) return;
    const granted = micDevicesCache.some((d) => d.label);
    const items = [
      { id: "", label: "系统默认麦克风", meta: "跟随浏览器/操作系统设置" },
      ...micDevicesCache.map((d, i) => ({
        id: d.deviceId,
        label: d.label || `麦克风 ${d.deviceId.slice(0, 8) || i + 1}`,
        meta: d.deviceId ? d.deviceId.slice(0, 24) + (d.deviceId.length > 24 ? "…" : "") : "",
      })),
    ];
    const subtitle = granted
      ? `检测到 ${micDevicesCache.length} 个输入设备`
      : "设备名称需要麦克风授权后显示";
    renderDevicePopover(micPopoverEl, {
      title: "麦克风输入",
      subtitle,
      items,
      activeId: preferredMicId || "",
      onPick: (item) => selectMicDevice(item.id),
      footer: granted ? "" : '<button type="button" class="device-popover-action" data-action="grant-mic">授权以显示设备名称</button>',
    });
    const grantBtn = micPopoverEl.querySelector('[data-action="grant-mic"]');
    if (grantBtn) {
      grantBtn.addEventListener("click", async (ev) => {
        ev.stopPropagation();
        try {
          const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
          stream.getTracks().forEach((t) => t.stop());
          micDevicesCache = await loadMicDevices();
          renderMicPopover();
        } catch (err) {
          /* ignore */
        }
      });
    }
  }

  function selectMicDevice(id) {
    preferredMicId = id || "";
    try {
      if (id) localStorage.setItem(MIC_DEVICE_KEY, id);
      else localStorage.removeItem(MIC_DEVICE_KEY);
    } catch (_) { /* ignore */ }
    window.dispatchEvent(new CustomEvent("lampgo:mic-selected", { detail: { deviceId: preferredMicId } }));
    const selected = micDevicesCache.find((d) => d.deviceId === id);
    if (chipMic) chipMic.title = selected && selected.label ? `麦克风：${selected.label}` : "系统默认麦克风";
    closeMicPopover();
  }

  if (chipMic) {
    chipMic.style.cursor = "pointer";
    chipMic.classList.add("is-clickable");
    chipMic.addEventListener("click", (ev) => {
      ev.stopPropagation();
      if (isMicPopoverOpen()) closeMicPopover();
      else openMicPopover();
    });
  }

  /* ---- Camera popover ---- */

  let cameraPopoverEl = null;
  let cameraCache = { cameras: [], active: "", available: true, reason: "" };
  let cameraPendingRequest = 0;

  function ensureCameraPopover() {
    if (!cameraPopoverEl) cameraPopoverEl = buildDevicePopover("device-popover-camera");
    return cameraPopoverEl;
  }

  function isCameraPopoverOpen() {
    return cameraPopoverEl && cameraPopoverEl.style.display !== "none";
  }

  function closeCameraPopover() {
    if (cameraPopoverEl) cameraPopoverEl.style.display = "none";
    if (chipCamera) chipCamera.classList.remove("is-active");
  }

  function openCameraPopover() {
    const popover = ensureCameraPopover();
    renderCameraPopover(true);
    positionPopover(popover, chipCamera);
    if (chipCamera) chipCamera.classList.add("is-active");
    send({ type: "list_cameras", request_id: `cam_${Date.now()}` });
    cameraPendingRequest = performance.now();
  }

  function renderCameraPopover(loading) {
    if (!cameraPopoverEl) return;
    const { cameras, active, available, reason } = cameraCache;
    const items = cameras.map((c) => ({
      id: c.port,
      label: c.name ? `${c.name} (port ${c.port})` : `摄像头 ${c.port}`,
      meta: `port = ${c.port}`,
    }));
    items.unshift({ id: "", label: "关闭摄像头", meta: "禁用视觉输入" });
    let subtitle = "";
    if (loading) subtitle = "正在探测可用摄像头...";
    else if (!available) subtitle = reason || "摄像头探测不可用";
    else if (!cameras.length) subtitle = "未在 port 0-3 探测到设备";
    else subtitle = `已探测 ${cameras.length} 个设备 · 运行时切换`;
    renderDevicePopover(cameraPopoverEl, {
      title: "摄像头设备",
      subtitle,
      items,
      activeId: (active || "").trim(),
      emptyText: loading ? "加载中..." : "未检测到可用摄像头",
      onPick: (item) => selectCameraPort(item.id),
    });
  }

  function selectCameraPort(port) {
    cameraCache.active = port || "";
    send({ type: "set_camera", port: port || "", request_id: `cam_set_${Date.now()}` });
    if (chipCamera) {
      chipCamera.title = port ? `摄像头 port = ${port}` : "摄像头已关闭";
    }
    closeCameraPopover();
  }

  if (chipCamera) {
    chipCamera.style.cursor = "pointer";
    chipCamera.classList.add("is-clickable");
    chipCamera.addEventListener("click", (ev) => {
      ev.stopPropagation();
      if (isCameraPopoverOpen()) closeCameraPopover();
      else openCameraPopover();
    });
  }

  document.addEventListener("click", (ev) => {
    if (isMicPopoverOpen() && !micPopoverEl.contains(ev.target) && !(chipMic && chipMic.contains(ev.target))) {
      closeMicPopover();
    }
    if (isCameraPopoverOpen() && !cameraPopoverEl.contains(ev.target) && !(chipCamera && chipCamera.contains(ev.target))) {
      closeCameraPopover();
    }
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") {
      closeMicPopover();
      closeCameraPopover();
    }
  });
  window.addEventListener("resize", () => {
    closeMicPopover();
    closeCameraPopover();
  });

  function send(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(obj));
    }
  }

  function handleMessage(msg) {
    if (msg.type === "status") {
      updateStatus(msg.data);
      return;
    }

    if (msg.type === "event") {
      try {
        handleEvent(msg);
      } catch (err) {
        console.error("handleEvent failed", msg.event, err);
      }
      try {
        logEvent(msg);
      } catch (err) {
        console.error("logEvent failed", msg.event, err);
      }
      return;
    }

    if (msg.type === "list_cameras") {
      if (msg.ok && msg.result) {
        cameraCache = {
          cameras: msg.result.cameras || [],
          active: msg.result.active || "",
          available: msg.result.available !== false,
          reason: msg.result.reason || "",
        };
        if (isCameraPopoverOpen()) renderCameraPopover(false);
      }
      return;
    }

    if (msg.type === "set_camera") {
      if (msg.ok && msg.result) {
        cameraCache.active = msg.result.active || "";
        if (isCameraPopoverOpen()) renderCameraPopover(false);
        send({ type: "status", request_id: `cam_refresh_${Date.now()}` });
      }
      return;
    }

    if (msg.ok && msg.result && msg.result.skills) {
      renderSkills(msg.result.skills);
      return;
    }

    if (msg.ok && msg.result && msg.result.recordings) {
      renderRecordings(msg.result.recordings);
      return;
    }

    if (msg.ok && msg.result && msg.result.expressions) {
      renderExpressions(msg.result.expressions);
      return;
    }

    if (msg.ok && msg.result && msg.result.openclaw_tasks) {
      renderOpenClawTasks(msg.result.openclaw_tasks);
      return;
    }

    if (msg.ok && msg.result && msg.result.openclaw_task) {
      upsertOpenClawTask(msg.result.openclaw_task);
    }

    if (msg.result && msg.result.status === "recording") {
      isMotionRecording = true;
      hasPendingMotionRecording = false;
      recordingFps = Number(msg.result.fps || 30);
      recordingFrames = 0;
      recordingStartTs = Date.now() / 1000;
      updateRecordButtonState();
      if (btnRecordStartConfirm) btnRecordStartConfirm.textContent = "结束录制";
      if (btnRecordStartCancel) btnRecordStartCancel.classList.add("hidden");
      if (recordStartDesc) recordStartDesc.textContent = "录制进行中。按“结束录制”完成采集。";
      if (recordMetrics) recordMetrics.textContent = `采样：${recordingFps} FPS · 0 帧`;
      startRecordTimer();
      if (recordStartDialog && !recordStartDialog.open) recordStartDialog.showModal();
      addSystemMessage(`开始录制（${msg.result.fps} FPS）`);
      return;
    }

    if (msg.result && msg.result.status === "stopped") {
      isMotionRecording = false;
      hasPendingMotionRecording = true;
      recordingFrames = Number(msg.result.frames || recordingFrames || 0);
      stopRecordTimer();
      closeRecordStartDialog();
      resetRecordStartDialogUI();
      updateRecordButtonState();
      addSystemMessage(`录制结束：${msg.result.frames} 帧，等待保存`);
      openRecordNameDialog();
      return;
    }

    if (msg.result && msg.result.status === "saved") {
      hasPendingMotionRecording = false;
      pendingOverwriteSave = false;
      stopRecordTimer();
      closeRecordStartDialog();
      resetRecordStartDialogUI();
      updateRecordButtonState();
      closeRecordNameDialog();
      addSystemMessage(`录制已保存：${msg.result.name}`);
      return;
    }

    if (msg.result && msg.result.status === "discarded") {
      hasPendingMotionRecording = false;
      pendingOverwriteSave = false;
      stopRecordTimer();
      closeRecordStartDialog();
      resetRecordStartDialogUI();
      updateRecordButtonState();
      closeRecordNameDialog();
      addSystemMessage("录制已放弃");
      return;
    }

    if (msg.result && msg.result.status === "name_conflict") {
      pendingOverwriteSave = true;
      if (recordNameError) recordNameError.textContent = "同名动作已存在，再次点击保存将覆盖";
      if (btnRecordSave) btnRecordSave.textContent = "确认覆盖";
      return;
    }

    if (msg.request_id && pendingMessages.has(msg.request_id)) {
      finishPending(msg);
      return;
    }

    if (!msg.ok && msg.error) {
      addSystemMessage(`操作失败：${msg.error}`);
    }
  }

  function handleEvent(msg) {
    const evt = msg.event;
    const data = msg.data || {};

    if (evt === "OpenClawTaskUpdated" && data.task) upsertOpenClawTask(data.task);
    else if (evt === "OpenClawPromotionRequested" && data.task) upsertOpenClawTask(data.task);
    else if (evt === "OpenClawPromotionDecision" && data.task) upsertOpenClawTask(data.task);

    if (evt === "TtsAudio" && data.audio) handleTtsAudio(data.audio, data.format || "mp3");

    const requestId = data.request_id || "";
    const bubble = requestId ? pendingMessages.get(requestId) : null;
    if (!bubble) return;

    const log = ensureActivityLog(bubble);
    if (!log) return;
    const preludeEl = getPreludeArea(log);
    const epilogueEl = getEpilogueArea(log);

    switch (evt) {
      case "IntentRouting":
        setRouteTrail(log, [{ label: "关键词路由", state: "active" }]);
        break;
      case "IntentProgress":
        if (data.message) {
          if (data.stage === "audio_transcribed") {
            updateUserBubbleText(requestId, data.message.replace(/^听到：\s*/, ""));
          }
          if (data.stage === "llm_fallback" && requestId) {
            activeAgentRequestId = requestId;
            btnStop.classList.remove("hidden");
          }
          if (data.stage === "llm_fallback") {
            setRouteTrail(log, [
              { label: "关键词", state: "miss" },
              { label: "LLM Agent", state: "active" },
            ]);
          } else if (data.stage === "openclaw_handoff") {
            setRouteTrail(log, [
              { label: "关键词", state: "miss" },
              { label: "OpenClaw", state: "active" },
            ]);
          } else if (data.stage === "llm_request") {
            finalizeStreamingThinking(requestId);
            markAllActiveDone(preludeEl);
            bumpTurn(log);
          } else if (data.stage === "llm_thinking_delta") {
            appendThinkingDelta(bubble, requestId, data.message);
          } else if (data.stage === "llm_response_delta") {
            appendResponseDelta(bubble, requestId, data.message);
          } else if (data.stage === "llm_narration") {
            finalizeNarration(bubble, requestId, data.message);
          } else if (data.stage === "llm_thinking") {
            appendThinkingToBubble(bubble, data.message);
          } else if (preludeEl.querySelector(".step-row.active:last-child")) {
            updateActiveStep(preludeEl, data.message);
          } else {
            addStep(preludeEl, data.message, "active");
          }
        }
        break;
      case "IntentResolved":
        markLastDone(preludeEl);
        setRouteTrail(log, buildResolvedTrail(data));
        if (requestId && requestId === activeAgentRequestId) {
          activeAgentRequestId = null;
          btnStop.classList.add("hidden");
        }
        break;
      case "OpenClawTaskUpdated":
        markLastDone(preludeEl);
        addStep(preludeEl, `OpenClaw 状态：${formatOpenClawStatus(data.task && data.task.status)}`, "done");
        break;
      case "OpenClawPromotionRequested":
        markLastDone(preludeEl);
        addStep(preludeEl, "OpenClaw 生成了 promoted 待确认方案", "active");
        break;
      case "OpenClawPromotionDecision":
        markLastDone(preludeEl);
        addStep(
          preludeEl,
          data.decision === "approve" ? "已确认 promoted" : "已拒绝 promoted",
          data.decision === "approve" ? "done" : "error"
        );
        break;
      case "ToolCallPlanned":
        addToolChip(log, data.turn_index, data.tool_index, data.tool_name, data.arguments);
        break;
      case "ToolCallFinished":
        finishToolChip(log, data.turn_index, data.tool_index, data.status, data.summary);
        break;
      case "AgentFinished":
        if (data.stop_reason === "finish_response") addStep(epilogueEl, "任务完成", "done");
        else if (data.stop_reason === "user_cancelled") {
          markAllActiveDone(preludeEl);
          addStep(epilogueEl, "已停止", "error");
        } else addStep(epilogueEl, `流程结束：${data.stop_reason || "unknown"}`, "error");
        if (requestId && requestId === activeAgentRequestId) {
          activeAgentRequestId = null;
          btnStop.classList.add("hidden");
        }
        break;
      case "SkillStarted":
        addStep(preludeEl, `执行技能：${data.skill_id}`, "active");
        break;
      case "SkillProgress":
        updateActiveStep(preludeEl, `执行中 ${Math.round(data.progress * 100)}% ${data.message || ""}`.trim());
        break;
      case "SkillFinished":
        markLastDone(preludeEl);
        addStep(preludeEl, data.status === "ok" ? "执行完成" : `结束：${data.status}`, data.status === "ok" ? "done" : "error");
        break;
      case "SkillCancelled":
        markLastDone(preludeEl);
        addStep(preludeEl, "已取消", "error");
        break;
      case "ChatMessage":
        if (data.content) appendTextToBubble(bubble, data.content);
        break;
    }

    if (requestId) schedulePendingSnapshot(requestId);
  }

  const EVENT_LOG_MAX = 500;

  function eventCategory(evtName) {
    if (!evtName) return { cls: "", icon: "•" };
    if (evtName.startsWith("Skill")) return { cls: "evt-skill", icon: "🎯" };
    if (evtName.startsWith("Safety") || evtName.startsWith("EStop")) return { cls: "evt-safety", icon: "⚠" };
    if (evtName === "AgentFinished" || evtName.startsWith("Intent")) return { cls: "evt-intent", icon: "🧠" };
    if (evtName.startsWith("ToolCall")) return { cls: "evt-tool", icon: "🔧" };
    if (evtName.startsWith("OpenClaw")) return { cls: "evt-openclaw", icon: "⚙" };
    if (evtName === "ChatMessage") return { cls: "evt-chat", icon: "💬" };
    if (evtName === "TtsAudio") return { cls: "evt-audio", icon: "🔊" };
    return { cls: "", icon: "•" };
  }

  function summarizeEventData(evtName, data) {
    if (!data || typeof data !== "object") return "";
    if (evtName === "TtsAudio") {
      const size = typeof data.audio === "string" ? data.audio.length : 0;
      return `format=${data.format || "?"} · payload=${size}B`;
    }
    if (evtName === "IntentProgress") {
      const stage = data.stage ? `[${data.stage}] ` : "";
      return `${stage}${data.message || ""}`.trim();
    }
    if (evtName === "IntentRouting") return data.text || "";
    if (evtName === "IntentResolved") {
      const parts = [data.intent_type, data.source, data.detail].filter(Boolean);
      return parts.join(" · ");
    }
    if (evtName === "ToolCallPlanned" || evtName === "ToolCallFinished") {
      const turn = data.turn_index != null ? `T${data.turn_index}` : "";
      const idx = data.tool_index != null ? `#${data.tool_index}` : "";
      const status = data.status ? ` → ${data.status}` : "";
      const err = data.error ? ` (err: ${data.error})` : "";
      return `${turn}${idx} ${data.tool_name || ""}${status}${err}`.trim();
    }
    if (evtName === "AgentFinished") {
      return `${data.stop_reason || ""} · ${data.tool_call_count ?? 0} calls`;
    }
    if (evtName.startsWith("Skill")) {
      const invo = data.invocation_id ? ` (${String(data.invocation_id).slice(0, 8)})` : "";
      return `${data.skill_id || ""}${invo}${data.reason ? " · " + data.reason : ""}`;
    }
    if (evtName.startsWith("OpenClaw")) {
      const task = data.task || {};
      const status = task.status || data.status || "";
      return `${task.task_id || data.task_id || ""} · ${status}`.trim();
    }
    return "";
  }

  function formatEventJson(evtName, data) {
    if (!data || typeof data !== "object") return "";
    let sanitized = data;
    if (evtName === "TtsAudio" && data.audio && data.audio.length > 120) {
      sanitized = { ...data, audio: data.audio.slice(0, 120) + `… (${data.audio.length}B)` };
    }
    try {
      return JSON.stringify(sanitized, null, 2);
    } catch {
      return String(sanitized);
    }
  }

  const NOISY_PROGRESS_STAGES = new Set([
    "llm_thinking_delta",
    "llm_response_delta",
  ]);

  function logEvent(msg) {
    if (!eventLog || !msg || !msg.event) return;
    if (msg.event === "IntentProgress" && msg.data && NOISY_PROGRESS_STAGES.has(msg.data.stage)) {
      return; // skip high-frequency streaming chunks from the activity log
    }
    const { cls, icon } = eventCategory(msg.event);
    const ts = new Date((msg.ts || Date.now() / 1000) * 1000);
    const timeStr = ts.toLocaleTimeString("zh-CN", { hour12: false });
    const summary = summarizeEventData(msg.event, msg.data);

    const item = document.createElement("details");
    item.className = `event-item ${cls}`.trim();
    item.innerHTML = `
      <summary class="event-item-head">
        <span class="event-icon" aria-hidden="true">${icon}</span>
        <span class="event-time">${timeStr}</span>
        <span class="event-name">${esc(msg.event)}</span>
        <span class="event-summary">${esc(summary)}</span>
      </summary>
      <pre class="event-body">${esc(formatEventJson(msg.event, msg.data))}</pre>
    `;
    eventLog.appendChild(item);

    while (eventLog.childElementCount > EVENT_LOG_MAX) {
      eventLog.removeChild(eventLog.firstElementChild);
    }
    eventLog.scrollTop = eventLog.scrollHeight;
  }

  function updateStatus(data) {
    if (!data) return;

    const healthy = data.device_health === "ok" && !data.estopped;
    const jointEntries = Object.entries(data.joint_positions || {});
    latestJointPositions = data.joint_positions || {};

    if (chipJoint) {
      const online = healthy && jointEntries.length > 0;
      chipJoint.classList.toggle("is-offline", !online);
      chipJoint.classList.toggle("is-online", online);
      if (chipJointDot) {
        chipJointDot.classList.toggle("is-online", online);
        chipJointDot.classList.toggle("is-offline", !online);
      }
      const parts = [];
      parts.push(`健康：${data.device_health || "--"}`);
      if (data.is_busy) parts.push("忙碌");
      if (data.running_skill) parts.push(`技能：${data.running_skill}`);
      if (data.estopped) parts.push(`急停${data.estop_reason ? ` (${data.estop_reason})` : ""}`);
      chipJoint.title = `点击查看各关节实时角度 · ${parts.join(" · ")}`;
    }

    if (chipCamera) {
      const online = Boolean(data.camera_ready);
      chipCamera.classList.toggle("is-online", online);
      chipCamera.classList.toggle("is-offline", !online);
      if (chipCameraDot) {
        chipCameraDot.classList.toggle("is-online", online);
        chipCameraDot.classList.toggle("is-offline", !online);
      }
      chipCamera.title = online ? "摄像头已接入" : "摄像头未配置";
    }

    if (chipLed) {
      const online = Boolean(data.led_ready);
      chipLed.classList.toggle("is-online", online);
      chipLed.classList.toggle("is-offline", !online);
      if (chipLedDot) {
        chipLedDot.classList.toggle("is-online", online);
        chipLedDot.classList.toggle("is-offline", !online);
      }
      chipLed.title = online ? "LED 控制器已连接" : "LED 控制器未连接";
    }

    if (isJointPopoverOpen()) renderJointPopoverContent();

    const rec = data.recording || {};
    isMotionRecording = Boolean(rec.active);
    hasPendingMotionRecording = Boolean(rec.has_buffer) && !isMotionRecording;
    recordingFps = Number(rec.fps || recordingFps || 30);
    recordingFrames = Number(rec.frames || 0);
    if (isMotionRecording && recordMetrics) {
      recordMetrics.textContent = `采样：${recordingFps} FPS · ${recordingFrames} 帧`;
    }
    updateRecordButtonState();
  }

  function formatIntentResolved(data) {
    const source = formatIntentSource(data.source);
    if (data.intent_type === "openclaw") return `${source}已接管复杂任务`;
    if (data.intent_type === "agent") return `${source}完成多步工具编排`;
    if (data.intent_type === "skill" && data.skill_id) {
      if (data.source === "keyword" && data.matched_keyword) {
        return `${source}命中"${data.matched_keyword}" -> 技能：${data.skill_id}`;
      }
      return `${source}识别为技能：${data.skill_id}`;
    }
    if (data.intent_type === "chat") return `${source}识别为聊天回复`;
    return `${source}判定为复杂请求`;
  }

  function formatIntentSource(source) {
    if (source === "keyword") return "关键词";
    if (source === "llm") return "LLM";
    if (source === "llm_web_search") return "LLM 网页搜索";
    if (source === "openclaw") return "OpenClaw";
    return "意图路由";
  }

  function formatToolArguments(args) {
    const entries = Object.entries(args || {});
    if (!entries.length) return "()";
    return `(${entries.map(([key, value]) => `${key}=${JSON.stringify(value)}`).join(", ")})`;
  }

  /* ---- Skill / recording / expression render ---- */

  function initialIcon(name) {
    const trimmed = (name || "").trim();
    if (!trimmed) return "?";
    const first = Array.from(trimmed)[0];
    return first.toUpperCase();
  }

  function makeSkillCard({ title, meta, onClick, tooltip, icon }) {
    const btn = document.createElement("button");
    btn.className = "skill-card";
    btn.type = "button";
    if (tooltip) btn.title = tooltip;
    btn.innerHTML = `
      <span class="skill-card-icon">${esc(icon || initialIcon(title))}</span>
      <span class="skill-card-body">
        <span class="skill-card-title">${esc(title)}</span>
        ${meta ? `<span class="skill-card-meta">${esc(meta)}</span>` : ""}
      </span>
    `;
    btn.addEventListener("click", onClick);
    return btn;
  }

  let latestSkills = [];
  let latestRecordings = [];
  let latestExpressions = [];
  let skillQuery = "";
  let recordingQuery = "";
  let expressionQuery = "";

  function renderEmptyCell(grid, text) {
    const empty = document.createElement("div");
    empty.className = "skill-grid-empty";
    empty.textContent = text;
    grid.appendChild(empty);
  }

  function updateCount(el, shown, total) {
    if (!el) return;
    el.textContent = shown === total ? String(total) : `${shown}/${total}`;
  }

  function renderSkills(skills) {
    if (Array.isArray(skills)) {
      latestSkills = skills.filter((s) => !["estop", "play_recording"].includes(s.skill_id));
    }
    skillGrid.innerHTML = "";
    const q = skillQuery.trim().toLowerCase();
    const filtered = q
      ? latestSkills.filter((s) =>
          (s.skill_id || "").toLowerCase().includes(q) ||
          (s.description || "").toLowerCase().includes(q))
      : latestSkills;
    filtered.forEach((skill) => {
      const card = makeSkillCard({
        title: skill.skill_id,
        meta: skill.description || "",
        tooltip: skill.description,
        onClick: () => invokeSkill(skill.skill_id),
      });
      skillGrid.appendChild(card);
    });
    if (!filtered.length) renderEmptyCell(skillGrid, q ? `无匹配「${q}」的技能` : "暂无技能");
    updateCount(skillCountEl, filtered.length, latestSkills.length);
  }

  function renderRecordings(recordings) {
    if (Array.isArray(recordings)) latestRecordings = recordings.slice();
    recordingGrid.innerHTML = "";
    const q = recordingQuery.trim().toLowerCase();
    const filtered = q
      ? latestRecordings.filter((name) => {
          const expr = getRecordingExpression(name) || "";
          return name.toLowerCase().includes(q) || expr.toLowerCase().includes(q);
        })
      : latestRecordings;
    filtered.forEach((name) => {
      const expression = getRecordingExpression(name);
      const card = makeSkillCard({
        title: name,
        meta: `表情 · ${expression}`,
        tooltip: `播放录制动作：${name} · 推荐表情：${expression}`,
        onClick: () => invokeRecording(name),
      });
      recordingGrid.appendChild(card);
    });
    if (!filtered.length) renderEmptyCell(recordingGrid, q ? `无匹配「${q}」的录制动作` : "暂无录制动作");
    updateCount(recordingCountEl, filtered.length, latestRecordings.length);
  }

  function renderExpressions(expressions) {
    if (Array.isArray(expressions)) latestExpressions = expressions.slice();
    expressionGrid.innerHTML = "";
    const q = expressionQuery.trim().toLowerCase();
    const filtered = q
      ? latestExpressions.filter((name) => name.toLowerCase().includes(q))
      : latestExpressions;
    filtered.forEach((name) => {
      const card = makeSkillCard({
        title: name,
        meta: "LED 表情",
        tooltip: `切换灯光表情：${name}`,
        onClick: () => invokeExpression(name),
      });
      expressionGrid.appendChild(card);
    });
    if (!filtered.length) renderEmptyCell(expressionGrid, q ? `无匹配「${q}」的灯光表情` : "暂无灯光表情");
    updateCount(expressionCountEl, filtered.length, latestExpressions.length);
  }

  function wireSkillSearch(input, apply) {
    if (!input) return;
    input.addEventListener("input", () => {
      apply(input.value || "");
      const wrapper = input.closest(".skill-section-search");
      if (wrapper) wrapper.classList.toggle("has-value", !!input.value);
    });
  }

  wireSkillSearch(skillSearchEl, (v) => { skillQuery = v; renderSkills(); });
  wireSkillSearch(recordingSearchEl, (v) => { recordingQuery = v; renderRecordings(); });
  wireSkillSearch(expressionSearchEl, (v) => { expressionQuery = v; renderExpressions(); });

  document.querySelectorAll(".skill-search-clear").forEach((btn) => {
    btn.addEventListener("click", () => {
      const targetId = btn.dataset.target;
      const input = targetId ? document.getElementById(targetId) : null;
      if (!input) return;
      input.value = "";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
    });
  });

  /* ---- OpenClaw ---- */

  function renderOpenClawTasks(tasks) {
    openclawTasks.clear();
    const list = tasks || [];
    list.forEach((task) => {
      if (task && task.task_id) {
        openclawTasks.set(task.task_id, task);
        openclawPrevStatus.set(task.task_id, task.status || "");
      }
    });
    paintOpenClawTasks();
    list.forEach((task) => maybePostOpenClawFollowup(task, ""));
  }

  function upsertOpenClawTask(task) {
    if (!task || !task.task_id) return;
    const prev = openclawPrevStatus.get(task.task_id) || "";
    openclawTasks.set(task.task_id, task);
    openclawPrevStatus.set(task.task_id, task.status || "");
    paintOpenClawTasks();
    maybePostOpenClawFollowup(task, prev);
  }

  function maybePostOpenClawFollowup(task, prevStatus) {
    if (!task || !task.task_id) return;
    const TERMINAL = new Set(["completed", "failed", "promoted", "rejected"]);
    if (!TERMINAL.has(task.status)) return;
    if (prevStatus === task.status) return;
    if (openclawFollowups.has(task.task_id)) return;

    openclawFollowups.add(task.task_id);
    persistFollowupSet();

    const sessionId = openclawTaskSessions.get(task.task_id) || activeSessionId;
    if (!sessionId) return;

    const detail = (task.detail || "").trim();
    let prefix = "";
    if (task.status === "completed") prefix = "OpenClaw 已完成任务。";
    else if (task.status === "failed") prefix = "OpenClaw 执行失败。";
    else if (task.status === "promoted") prefix = "OpenClaw 已沉淀该能力。";
    else if (task.status === "rejected") prefix = "已拒绝沉淀。";
    const body = detail ? `${prefix}\n\n${detail}` : prefix;
    const meta = {
      openclaw_task_id: task.task_id,
      openclaw_user_text: task.user_text || "",
      openclaw_status: task.status || "",
    };

    pushMessageToSession("assistant", body, meta, { sessionId });

    if (sessionId === activeSessionId && currentView === "chat") {
      renderHistoricalAssistantBubble(body, Date.now(), meta);
      scrollChat();
    }
  }

  function openClawStatusKey(status) {
    if (status === "queued") return "queued";
    if (
      status === "planning" ||
      status === "executing" ||
      status === "executing_with_existing_tools" ||
      status === "generating_temporary_asset"
    ) {
      return "running";
    }
    if (status === "awaiting_promotion_confirmation") return "awaiting";
    if (status === "promoted" || status === "completed") return "promoted";
    if (status === "rejected") return "rejected";
    if (status === "failed") return "failed";
    return "running";
  }

  function paintOpenClawTasks() {
    const tasks = Array.from(openclawTasks.values()).sort((a, b) => (b.created_at || 0) - (a.created_at || 0));

    const counts = { queued: 0, running: 0, awaiting: 0, promoted: 0, failed: 0 };
    tasks.forEach((t) => {
      const k = openClawStatusKey(t.status);
      if (k === "rejected" || k === "failed") counts.failed += 1;
      else if (counts[k] !== undefined) counts[k] += 1;
    });
    if (ocCountQueued) ocCountQueued.textContent = String(counts.queued);
    if (ocCountRunning) ocCountRunning.textContent = String(counts.running);
    if (ocCountAwaiting) ocCountAwaiting.textContent = String(counts.awaiting);
    if (ocCountPromoted) ocCountPromoted.textContent = String(counts.promoted);
    if (ocCountFailed) ocCountFailed.textContent = String(counts.failed);

    if (!openclawTaskList) return;
    if (!tasks.length) {
      openclawTaskList.innerHTML = '<div class="openclaw-empty">暂无复杂任务</div>';
      return;
    }
    openclawTaskList.innerHTML = tasks.map(renderOpenClawTaskCard).join("");
    openclawTaskList.querySelectorAll("[data-confirm-task]").forEach((btn) => {
      btn.addEventListener("click", () => {
        confirmPromotion(btn.dataset.confirmTask, btn.dataset.proposalId, btn.dataset.decision);
      });
    });
  }

  function renderOpenClawTaskCard(task) {
    const proposals = Array.isArray(task.proposals) ? task.proposals : [];
    const statusKey = openClawStatusKey(task.status);
    return `
      <div class="openclaw-task-card" data-task-id="${esc(task.task_id)}">
        <div class="openclaw-task-head">
          <div class="openclaw-task-title">${esc(task.user_text || task.task_id)}</div>
          <div class="openclaw-task-status st-${esc(statusKey)}">${esc(formatOpenClawStatus(task.status))}</div>
        </div>
        <div class="openclaw-task-detail">${esc(task.detail || task.reason || "等待 OpenClaw 处理")}</div>
        ${proposals.map((proposal) => renderProposalCard(task, proposal)).join("")}
      </div>
    `;
  }

  function renderProposalCard(task, proposal) {
    const files = (proposal.files || []).map((file) => esc(file)).join("<br>");
    const risks = (proposal.risks || []).map((risk) => esc(risk)).join("<br>");
    const pending = proposal.status === "pending" && task.status === "awaiting_promotion_confirmation";
    return `
      <div class="proposal-card">
        <div class="proposal-title">${esc(proposal.title || proposal.proposal_type)}</div>
        <div class="proposal-meta">${esc(proposal.proposal_type || "proposal")} · ${esc(formatProposalStatus(proposal.status))}</div>
        <div class="proposal-summary">${esc(proposal.summary || "")}</div>
        ${files ? `<div class="proposal-files"><strong>涉及文件</strong><br>${files}</div>` : ""}
        ${risks ? `<div class="proposal-risks"><strong>风险提示</strong><br>${risks}</div>` : ""}
        ${
          pending
            ? `<div class="proposal-actions">
                <button class="proposal-btn approve" type="button" data-confirm-task="${esc(task.task_id)}" data-proposal-id="${esc(proposal.proposal_id)}" data-decision="approve">确认沉淀</button>
                <button class="proposal-btn reject" type="button" data-confirm-task="${esc(task.task_id)}" data-proposal-id="${esc(proposal.proposal_id)}" data-decision="reject">暂不沉淀</button>
              </div>`
            : ""
        }
      </div>
    `;
  }

  function confirmPromotion(taskId, proposalId, decision) {
    send({
      type: "confirm_promotion",
      task_id: taskId,
      proposal_id: proposalId,
      decision,
      request_id: nextId(),
    });
  }

  function formatOpenClawStatus(status) {
    switch (status) {
      case "queued": return "排队中";
      case "planning": return "规划中";
      case "executing": return "执行中";
      case "executing_with_existing_tools": return "执行中";
      case "generating_temporary_asset": return "生成 temporary";
      case "awaiting_promotion_confirmation": return "等待确认";
      case "promoted": return "已 promoted";
      case "completed": return "已完成";
      case "rejected": return "已拒绝";
      case "failed": return "失败";
      default: return status || "--";
    }
  }

  function formatProposalStatus(status) {
    if (status === "approved") return "已确认";
    if (status === "rejected") return "已拒绝";
    return "待确认";
  }

  /* ---- Invoke actions ---- */

  function invokeSkill(skillId) {
    clearEmptyState();
    const requestId = nextId();
    const bubble = addAssistantBubble(requestId);
    addStep(getPreludeArea(ensureActivityLog(bubble)), `调用 ${skillId}`, "active");
    send({ type: "invoke", skill_id: skillId, params: {}, wait: true, request_id: requestId });
  }

  function invokeRecording(name) {
    clearEmptyState();
    const requestId = nextId();
    const bubble = addAssistantBubble(requestId);
    const expression = getRecordingExpression(name);
    addStep(
      getPreludeArea(ensureActivityLog(bubble)),
      `播放录制动作 ${name} · 模式 ${playbackMode} · 表情 ${expression}`,
      "active"
    );
    send({
      type: "invoke",
      skill_id: "play_recording",
      params: { name, expression, playback_mode: playbackMode },
      wait: true,
      request_id: requestId,
    });
  }

  function invokeExpression(name) {
    clearEmptyState();
    const requestId = nextId();
    const bubble = addAssistantBubble(requestId);
    addStep(getPreludeArea(ensureActivityLog(bubble)), `切换灯光表情 ${name}`, "active");
    send({
      type: "invoke",
      skill_id: "set_expression",
      params: { expression: name },
      wait: true,
      request_id: requestId,
    });
  }

  /* ---- Recording flow ---- */

  function updateRecordButtonState() {
    if (!btnRecordMotionPanel) return;
    if (isMotionRecording) {
      eachRecordButton((btn) => {
        btn.textContent = "结束录制";
        btn.classList.add("is-recording");
      });
      return;
    }
    if (hasPendingMotionRecording) {
      eachRecordButton((btn) => {
        btn.textContent = "等待保存";
        btn.classList.remove("is-recording");
      });
      return;
    }
    eachRecordButton((btn) => {
      btn.textContent = "录制动作";
      btn.classList.remove("is-recording");
    });
  }

  function startMotionRecording() {
    send({ type: "recording_start", fps: 30, request_id: nextId() });
  }

  function openRecordStartDialog() {
    if (!recordStartDialog) {
      startMotionRecording();
      return;
    }
    resetRecordStartDialogUI();
    recordStartDialog.showModal();
  }

  function closeRecordStartDialog() {
    if (!recordStartDialog || !recordStartDialog.open) return;
    recordStartDialog.close();
  }

  function stopMotionRecording() {
    send({ type: "recording_stop", request_id: nextId() });
  }

  function openRecordNameDialog() {
    if (!recordNameDialog) return;
    pendingOverwriteSave = false;
    recordNameError.textContent = "";
    recordNameInput.value = "";
    if (btnRecordSave) btnRecordSave.textContent = "保存";
    recordNameDialog.showModal();
    recordNameInput.focus();
  }

  function closeRecordNameDialog() {
    if (!recordNameDialog || !recordNameDialog.open) return;
    pendingOverwriteSave = false;
    if (btnRecordSave) btnRecordSave.textContent = "保存";
    recordNameDialog.close();
  }

  function saveMotionRecording(name, overwrite = false) {
    send({ type: "recording_save", name, overwrite, request_id: nextId() });
  }

  function discardMotionRecording() {
    send({ type: "recording_discard", request_id: nextId() });
  }

  /* ---- Chat form ---- */

  chatForm.addEventListener("submit", (e) => {
    e.preventDefault();
    void unlockTtsPlayback();

    if (isVoiceMode) {
      if (mediaRecorder && mediaRecorder.state !== "inactive") mediaRecorder.stop();
      stopVoiceMode();
      return;
    }

    const text = chatInput.value.trim();
    if (!text) return;

    clearEmptyState();
    chatInput.value = "";
    addUserBubble(text);
    pushMessageToSession("user", text);

    const requestId = nextId();
    addAssistantBubble(requestId);
    send({ type: "text", input: text, request_id: requestId });
  });

  function addUserBubble(text, requestId) {
    const row = document.createElement("div");
    row.className = "flex justify-end mb-4";
    row.innerHTML = `<div class="msg-bubble-wrap"><div class="msg-user">${esc(text)}</div><span class="msg-time">${formatTime()}</span></div>`;
    chatMessages.appendChild(row);
    const bubble = row.querySelector(".msg-user");
    if (requestId && bubble) pendingUserMessages.set(requestId, bubble);
    scrollChat();
    return bubble;
  }

  function renderHistoricalUserBubble(text, ts) {
    const row = document.createElement("div");
    row.className = "flex justify-end mb-4";
    row.innerHTML = `<div class="msg-bubble-wrap"><div class="msg-user">${esc(text)}</div><span class="msg-time">${formatTime(new Date(ts))}</span></div>`;
    chatMessages.appendChild(row);
  }

  function renderHistoricalAssistantBubble(text, ts, meta) {
    const row = document.createElement("div");
    row.className = "flex justify-start mb-4";
    const responseHtml = text ? `<div class="final-response">${formatAssistantText(text)}</div>` : "";
    row.innerHTML = `
      <div class="msg-bubble-wrap">
        <div class="msg-assistant">
          <div class="steps"></div>
          <div class="response-text">${responseHtml}</div>
        </div>
        <span class="msg-time">${formatTime(new Date(ts))}</span>
      </div>
    `;
    chatMessages.appendChild(row);
    const bubble = row.querySelector(".msg-assistant");
    if (bubble && meta && meta.activity_html) {
      rehydrateActivityLog(bubble, meta.activity_html);
    }
    if (bubble && meta && meta.openclaw_task_id) {
      appendOpenClawLinkCard(bubble, {
        task_id: meta.openclaw_task_id,
        user_text: meta.openclaw_user_text || "",
        status: meta.openclaw_status || "",
      });
    }
    if (bubble && meta && meta.pending && meta.requestId) {
      const requestId = meta.requestId;
      bubble.dataset.requestId = requestId;
      bubble.classList.add("is-pending");
      pendingMessages.set(requestId, bubble);
      const pushed = pendingAssistantEntries.get(requestId);
      if (!pushed) {
        const session = sessions.find((s) => s.messages.some((m) => m.meta && m.meta.requestId === requestId));
        const entry = session && session.messages.find((m) => m.meta && m.meta.requestId === requestId);
        if (session && entry) pendingAssistantEntries.set(requestId, { session, entry });
      }
    }
  }

  function appendOpenClawLinkCard(bubble, task) {
    if (!bubble || !task || !task.task_id) return;
    const responseText = bubble.querySelector(".response-text") || bubble;
    const card = document.createElement("button");
    card.type = "button";
    card.className = "openclaw-link-card";
    card.dataset.taskId = task.task_id;
    const latest = openclawTasks.get(task.task_id) || task;
    const status = latest.status || task.status || "";
    const title = latest.user_text || task.user_text || "OpenClaw 任务";
    const statusLabel = status ? formatOpenClawStatus(status) : "查看详情";
    const statusKey = status ? openClawStatusKey(status) : "running";
    card.innerHTML = `
      <span class="openclaw-link-icon" aria-hidden="true">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14M13 6l6 6-6 6"/></svg>
      </span>
      <span class="openclaw-link-body">
        <span class="openclaw-link-title">${esc(title)}</span>
        <span class="openclaw-link-meta">在 OpenClaw 页面查看详情</span>
      </span>
      <span class="openclaw-link-status st-${esc(statusKey)}">${esc(statusLabel)}</span>
    `;
    card.addEventListener("click", () => jumpToOpenClawTask(task.task_id));
    responseText.appendChild(card);
  }

  function jumpToOpenClawTask(taskId) {
    showView("openclaw");
    setTimeout(() => {
      if (!openclawTaskList || !taskId) return;
      const el = openclawTaskList.querySelector(`[data-task-id="${CSS.escape(taskId)}"]`);
      if (!el) return;
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.add("is-flash");
      setTimeout(() => el.classList.remove("is-flash"), 1600);
    }, 60);
  }

  function addAssistantBubble(requestId) {
    const row = document.createElement("div");
    row.className = "flex justify-start mb-4";
    const wrap = document.createElement("div");
    wrap.className = "msg-bubble-wrap";
    const bubble = document.createElement("div");
    bubble.className = "msg-assistant";
    bubble.innerHTML = '<div class="steps"></div><div class="response-text"></div>';
    const time = document.createElement("span");
    time.className = "msg-time";
    time.textContent = formatTime();
    wrap.appendChild(bubble);
    wrap.appendChild(time);
    row.appendChild(wrap);
    chatMessages.appendChild(row);
    pendingMessages.set(requestId, bubble);
    bubble.dataset.requestId = requestId;
    registerPendingAssistantEntry(requestId);
    scrollChat();
    return bubble;
  }

  function registerPendingAssistantEntry(requestId) {
    if (!requestId) return;
    if (pendingAssistantEntries.has(requestId)) return;
    const session = ensureActiveSession();
    if (!session) return;
    const entry = {
      role: "assistant",
      text: "",
      ts: Date.now(),
      meta: { pending: true, requestId },
    };
    session.messages.push(entry);
    session.updatedAt = Date.now();
    persistSessions();
    pendingAssistantEntries.set(requestId, { session, entry });
  }

  function schedulePendingSnapshot(requestId) {
    if (!requestId) return;
    const pushed = pendingAssistantEntries.get(requestId);
    if (!pushed || !pushed.entry) return;
    if (pendingSnapshotTimers.has(requestId)) return;
    const timer = setTimeout(() => {
      pendingSnapshotTimers.delete(requestId);
      const bubble = pendingMessages.get(requestId);
      if (!bubble) return;
      const current = pendingAssistantEntries.get(requestId);
      if (!current || !current.entry) return;
      const meta = current.entry.meta || (current.entry.meta = {});
      const html = captureActivityHtml(bubble);
      if (html) meta.activity_html = html;
      const liveText = getBubbleResponseText(bubble);
      if (liveText) current.entry.text = liveText;
      meta.pending = true;
      current.entry.ts = current.entry.ts || Date.now();
      persistSessions();
    }, 400);
    pendingSnapshotTimers.set(requestId, timer);
  }

  function flushPendingSnapshot(requestId) {
    const timer = pendingSnapshotTimers.get(requestId);
    if (timer) {
      clearTimeout(timer);
      pendingSnapshotTimers.delete(requestId);
    }
  }

  function getBubbleResponseText(bubble) {
    if (!bubble) return "";
    const finalEl = bubble.querySelector(".response-text .final-response");
    if (!finalEl) return "";
    return (finalEl.textContent || "").trim();
  }

  function finishPending(msg) {
    const bubble = pendingMessages.get(msg.request_id);
    if (!bubble) return;

    finalizeStreamingThinking(msg.request_id);
    streamingState.delete(msg.request_id);

    const log = ensureActivityLog(bubble);
    const stepsEl = bubble.querySelector(".steps");
    if (log) {
      markAllActiveDone(getPreludeArea(log));
      markAllActiveDone(getEpilogueArea(log));
    } else {
      markLastDone(stepsEl);
    }

    const result = msg.result || {};
    const text = result.response || result.chat_response;
    const ocTask = result.openclaw_task;

    if (ocTask && ocTask.task_id) {
      appendOpenClawLinkCard(bubble, ocTask);
      rememberOpenClawTaskSession(ocTask.task_id, activeSessionId);
    }

    if (!msg.ok && msg.error) {
      const errTarget = log ? getEpilogueArea(log) : stepsEl;
      addStep(errTarget, `错误：${msg.error}`, "error");
    }

    if (msg.request_id === activeAgentRequestId) {
      activeAgentRequestId = null;
      btnStop.classList.add("hidden");
    }

    if (log) finalizeActivity(log);

    if (text) {
      appendTextToBubble(bubble, text);
    }

    flushPendingSnapshot(msg.request_id);
    const pushed = pendingAssistantEntries.get(msg.request_id);
    if (pushed && pushed.entry) {
      const meta = pushed.entry.meta || (pushed.entry.meta = {});
      if (text) pushed.entry.text = text;
      if (ocTask) {
        meta.openclaw_task_id = ocTask.task_id;
        meta.openclaw_user_text = ocTask.user_text || "";
        meta.openclaw_status = ocTask.status || "";
      }
      const activityHtml = captureActivityHtml(bubble);
      if (activityHtml) meta.activity_html = activityHtml;
      delete meta.pending;
      delete meta.requestId;
      pushed.entry.ts = Date.now();
      if (pushed.session) pushed.session.updatedAt = Date.now();
      persistSessions();
      renderHistory();
      pendingAssistantEntries.delete(msg.request_id);
    } else if (text) {
      const meta = {};
      if (ocTask) {
        meta.openclaw_task_id = ocTask.task_id;
        meta.openclaw_user_text = ocTask.user_text || "";
        meta.openclaw_status = ocTask.status || "";
      }
      const activityHtml = captureActivityHtml(bubble);
      if (activityHtml) meta.activity_html = activityHtml;
      pushMessageToSession("assistant", text, Object.keys(meta).length ? meta : undefined);
    }

    pendingMessages.delete(msg.request_id);
    pendingUserMessages.delete(msg.request_id);
    scrollChat();
  }

  function captureActivityHtml(bubble) {
    if (!bubble) return "";
    const log = bubble.querySelector(".steps.activity-log");
    if (!log) return "";
    if (!log.querySelector(".turn-card") && !log.querySelector(".narration-pill") && !log.querySelector(".route-trail")) {
      return "";
    }
    const clone = log.cloneNode(true);
    clone.classList.remove("is-expanded");
    clone.querySelectorAll(".activity-summary-toggle").forEach((el) => {
      el.textContent = "查看详情";
    });
    const MAX_BYTES = 180_000;
    const html = clone.outerHTML;
    if (html.length > MAX_BYTES) return "";
    return html;
  }

  function rehydrateActivityLog(bubble, html) {
    if (!bubble || !html) return;
    const stepsEl = bubble.querySelector(".steps");
    if (!stepsEl) return;
    const tmp = document.createElement("div");
    tmp.innerHTML = html;
    const newLog = tmp.firstElementChild;
    if (!newLog) return;
    stepsEl.replaceWith(newLog);
    const summary = newLog.querySelector(".activity-summary");
    if (summary) {
      summary.addEventListener("click", () => {
        newLog.classList.toggle("is-expanded");
        const toggle = summary.querySelector(".activity-summary-toggle");
        if (toggle) {
          toggle.textContent = newLog.classList.contains("is-expanded") ? "收起详情" : "查看详情";
        }
      });
    }
  }

  function updateUserBubbleText(requestId, text) {
    if (!text) return;
    const bubble = pendingUserMessages.get(requestId);
    if (bubble) bubble.textContent = text;

    const pushed = pendingUserEntries.get(requestId);
    if (pushed && pushed.entry) {
      pushed.entry.text = text;
      if (pushed.entry.meta && typeof pushed.entry.meta === "object") {
        pushed.entry.meta.voice_transcribed = true;
      }
      const session = pushed.session;
      if (session) {
        session.updatedAt = Date.now();
        if (!session.title || session.title === "新会话" || session.title === "[语音消息]") {
          session.title = text.length > 28 ? text.slice(0, 28) + "…" : text;
        }
      }
      persistSessions();
      renderHistory();
      pendingUserEntries.delete(requestId);
    }
  }

  function thinkingHost(bubble) {
    const log = ensureActivityLog(bubble);
    if (!log) return null;
    return currentTurnBody(log) || getPreludeArea(log);
  }

  function createThinkingSummary(labelText) {
    const summary = document.createElement("summary");
    summary.innerHTML = `<span class="thinking-summary-label"></span><span class="thinking-summary-tools"></span>`;
    summary.querySelector(".thinking-summary-label").textContent = labelText;
    return summary;
  }

  function setThinkingSummaryLabel(details, labelText) {
    if (!details) return;
    const label = details.querySelector("summary .thinking-summary-label");
    if (label) label.textContent = labelText;
  }

  function appendThinkingToBubble(bubble, text) {
    if (!bubble || !text) return;
    const host = thinkingHost(bubble);
    if (!host) return;
    const details = document.createElement("details");
    details.className = "thinking-block";
    details.appendChild(createThinkingSummary("思考过程"));
    const body = document.createElement("div");
    body.className = "thinking-body";
    body.innerHTML = formatAssistantText(text);
    details.appendChild(body);
    host.appendChild(details);
    const card = host.closest && host.closest(".turn-card");
    if (card && card.dataset.tools) {
      updateTurnToolHints(card, ""); // re-apply tool tail using stored names
    }
    scrollChat();
  }

  function appendThinkingDelta(bubble, requestId, chunk) {
    if (!bubble) return;
    const host = thinkingHost(bubble);
    if (!host) return;
    let state = streamingState.get(requestId);
    if (!state) {
      state = { thinkingEl: null, thinkingText: "" };
      streamingState.set(requestId, state);
    }
    if (!state.thinkingEl) {
      const details = document.createElement("details");
      details.className = "thinking-block";
      details.open = true;
      details.appendChild(createThinkingSummary("思考中…"));
      const body = document.createElement("div");
      body.className = "thinking-body";
      details.appendChild(body);
      host.appendChild(details);
      state.thinkingEl = details;
      state.thinkingText = "";
      const card = host.closest && host.closest(".turn-card");
      if (card && card.dataset.tools) updateTurnToolHints(card, "");
    }
    state.thinkingText += chunk;
    const body = state.thinkingEl.querySelector(".thinking-body");
    body.textContent = state.thinkingText;
    scrollChat();
  }

  function appendResponseDelta(bubble, requestId, chunk) {
    if (!bubble) return;
    const el = bubble.querySelector(".response-text");
    if (!el) return;
    let finalEl = el.querySelector(".final-response");
    if (!finalEl) {
      finalEl = document.createElement("div");
      finalEl.className = "final-response";
      el.appendChild(finalEl);
    }
    finalEl.textContent += chunk;
    scrollChat();
  }

  function finalizeStreamingThinking(requestId) {
    const state = streamingState.get(requestId);
    if (state && state.thinkingEl && state.thinkingText) {
      const body = state.thinkingEl.querySelector(".thinking-body");
      if (body) body.innerHTML = formatAssistantText(state.thinkingText);
      state.thinkingEl.open = false;
      setThinkingSummaryLabel(state.thinkingEl, "思考过程");
    }
    if (state) {
      state.thinkingEl = null;
      state.thinkingText = "";
    }
  }

  function finalizeNarration(bubble, requestId, text) {
    if (!bubble || !text) return;
    const el = bubble.querySelector(".response-text");
    if (el) {
      const finalEl = el.querySelector(".final-response");
      if (finalEl) finalEl.textContent = "";
    }
    const log = ensureActivityLog(bubble);
    if (log) addNarrationPill(log, text);
  }

  function appendTextToBubble(bubble, text) {
    const el = bubble.querySelector(".response-text");
    if (!el) return;
    let finalEl = el.querySelector(".final-response");
    if (!finalEl) {
      finalEl = document.createElement("div");
      finalEl.className = "final-response";
      el.appendChild(finalEl);
    }
    finalEl.innerHTML = formatAssistantText(text);
  }

  function ensureActivityLog(bubble) {
    if (!bubble) return null;
    const log = bubble.querySelector(".steps");
    if (!log) return null;
    if (!log.dataset.activityInit) {
      log.dataset.activityInit = "1";
      log.dataset.currentTurn = "0";
      log.dataset.startedAt = String(performance.now());
      log.classList.add("activity-log");
      log.innerHTML = `
        <div class="activity-narrations"></div>
        <div class="activity-timeline">
          <div class="activity-prelude"></div>
          <div class="turn-list"></div>
          <div class="activity-epilogue"></div>
        </div>`;
    }
    return log;
  }

  function resolveLog(containerOrBubble) {
    if (!containerOrBubble) return null;
    if (containerOrBubble.classList && containerOrBubble.classList.contains("steps")) {
      return ensureActivityLog({ querySelector: () => containerOrBubble });
    }
    if (containerOrBubble.classList && containerOrBubble.classList.contains("msg-assistant")) {
      return ensureActivityLog(containerOrBubble);
    }
    const steps = containerOrBubble.closest && containerOrBubble.closest(".steps");
    if (steps) return ensureActivityLog({ querySelector: () => steps });
    return null;
  }

  function getPreludeArea(log) {
    return (log && log.querySelector(".activity-prelude")) || log;
  }
  function getEpilogueArea(log) {
    return (log && log.querySelector(".activity-epilogue")) || log;
  }
  function getTurnList(log) {
    return (log && log.querySelector(".turn-list")) || log;
  }
  function getNarrationsArea(log) {
    return (log && log.querySelector(".activity-narrations")) || log;
  }

  function ensureTurnCard(log, turnIdx) {
    if (!log || !turnIdx) return null;
    const list = getTurnList(log);
    let card = list.querySelector(`.turn-card[data-turn="${turnIdx}"]`);
    if (!card) {
      card = document.createElement("div");
      card.className = "turn-card";
      card.dataset.turn = String(turnIdx);
      card.dataset.tools = "";
      card.innerHTML = `
        <div class="turn-head">
          <span class="turn-index">第 ${turnIdx} 轮</span>
          <span class="turn-tools" aria-hidden="true"></span>
        </div>
        <div class="turn-body"></div>`;
      list.appendChild(card);
    }
    const prev = parseInt(log.dataset.currentTurn || "0", 10);
    if (turnIdx > prev) log.dataset.currentTurn = String(turnIdx);
    return card;
  }

  function updateTurnToolHints(card, toolName) {
    if (!card) return;
    const existing = (card.dataset.tools || "").split("|").filter(Boolean);
    if (toolName && !existing.includes(toolName)) existing.push(toolName);
    card.dataset.tools = existing.join("|");
    if (!existing.length) return;

    const toolsEl = card.querySelector(".turn-tools");
    if (toolsEl) {
      toolsEl.innerHTML = existing
        .map((n) => `<span class="turn-tool-chip">${esc(n)}</span>`)
        .join("");
    }

    const summaries = card.querySelectorAll(".thinking-block > summary");
    summaries.forEach((summary) => {
      let tail = summary.querySelector(".thinking-summary-tools");
      if (!tail) {
        tail = document.createElement("span");
        tail.className = "thinking-summary-tools";
        summary.appendChild(tail);
      }
      tail.textContent = ` · 调用 ${existing.join(", ")}`;
    });
  }

  function bumpTurn(log) {
    if (!log) return null;
    const next = parseInt(log.dataset.currentTurn || "0", 10) + 1;
    return ensureTurnCard(log, next);
  }

  function currentTurnBody(log) {
    if (!log) return null;
    const idx = parseInt(log.dataset.currentTurn || "0", 10);
    if (!idx) return null;
    return log.querySelector(`.turn-card[data-turn="${idx}"] .turn-body`);
  }

  function addToolChip(log, turnIdx, toolIdx, name, args) {
    if (!log) return;
    const card = ensureTurnCard(log, turnIdx || parseInt(log.dataset.currentTurn || "1", 10) || 1);
    if (!card) return;
    const body = card.querySelector(".turn-body");
    const chip = document.createElement("div");
    chip.className = "tool-chip is-running";
    if (toolIdx !== undefined && toolIdx !== null) chip.dataset.toolIdx = String(toolIdx);
    chip.dataset.toolName = name || "";
    const argStr = formatToolArguments(args);
    chip.innerHTML = `
      <span class="tool-chip-icon"><div class="spinner"></div></span>
      <span class="tool-chip-name">${esc(name || "")}</span>
      <span class="tool-chip-args" title="${esc(argStr)}">${esc(argStr)}</span>`;
    body.appendChild(chip);
    updateTurnToolHints(card, name || "");
    scrollChat();
  }

  function finishToolChip(log, turnIdx, toolIdx, status, summary) {
    if (!log) return;
    const card = log.querySelector(`.turn-card[data-turn="${turnIdx}"]`);
    if (!card) return;
    let chip = null;
    if (toolIdx !== undefined && toolIdx !== null) {
      chip = card.querySelector(`.tool-chip[data-tool-idx="${toolIdx}"]`);
    }
    if (!chip) {
      const candidates = card.querySelectorAll(".tool-chip.is-running");
      chip = candidates.length ? candidates[candidates.length - 1] : null;
    }
    if (!chip) return;
    chip.classList.remove("is-running");
    const ok = status === "ok";
    chip.classList.add(ok ? "is-ok" : "is-err");
    const icon = chip.querySelector(".tool-chip-icon");
    if (icon) icon.textContent = ok ? "✓" : "✕";
    const result = extractToolResultText(summary, status);
    if (result) {
      const rEl = document.createElement("span");
      rEl.className = "tool-chip-result";
      rEl.title = result;
      rEl.textContent = result;
      chip.appendChild(rEl);
    }
    scrollChat();
  }

  function extractToolResultText(summary, status) {
    if (!summary) return status === "ok" ? "" : status || "error";
    let out = summary;
    const arrowIdx = out.indexOf("->");
    if (arrowIdx !== -1) out = out.slice(arrowIdx + 2).trim();
    out = out.replace(/\s+/g, " ").trim();
    if (out.length > 160) out = out.slice(0, 160) + "…";
    return out;
  }

  function setRouteTrail(log, nodes) {
    if (!log || !Array.isArray(nodes) || !nodes.length) return;
    let trail = log.querySelector(":scope > .route-trail");
    if (!trail) {
      trail = document.createElement("div");
      trail.className = "route-trail";
      log.insertBefore(trail, log.firstChild);
    }
    trail.innerHTML = nodes
      .map((n, i) => {
        const sep = i ? '<span class="route-arrow">›</span>' : "";
        const label = esc(n.label || "");
        const state = esc(n.state || "pending");
        return `${sep}<span class="route-node is-${state}">${label}</span>`;
      })
      .join("");
  }

  function buildResolvedTrail(data) {
    const source = data.source || "";
    const type = data.intent_type || "";
    if (type === "skill" && source === "keyword") {
      const hit = data.matched_keyword ? `"${data.matched_keyword}" · ${data.skill_id || ""}` : data.skill_id || "技能";
      return [
        { label: "关键词", state: "done" },
        { label: hit, state: "done" },
      ];
    }
    if (type === "skill") {
      return [
        { label: "关键词", state: "miss" },
        { label: "LLM Agent", state: "done" },
        { label: `技能：${data.skill_id || ""}`.trim(), state: "done" },
      ];
    }
    if (type === "agent") {
      return [
        { label: "关键词", state: "miss" },
        { label: "LLM Agent", state: "done" },
      ];
    }
    if (type === "openclaw") {
      return [
        { label: "关键词", state: "miss" },
        { label: "OpenClaw", state: "done" },
      ];
    }
    if (type === "chat") {
      return [
        { label: "关键词", state: "miss" },
        { label: "闲聊回复", state: "done" },
      ];
    }
    return [{ label: type || "已完成", state: "done" }];
  }

  function addNarrationPill(log, text) {
    if (!log || !text) return;
    const area = getNarrationsArea(log);
    const pill = document.createElement("div");
    pill.className = "narration-pill";
    pill.innerHTML = `<span class="narration-icon" aria-hidden="true">💬</span><span class="narration-text">${esc(text)}</span>`;
    area.appendChild(pill);
    scrollChat();
  }

  function finalizeActivity(log) {
    if (!log || log.dataset.finalized === "1") return;
    log.dataset.finalized = "1";
    const turnCount = log.querySelectorAll(".turn-card").length;
    const chips = log.querySelectorAll(".tool-chip");
    const toolTotal = chips.length;
    const toolFail = Array.from(chips).filter((c) => c.classList.contains("is-err")).length;
    const started = parseFloat(log.dataset.startedAt || "0");
    const elapsed = started ? (performance.now() - started) / 1000 : 0;

    const bits = [`<span class="activity-summary-status">✓</span>`];
    if (turnCount) {
      bits.push(`<span class="activity-summary-seg"><span class="seg-ico">🧠</span>${turnCount} 轮推理</span>`);
    }
    if (toolTotal) {
      const tail = toolFail ? `（${toolFail} 失败）` : "";
      bits.push(`<span class="activity-summary-seg"><span class="seg-ico">🔧</span>${toolTotal} 次工具${tail}</span>`);
    }
    if (elapsed >= 0.2) {
      bits.push(`<span class="activity-summary-seg"><span class="seg-ico">⏱</span>${elapsed.toFixed(1)}s</span>`);
    }
    if (bits.length === 1) bits.push(`<span class="activity-summary-seg">完成</span>`);
    bits.push(`<span class="activity-summary-toggle">查看详情</span>`);

    const summary = document.createElement("button");
    summary.type = "button";
    summary.className = "activity-summary";
    summary.innerHTML = bits.join("");
    summary.addEventListener("click", () => {
      log.classList.toggle("is-expanded");
      const toggle = summary.querySelector(".activity-summary-toggle");
      if (toggle) toggle.textContent = log.classList.contains("is-expanded") ? "收起详情" : "查看详情";
    });

    const narr = log.querySelector(".activity-narrations");
    if (narr && narr.nextSibling) log.insertBefore(summary, narr.nextSibling);
    else log.appendChild(summary);
    log.classList.add("is-finalized");
  }

  function addStep(container, text, state) {
    const row = document.createElement("div");
    row.className = `step-row ${state}`;
    let icon = "";
    if (state === "done") icon = "✓";
    else if (state === "active") icon = '<div class="spinner"></div>';
    else if (state === "error") icon = "✕";
    row.innerHTML = `<span class="step-icon">${icon}</span><span>${esc(text)}</span>`;
    container.appendChild(row);
    scrollChat();
  }

  function markAllActiveDone(container) {
    container.querySelectorAll(".step-row.active").forEach((el) => {
      el.classList.remove("active");
      el.classList.add("done");
      const icon = el.querySelector(".step-icon");
      if (icon) icon.textContent = "✓";
    });
  }

  function markLastDone(container) {
    const actives = container.querySelectorAll(".step-row.active");
    const active = actives.length ? actives[actives.length - 1] : null;
    if (!active) return;
    active.classList.remove("active");
    active.classList.add("done");
    const icon = active.querySelector(".step-icon");
    if (icon) icon.textContent = "✓";
  }

  function updateActiveStep(container, text) {
    const actives = container.querySelectorAll(".step-row.active");
    const active = actives.length ? actives[actives.length - 1] : null;
    if (!active) return;
    const spans = active.querySelectorAll("span");
    if (spans[1]) spans[1].textContent = text;
  }

  /* ---- Button listeners ---- */

  btnStop.addEventListener("click", () => {
    stopAllTts();
    send({ type: "stop_loop", request_id: activeAgentRequestId || "" });
    btnStop.classList.add("hidden");
    activeAgentRequestId = null;
  });

  btnEstop.addEventListener("click", () => {
    if (confirm("确认发送急停命令？")) {
      send({ type: "estop" });
      addSystemMessage("已发送 E-STOP 命令");
    }
  });

  eachRecordButton((btn) => {
    btn.addEventListener("click", () => {
      if (isMotionRecording) stopMotionRecording();
      else if (hasPendingMotionRecording) openRecordNameDialog();
      else openRecordStartDialog();
    });
  });

  if (recordStartForm) {
    recordStartForm.addEventListener("submit", (e) => {
      e.preventDefault();
      if (isMotionRecording) stopMotionRecording();
      else startMotionRecording();
    });
  }

  if (btnRecordStartCancel) {
    btnRecordStartCancel.addEventListener("click", () => {
      stopRecordTimer();
      closeRecordStartDialog();
      resetRecordStartDialogUI();
    });
  }

  if (recordNameForm) {
    recordNameForm.addEventListener("submit", (e) => {
      e.preventDefault();
      const name = (recordNameInput.value || "").trim();
      if (!name) {
        recordNameError.textContent = "请输入动作名称";
        return;
      }
      if (!/^[\w-]+$/.test(name)) {
        recordNameError.textContent = "名称仅支持字母、数字、下划线、短横线";
        return;
      }
      recordNameError.textContent = "";
      saveMotionRecording(name, pendingOverwriteSave);
    });
  }

  if (btnRecordDiscard) {
    btnRecordDiscard.addEventListener("click", () => discardMotionRecording());
  }

  if (btnRecordRerecord) {
    btnRecordRerecord.addEventListener("click", () => {
      discardMotionRecording();
      setTimeout(() => openRecordStartDialog(), 80);
    });
  }

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    if (recordStartDialog && recordStartDialog.open) {
      e.preventDefault();
      if (btnRecordStartConfirm) btnRecordStartConfirm.click();
      return;
    }
    if (recordNameDialog && recordNameDialog.open) return;
    const tag = (e.target && e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select" || e.isComposing) return;
    if (!btnRecordMotionPanel) return;
    if (currentView !== "skills") return;
    e.preventDefault();
    btnRecordMotionPanel.click();
  });

  if (btnClearEvents) {
    btnClearEvents.addEventListener("click", () => {
      if (eventLog) eventLog.innerHTML = "";
    });
  }

  if (btnRefreshOpenclaw) {
    btnRefreshOpenclaw.addEventListener("click", () => send({ type: "openclaw_tasks" }));
  }

  /* ---- Navigation + hints + history ---- */

  navButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const view = btn.dataset.view;
      if (!view) return;
      if (btn.dataset.action === "new-chat") startNewSession();
      else showView(view);
    });
  });

  hintChips.forEach((chip) => {
    chip.addEventListener("click", () => {
      const prompt = chip.dataset.prompt || chip.textContent || "";
      chatInput.value = prompt;
      chatInput.focus();
    });
  });

  if (historySearch) {
    historySearch.addEventListener("input", () => renderHistory());
  }

  if (btnHistoryClear) {
    btnHistoryClear.addEventListener("click", () => clearAllHistory());
  }

  const savedPlaybackMode = localStorage.getItem(PLAYBACK_MODE_KEY) || "cleaned";
  setPlaybackMode(savedPlaybackMode);
  playbackModeButtons.forEach((btn) => {
    btn.addEventListener("click", () => setPlaybackMode(btn.dataset.playbackMode || "cleaned"));
  });

  function addSystemMessage(text) {
    clearEmptyState();
    const note = document.createElement("div");
    note.className = "system-note";
    note.textContent = text;
    chatMessages.appendChild(note);
    scrollChat();
  }

  function nextId() {
    reqCounter += 1;
    return `r${reqCounter}_${Date.now().toString(36)}`;
  }

  function getRecordingExpression(name) {
    return RECORDING_EXPRESSIONS[name] || "smiley";
  }

  function formatTime(date) {
    const d = date || new Date();
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  function esc(s) {
    const div = document.createElement("div");
    div.textContent = s == null ? "" : String(s);
    return div.innerHTML;
  }

  function formatAssistantText(text) {
    const lines = String(text || "").replace(/\r\n/g, "\n").split("\n");
    const blocks = [];
    let paragraph = [];
    let listItems = [];

    function flushParagraph() {
      if (!paragraph.length) return;
      blocks.push(`<p>${formatInlineMarkdown(paragraph.join("<br>"))}</p>`);
      paragraph = [];
    }

    function flushList() {
      if (!listItems.length) return;
      blocks.push(`<ul>${listItems.map((item) => `<li>${formatInlineMarkdown(item)}</li>`).join("")}</ul>`);
      listItems = [];
    }

    lines.forEach((line) => {
      const trimmed = line.trim();
      if (!trimmed) {
        flushParagraph();
        flushList();
        return;
      }
      const bullet = trimmed.match(/^[-*]\s+(.*)$/);
      if (bullet) {
        flushParagraph();
        listItems.push(esc(bullet[1]));
        return;
      }
      flushList();
      paragraph.push(esc(trimmed));
    });

    flushParagraph();
    flushList();

    return blocks.join("") || `<p>${formatInlineMarkdown(esc(String(text || "")))}</p>`;
  }

  function formatInlineMarkdown(text) {
    return text.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  }

  function scrollChat() {
    requestAnimationFrame(() => {
      chatMessages.scrollTop = chatMessages.scrollHeight;
    });
  }

  /* ---- Voice Recording ---- */
  let mediaRecorder = null;
  let audioChunks = [];
  let audioContext = null;
  let analyserNode = null;
  let sourceNode = null;
  let waveAnimId = null;
  let micStream = null;
  let isVoiceMode = false;
  let voiceCancelled = false;
  let selectedMicId = preferredMicId || "";
  const micSelect = document.getElementById("mic-select");

  async function enumerateMics() {
    try {
      await navigator.mediaDevices.getUserMedia({ audio: true }).then((s) => s.getTracks().forEach((t) => t.stop()));
      const devices = await navigator.mediaDevices.enumerateDevices();
      const mics = devices.filter((d) => d.kind === "audioinput");
      if (micSelect) {
        micSelect.innerHTML = '<option value="">默认麦克风</option>';
        mics.forEach((d) => {
          const opt = document.createElement("option");
          opt.value = d.deviceId;
          opt.textContent = d.label || `麦克风 ${d.deviceId.slice(0, 8)}`;
          micSelect.appendChild(opt);
        });
        if (selectedMicId && mics.some((m) => m.deviceId === selectedMicId)) {
          micSelect.value = selectedMicId;
        }
      }
      micDevicesCache = mics;
      if (isMicPopoverOpen()) renderMicPopover();
      console.log("[voice] found", mics.length, "mic devices:", mics.map((d) => d.label));
    } catch (err) {
      console.warn("[voice] cannot enumerate mics:", err);
    }
  }

  if (micSelect) {
    micSelect.addEventListener("change", () => {
      selectedMicId = micSelect.value;
      preferredMicId = selectedMicId;
      try {
        if (selectedMicId) localStorage.setItem(MIC_DEVICE_KEY, selectedMicId);
        else localStorage.removeItem(MIC_DEVICE_KEY);
      } catch (_) { /* ignore */ }
      if (isMicPopoverOpen()) renderMicPopover();
      console.log("[voice] selected mic:", selectedMicId || "(default)");
    });
  }

  window.addEventListener("lampgo:mic-selected", (ev) => {
    const id = (ev.detail && ev.detail.deviceId) || "";
    selectedMicId = id;
    if (micSelect) micSelect.value = id;
    console.log("[voice] selected mic (via popover):", id || "(default)");
  });

  enumerateMics();

  const micGroup = document.querySelector(".mic-group");

  btnMic.addEventListener("click", () => {
    stopAllTts();
    void unlockTtsPlayback();
    if (!isVoiceMode) startVoiceMode();
  });

  btnVoiceCancel.addEventListener("click", () => {
    if (!isVoiceMode) return;
    voiceCancelled = true;
    if (mediaRecorder && mediaRecorder.state !== "inactive") mediaRecorder.stop();
    stopVoiceMode();
  });

  async function startVoiceMode() {
    const constraints = {
      audio: {
        autoGainControl: true,
        echoCancellation: false,
        noiseSuppression: false,
      },
    };
    if (selectedMicId) constraints.audio.deviceId = { exact: selectedMicId };

    try {
      micStream = await navigator.mediaDevices.getUserMedia(constraints);
    } catch (err) {
      console.error("mic access denied", err);
      addSystemMessage("无法访问麦克风，请检查浏览器权限");
      return;
    }

    isVoiceMode = true;
    voiceCancelled = false;
    micGroup.style.display = "none";
    chatInput.style.display = "none";
    voiceWave.classList.remove("hidden");
    btnVoiceCancel.classList.remove("hidden");

    audioContext = new (window.AudioContext || window.webkitAudioContext)();
    sourceNode = audioContext.createMediaStreamSource(micStream);
    analyserNode = audioContext.createAnalyser();
    analyserNode.fftSize = 1024;
    analyserNode.smoothingTimeConstant = 0.88;
    sourceNode.connect(analyserNode);

    const tracks = micStream.getAudioTracks();
    const settings = tracks[0]?.getSettings() || {};
    console.log("[voice] mic started:", tracks[0]?.label, "sampleRate:", settings.sampleRate, "state:", audioContext.state);

    requestAnimationFrame(() => drawWaveform());

    audioChunks = [];
    mediaRecorder = new MediaRecorder(micStream, { mimeType: pickMimeType() });
    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) audioChunks.push(e.data);
    };
    mediaRecorder.onstop = () => finishRecording();
    mediaRecorder.start();
  }

  function stopVoiceMode() {
    isVoiceMode = false;
    micGroup.style.display = "";
    voiceWave.classList.add("hidden");
    btnVoiceCancel.classList.add("hidden");
    chatInput.style.display = "";

    if (waveAnimId) {
      cancelAnimationFrame(waveAnimId);
      waveAnimId = null;
    }
    if (micStream) {
      micStream.getTracks().forEach((t) => t.stop());
      micStream = null;
    }
    if (audioContext) {
      audioContext.close();
      audioContext = null;
    }
  }

  async function finishRecording() {
    if (voiceCancelled) {
      audioChunks = [];
      console.log("[voice] recording cancelled by user");
      return;
    }
    if (!audioChunks.length) {
      console.warn("[voice] no audio chunks captured");
      return;
    }

    const blob = new Blob(audioChunks, { type: audioChunks[0].type || "audio/webm" });
    audioChunks = [];
    console.log("[voice] raw blob:", blob.size, "bytes, type:", blob.type);

    const wavBlob = await blobToWav(blob);
    const b64 = await blobToBase64(wavBlob);

    const rms = await measureWavRms(wavBlob);
    console.log("[voice] WAV:", wavBlob.size, "bytes, RMS:", rms.toFixed(1), rms < 10 ? "⚠️ VERY QUIET" : "✓ OK");

    if (rms < 1) {
      addSystemMessage("录音似乎是静音，请检查浏览器麦克风权限和设备选择");
      return;
    }

    clearEmptyState();
    const requestId = nextId();
    addUserBubble("[语音消息]", requestId);
    const pushed = pushMessageToSession("user", "[语音消息]", { voice: true });
    if (pushed) pendingUserEntries.set(requestId, pushed);
    addAssistantBubble(requestId);
    send({ type: "audio", audio_data: b64, request_id: requestId });
  }

  async function measureWavRms(wavBlob) {
    try {
      const buf = await wavBlob.arrayBuffer();
      const view = new DataView(buf);
      const pcmStart = 44;
      let sumSq = 0;
      let count = 0;
      for (let i = pcmStart; i < buf.byteLength - 1; i += 2) {
        const sample = view.getInt16(i, true);
        sumSq += sample * sample;
        count++;
      }
      return count > 0 ? Math.sqrt(sumSq / count) : 0;
    } catch {
      return -1;
    }
  }

  function drawWaveform() {
    if (!analyserNode || !voiceCanvas) return;
    const ctx = voiceCanvas.getContext("2d");
    const bufLen = analyserNode.frequencyBinCount;
    const dataArray = new Uint8Array(bufLen);
    const smoothed = new Float32Array(bufLen);
    const ysBuf = new Float32Array(bufLen);
    const dpr = Math.min(window.devicePixelRatio || 1, 3);
    const AMP = 6;
    const SMOOTH = 0.12;
    const MIN_FRAME_MS = 55;

    let lastDraw = 0;

    function draw(ts) {
      if (!analyserNode) return;
      waveAnimId = requestAnimationFrame(draw);
      const now = ts || performance.now();
      if (now - lastDraw < MIN_FRAME_MS) return;
      lastDraw = now;

      const rect = voiceCanvas.getBoundingClientRect();
      let cw = Math.round(rect.width);
      let ch = Math.round(rect.height);
      cw = Math.min(Math.max(cw, 1), 4096);
      ch = Math.min(Math.max(ch, 1), 512);
      if (cw < 2 || ch < 2) return;

      const bw = Math.floor(cw * dpr);
      const bh = Math.floor(ch * dpr);
      if (voiceCanvas.width !== bw || voiceCanvas.height !== bh) {
        voiceCanvas.width = bw;
        voiceCanvas.height = bh;
      }

      const W = voiceCanvas.width;
      const H = voiceCanvas.height;
      const mid = H / 2;
      const half = (H / 2) * 0.92;

      analyserNode.getByteTimeDomainData(dataArray);

      for (let i = 0; i < bufLen; i++) {
        const dev = (dataArray[i] - 128) / 128;
        smoothed[i] += SMOOTH * (dev - smoothed[i]);
      }

      ctx.clearRect(0, 0, W, H);
      const sliceWidth = W / bufLen;
      for (let i = 0; i < bufLen; i++) {
        const y = mid - smoothed[i] * half * AMP;
        ysBuf[i] = Math.max(2, Math.min(H - 2, y));
      }

      ctx.fillStyle = "rgba(221, 147, 136, 0.2)";
      ctx.beginPath();
      ctx.moveTo(0, mid);
      for (let i = 0; i < bufLen; i++) ctx.lineTo(i * sliceWidth, ysBuf[i]);
      ctx.lineTo(W, mid);
      ctx.closePath();
      ctx.fill();

      ctx.lineWidth = Math.max(1.5, 2 * dpr);
      ctx.strokeStyle = "#c97a6e";
      ctx.lineJoin = "round";
      ctx.beginPath();
      for (let i = 0; i < bufLen; i++) {
        const px = i * sliceWidth;
        if (i === 0) ctx.moveTo(px, ysBuf[i]);
        else ctx.lineTo(px, ysBuf[i]);
      }
      ctx.stroke();
    }
    waveAnimId = requestAnimationFrame(draw);
  }

  function pickMimeType() {
    const types = ["audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus", "audio/mp4"];
    for (const t of types) {
      if (MediaRecorder.isTypeSupported(t)) return t;
    }
    return "";
  }

  async function blobToWav(blob) {
    const arrayBuf = await blob.arrayBuffer();
    const actx = new (window.OfflineAudioContext || window.webkitOfflineAudioContext)(1, 1, 16000);
    let audioBuf;
    try {
      audioBuf = await actx.decodeAudioData(arrayBuf);
    } catch {
      return blob;
    }

    const sampleRate = 16000;
    const offCtx = new OfflineAudioContext(1, Math.ceil(audioBuf.duration * sampleRate), sampleRate);
    const src = offCtx.createBufferSource();
    src.buffer = audioBuf;
    src.connect(offCtx.destination);
    src.start();
    const rendered = await offCtx.startRendering();
    const samples = rendered.getChannelData(0);

    const pcm = new Int16Array(samples.length);
    for (let i = 0; i < samples.length; i++) {
      const s = Math.max(-1, Math.min(1, samples[i]));
      pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }

    const wavBuf = new ArrayBuffer(44 + pcm.length * 2);
    const view = new DataView(wavBuf);
    function writeStr(offset, str) {
      for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i));
    }
    writeStr(0, "RIFF");
    view.setUint32(4, 36 + pcm.length * 2, true);
    writeStr(8, "WAVE");
    writeStr(12, "fmt ");
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, 1, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * 2, true);
    view.setUint16(32, 2, true);
    view.setUint16(34, 16, true);
    writeStr(36, "data");
    view.setUint32(40, pcm.length * 2, true);
    const pcmBytes = new Uint8Array(wavBuf, 44);
    pcmBytes.set(new Uint8Array(pcm.buffer));

    return new Blob([wavBuf], { type: "audio/wav" });
  }

  function blobToBase64(blob) {
    return new Promise((resolve) => {
      const reader = new FileReader();
      reader.onloadend = () => {
        const dataUrl = reader.result;
        resolve(dataUrl.split(",")[1]);
      };
      reader.readAsDataURL(blob);
    });
  }

  /* ---- TTS Audio Playback ---- */
  let ttsQueue = [];
  let ttsPlaying = false;
  let ttsAudioContext = null;
  let ttsCurrentSource = null;
  let ttsCurrentAudioEl = null;

  async function unlockTtsPlayback() {
    const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextCtor) return null;
    if (!ttsAudioContext || ttsAudioContext.state === "closed") {
      ttsAudioContext = new AudioContextCtor();
    }
    if (ttsAudioContext.state === "suspended") {
      try {
        await ttsAudioContext.resume();
      } catch (err) {
        console.warn("[tts] resume failed:", err);
      }
    }
    return ttsAudioContext;
  }

  function handleTtsAudio(audioB64, format) {
    const mimeMap = { mp3: "audio/mpeg", wav: "audio/wav", pcm16: "audio/wav", opus: "audio/ogg" };
    const mime = mimeMap[format] || "audio/mpeg";
    const bytes = Uint8Array.from(atob(audioB64), (c) => c.charCodeAt(0));
    ttsQueue.push({ bytes, mime, format });
    if (!ttsPlaying) void playNextTts();
  }

  async function playBlobWithAudioElement(blob) {
    const url = URL.createObjectURL(blob);
    try {
      await new Promise((resolve, reject) => {
        const audio = new Audio(url);
        ttsCurrentAudioEl = audio;
        audio.onended = () => {
          ttsCurrentAudioEl = null;
          resolve();
        };
        audio.onerror = () => {
          ttsCurrentAudioEl = null;
          reject(new Error("audio element playback failed"));
        };
        const playPromise = audio.play();
        if (playPromise && typeof playPromise.then === "function") {
          playPromise.catch(reject);
        }
      });
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  async function playTtsChunk(chunk) {
    const blob = new Blob([chunk.bytes], { type: chunk.mime });
    const ctx = await unlockTtsPlayback();
    if (ctx) {
      try {
        const buffer = await blob.arrayBuffer();
        const audioBuffer = await ctx.decodeAudioData(buffer.slice(0));
        await new Promise((resolve) => {
          const source = ctx.createBufferSource();
          source.buffer = audioBuffer;
          source.connect(ctx.destination);
          source.onended = () => {
            ttsCurrentSource = null;
            resolve();
          };
          ttsCurrentSource = source;
          source.start(0);
        });
        return;
      } catch (err) {
        console.warn("[tts] AudioContext playback failed, fallback to <audio>:", err);
      }
    }
    await playBlobWithAudioElement(blob);
  }

  async function playNextTts() {
    if (ttsPlaying) return;
    ttsPlaying = true;
    try {
      while (ttsQueue.length) {
        const chunk = ttsQueue.shift();
        try {
          await playTtsChunk(chunk);
        } catch (err) {
          console.warn("[tts] playback failed:", err);
        }
      }
    } finally {
      ttsPlaying = false;
    }
  }

  function stopAllTts() {
    ttsQueue.length = 0;
    if (ttsCurrentSource) {
      try { ttsCurrentSource.stop(); } catch (_) {}
      ttsCurrentSource = null;
    }
    if (ttsCurrentAudioEl) {
      try {
        ttsCurrentAudioEl.pause();
        ttsCurrentAudioEl.src = "";
      } catch (_) {}
      ttsCurrentAudioEl = null;
    }
    ttsPlaying = false;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "stop_tts" }));
    }
  }

  /* ---- Boot ---- */

  loadSessions();
  renderHistory();
  const bootSession = getActiveSession();
  if (bootSession && bootSession.messages.length) {
    loadSession(bootSession.id);
  }
  updateRecordButtonState();
  connect();
})();
