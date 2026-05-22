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
  const userSkillGrid = document.getElementById("user-skill-grid");
  const userSkillEmpty = document.getElementById("user-skill-empty");
  const userSkillCountEl = document.getElementById("user-skill-count");
  const userSkillSearchEl = document.getElementById("user-skill-search");
  const btnUserSkillsReload = document.getElementById("btn-user-skills-reload");
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
  const esp32VolumeControl = document.getElementById("esp32-volume-control");
  const esp32VolumeSlider = document.getElementById("esp32-volume-slider");
  const esp32VolumeValue = document.getElementById("esp32-volume-value");
  const btnRefreshOpenclaw = document.getElementById("btn-refresh-openclaw");
  const btnOpenclawHealth = document.getElementById("btn-openclaw-health-details");
  const openclawHealthCard = document.getElementById("openclaw-health-card");
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
  const recordDescriptionInput = document.getElementById("record-description-input");
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
  const sidebar = document.querySelector(".sidebar");
  const sidebarResizer = document.getElementById("sidebar-resizer");
  const hintChips = Array.from(document.querySelectorAll(".hint-chip"));
  const historyList = document.getElementById("history-list");
  const historySearch = document.getElementById("history-search");
  const btnHistoryClear = document.getElementById("btn-history-clear");

  const EXPRESSION_LABELS_CN = Object.freeze({
    off: "熄灭",
    red: "红色逐圈",
    green: "绿色逐圈",
    blue: "蓝色逐圈",
    white: "白色逐圈",
    theater: "剧场追逐",
    theaterred: "红色剧场",
    theatergreen: "绿色剧场",
    theaterblue: "蓝色剧场",
    rainbow: "彩虹渐变",
    rainbowchase: "彩虹追逐",
    smiley: "开心",
    sad: "伤心",
    left: "左箭头",
    right: "右箭头",
    check: "对号",
    cross: "叉号",
    music: "音符跳动",
    blush: "害羞",
    angry: "生气",
    surprised: "惊讶",
    exclaim: "感叹号",
    question: "问号",
    star: "星星",
    up: "上箭头",
    down: "下箭头",
    sleep: "睡觉",
    thinking: "思考",
    heart: "心动",
    helpless: "无奈",
    cool: "耍酷",
    focused: "专注",
    wink: "眨眼",
  });

  const RECORDING_LABELS_CN = Object.freeze({
    Stretch: "伸展",
    bowing_head: "低头鞠躬",
    dance1: "舞蹈一",
    dance2: "舞蹈二",
    deep_thinking: "深度思考",
    excited: "兴奋",
    headshake1: "摇头",
    lie_flat: "趴平",
    look_ahead: "向前看",
    look_around: "环顾四周",
    nod: "点头",
    peep: "偷瞄",
    raise_head: "抬头",
    shy: "害羞",
    sneeze: "打喷嚏",
    stand: "站立",
    suqat_down: "蹲下",
    thinking: "思考",
    turn_back: "回头",
    upset: "沮丧",
    wake_up: "苏醒",
    wave: "挥手",
  });

  const SKILL_LABELS_CN = Object.freeze({
    nod: { title: "点头", description: "上下点头，表达同意。" },
    headshake: { title: "摇头", description: "左右摇头，表达不同意。" },
    look_at: { title: "注视", description: "朝指定方向看过去。" },
    idle_sway: { title: "随机摆动", description: "轻微随机摆动，呈现呼吸般的灵动感。" },
    move_to: { title: "移动到目标", description: "以平滑的梯形插值移动到目标关节位置。" },
    return_safe: { title: "回到安全位", description: "平滑回到固定的待机安全姿态。" },
    presence_react: { title: "人来反应", description: "检测到人时转向并展示问候表情。" },
    face_follow: { title: "人脸跟随", description: "持续调整偏航与俯仰，跟踪人脸。" },
    teleop_mouse: { title: "鼠标遥操作", description: "用手臂当作鼠标控制光标。" },
    teleop_gamepad: { title: "手柄遥操作", description: "将关节映射为按键，当作游戏手柄。" },
    set_expression: { title: "设置表情", description: "切换 LED 灯光表情（例如 笑脸、爱心、生气）。" },
  });

  const expressionMetaByName = new Map();

  function expressionMeta(name) {
    return expressionMetaByName.get(String(name || ""));
  }

  function expressionLabel(name) {
    const meta = expressionMeta(name);
    return (meta && meta.label) || EXPRESSION_LABELS_CN[name] || name;
  }

  function normalizeExpressionEntries(entries) {
    const names = [];
    entries.forEach((entry) => {
      if (typeof entry === "string") {
        const name = entry.trim();
        if (!name) return;
        if (!expressionMetaByName.has(name)) {
          expressionMetaByName.set(name, {
            name,
            mode: null,
            label: EXPRESSION_LABELS_CN[name] || name,
            animated: false,
          });
        }
        names.push(name);
        return;
      }
      if (!entry || typeof entry !== "object") return;
      const name = String(entry.name || "").trim();
      if (!name) return;
      const modeRaw = Number(entry.mode);
      const mode = Number.isFinite(modeRaw) ? modeRaw : null;
      expressionMetaByName.set(name, {
        name,
        mode,
        label: String(entry.label || EXPRESSION_LABELS_CN[name] || name),
        animated: !!entry.animated,
      });
      names.push(name);
    });
    return names;
  }

  function recordingLabel(name) {
    return RECORDING_LABELS_CN[name] || name;
  }

  function skillLabel(skill) {
    const entry = SKILL_LABELS_CN[skill && skill.skill_id];
    return {
      title: (entry && entry.title) || (skill && skill.skill_id) || "",
      description: (entry && entry.description) || (skill && skill.description) || "",
    };
  }

  const RECORDING_EXPRESSIONS = Object.freeze({
    Stretch: "smiley",
    bowing_head: "smiley",
    dance1: "music",
    dance2: "music",
    deep_thinking: "focused",
    excited: "smiley",
    headshake1: "cross",
    lie_flat: "sleep",
    look_ahead: "focused",
    look_around: "question",
    nod: "check",
    peep: "question",
    raise_head: "surprised",
    shy: "blush",
    sneeze: "exclaim",
    stand: "focused",
    suqat_down: "helpless",
    thinking: "thinking",
    turn_back: "right",
    upset: "sad",
    wake_up: "surprised",
    wave: "smiley",
  });

  const PLAYBACK_MODE_KEY = "lampgo.playbackMode";
  const SIDEBAR_WIDTH_KEY = "lampgo.sidebarWidth";
  const SESSION_STORAGE_KEY = "lampgo.sessions";
  const ACTIVE_SESSION_KEY = "lampgo.activeSession";
  const OPENCLAW_TASK_SESSION_KEY = "lampgo.openclawTaskSessions";
  const OPENCLAW_FOLLOWUP_KEY = "lampgo.openclawFollowups";
  const ESP32_VOLUME_KEY = "lampgo.esp32SpeakerVolume";
  const MAX_SESSIONS = 40;
  const DEFAULT_SIDEBAR_WIDTH = 232;
  const MIN_SIDEBAR_WIDTH = 200;
  const MAX_SIDEBAR_WIDTH = 420;
  const SIDEBAR_COLLAPSE_BREAKPOINT = 960;
  const PLAYBACK_MODES = new Set(["raw", "cleaned", "expressive"]);
  const PLAYBACK_MODE_LABELS_CN = Object.freeze({
    raw: "原始",
    cleaned: "平滑",
    expressive: "表现力",
  });
  let sidebarResizeState = null;
  let esp32VolumeTimer = null;
  let esp32VolumePending = false;
  let esp32VolumeInitialSyncDone = false;

  function clampEsp32Volume(value) {
    const n = Number(value);
    if (!Number.isFinite(n)) return 70;
    return Math.max(0, Math.min(100, Math.round(n)));
  }

  function setEsp32VolumeUi(value, { persist = false } = {}) {
    const pct = clampEsp32Volume(value);
    if (esp32VolumeSlider) esp32VolumeSlider.value = String(pct);
    if (esp32VolumeValue) esp32VolumeValue.textContent = `${pct}%`;
    if (persist) {
      try { localStorage.setItem(ESP32_VOLUME_KEY, String(pct)); } catch (_) { /* ignore */ }
    }
    return pct;
  }

  async function syncEsp32Volume(value) {
    const pct = clampEsp32Volume(value);
    if (esp32VolumeControl) {
      esp32VolumeControl.classList.add("is-syncing");
      esp32VolumeControl.classList.remove("is-error");
    }
    try {
      const resp = await fetch("/api/device/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ speaker_volume: pct / 100 }),
      });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok || body.ok === false) {
        throw new Error(body.error || `HTTP ${resp.status}`);
      }
    } catch (err) {
      console.warn("[esp32] speaker volume sync failed:", err);
      if (esp32VolumeControl) esp32VolumeControl.classList.add("is-error");
    } finally {
      if (esp32VolumeControl) esp32VolumeControl.classList.remove("is-syncing");
      esp32VolumePending = false;
    }
  }

  function scheduleEsp32VolumeSync(value) {
    const pct = setEsp32VolumeUi(value, { persist: true });
    esp32VolumePending = true;
    if (esp32VolumeTimer) clearTimeout(esp32VolumeTimer);
    esp32VolumeTimer = setTimeout(() => {
      syncEsp32Volume(pct);
    }, 150);
  }

  function initEsp32VolumeControl() {
    if (!esp32VolumeSlider) return;
    let initial = 70;
    try {
      const stored = localStorage.getItem(ESP32_VOLUME_KEY);
      if (stored != null) initial = clampEsp32Volume(stored);
    } catch (_) { /* ignore */ }
    setEsp32VolumeUi(initial);
    esp32VolumeSlider.addEventListener("input", () => {
      scheduleEsp32VolumeSync(esp32VolumeSlider.value);
    });
  }

  function maybeSyncInitialEsp32Volume() {
    if (esp32VolumeInitialSyncDone || !esp32VolumeSlider) return;
    const esp = cameraCache && cameraCache.esp32 ? cameraCache.esp32 : null;
    if (!esp || esp.enabled === false || esp.online === false) return;
    esp32VolumeInitialSyncDone = true;
    syncEsp32Volume(esp32VolumeSlider.value);
  }

  function clampSidebarWidth(width) {
    return Math.max(MIN_SIDEBAR_WIDTH, Math.min(MAX_SIDEBAR_WIDTH, Math.round(width)));
  }

  function isSidebarResizeEnabled() {
    return !!(appShell && sidebar && sidebarResizer && window.innerWidth > SIDEBAR_COLLAPSE_BREAKPOINT);
  }

  function applySidebarWidth(width, { persist = false } = {}) {
    const next = clampSidebarWidth(width);
    document.documentElement.style.setProperty("--sidebar-width", `${next}px`);
    if (sidebarResizer) sidebarResizer.setAttribute("aria-valuenow", String(next));
    if (persist) localStorage.setItem(SIDEBAR_WIDTH_KEY, String(next));
  }

  function endSidebarResize(persist = true) {
    if (!sidebarResizeState) return;
    const currentWidth = sidebar ? sidebar.getBoundingClientRect().width : DEFAULT_SIDEBAR_WIDTH;
    applySidebarWidth(currentWidth, { persist: persist !== false });
    sidebarResizeState = null;
    if (appShell) appShell.classList.remove("is-resizing");
    window.removeEventListener("pointermove", onSidebarResizeMove);
    window.removeEventListener("pointerup", endSidebarResize);
    window.removeEventListener("pointercancel", endSidebarResize);
  }

  function onSidebarResizeMove(ev) {
    if (!sidebarResizeState) return;
    applySidebarWidth(sidebarResizeState.startWidth + (ev.clientX - sidebarResizeState.startX));
  }

  function syncSidebarResizeState() {
    if (!sidebarResizer) return;
    const enabled = isSidebarResizeEnabled();
    sidebarResizer.tabIndex = enabled ? 0 : -1;
    sidebarResizer.setAttribute("aria-hidden", enabled ? "false" : "true");
    if (!enabled) {
      endSidebarResize(false);
      return;
    }
    const saved = parseInt(localStorage.getItem(SIDEBAR_WIDTH_KEY) || "", 10);
    applySidebarWidth(Number.isFinite(saved) ? saved : DEFAULT_SIDEBAR_WIDTH);
  }

  function beginSidebarResize(ev) {
    if (!isSidebarResizeEnabled() || ev.button !== 0) return;
    ev.preventDefault();
    sidebarResizeState = {
      startX: ev.clientX,
      startWidth: sidebar ? sidebar.getBoundingClientRect().width : DEFAULT_SIDEBAR_WIDTH,
    };
    if (appShell) appShell.classList.add("is-resizing");
    window.addEventListener("pointermove", onSidebarResizeMove);
    window.addEventListener("pointerup", endSidebarResize);
    window.addEventListener("pointercancel", endSidebarResize);
  }

  function nudgeSidebarWidth(delta, { persist = true } = {}) {
    const currentWidth = sidebar ? sidebar.getBoundingClientRect().width : DEFAULT_SIDEBAR_WIDTH;
    applySidebarWidth(currentWidth + delta, { persist });
  }

  function playbackModeLabel(mode) {
    return PLAYBACK_MODE_LABELS_CN[mode] || mode;
  }
  let playbackMode = "cleaned";

  let ws = null;
  let reqCounter = 0;
  const WEB_PAGE_OWNER_KEY = "lampgo.webPageOwner";
  const WEB_PAGE_OWNER_TTL_MS = 30000;
  const WEB_PAGE_OWNER_REFRESH_MS = 5000;
  const webPageClientId = (() => {
    try {
      let id = sessionStorage.getItem("lampgo.webPageClientId");
      if (!id) {
        id = `web_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 9)}`;
        sessionStorage.setItem("lampgo.webPageClientId", id);
      }
      return id;
    } catch {
      return `web_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 9)}`;
    }
  })();
  let webPageOwnerActive = true;
  let webPageOwnerTimer = null;
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
  let callPreemptedAwaitingResponse = false;
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
    scheduleSessionServerPush();
  }

  // --- server-side persistence (shared across browsers + process restarts) ---
  let sessionSyncReady = false;
  let sessionPushTimer = null;
  let sessionPushInFlight = false;
  let sessionPushPending = false;

  function scheduleSessionServerPush() {
    if (!sessionSyncReady) return;
    if (sessionPushTimer) clearTimeout(sessionPushTimer);
    sessionPushTimer = setTimeout(pushSessionsToServer, 800);
  }

  async function pushSessionsToServer() {
    sessionPushTimer = null;
    if (sessionPushInFlight) {
      sessionPushPending = true;
      return;
    }
    sessionPushInFlight = true;
    try {
      const payload = {
        active_session_id: activeSessionId || null,
        sessions: sessions.slice(0, MAX_SESSIONS),
      };
      await fetch("/api/sessions", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } catch (err) {
      console.warn("[sessions] server push failed:", err);
    } finally {
      sessionPushInFlight = false;
      if (sessionPushPending) {
        sessionPushPending = false;
        scheduleSessionServerPush();
      }
    }
  }

  async function waitForSessionSyncIdle(timeoutMs = 3000) {
    if (sessionPushTimer) {
      clearTimeout(sessionPushTimer);
      sessionPushTimer = null;
    }
    const start = Date.now();
    while (sessionPushInFlight && Date.now() - start < timeoutMs) {
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
  }

  async function syncSessionsFromServer() {
    // Boot flow:
    //   1. We already loaded localStorage into `sessions` synchronously.
    //   2. Ask server for its snapshot.
    //   3. If server has any sessions, it wins (authoritative across
    //      browsers / process restarts) — replace local copy and re-render.
    //   4. If server is empty but we have local sessions, push ours up to
    //      seed the server (first-time migration from the localStorage-only
    //      era).
    try {
      const resp = await fetch("/api/sessions", { method: "GET" });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const body = await resp.json();
      if (body && body.ok && body.result) {
        const remote = body.result.sessions;
        const remoteActive = body.result.active_session_id || null;
        if (Array.isArray(remote) && remote.length > 0) {
          sessions = remote.map((s) => ({
            id: s.id,
            title: s.title || "新会话",
            messages: Array.isArray(s.messages) ? s.messages : [],
            createdAt: s.createdAt || Date.now(),
            updatedAt: s.updatedAt || Date.now(),
            ...(s.summarized !== undefined ? { summarized: s.summarized } : {}),
            ...(s.lastActivityAt !== undefined ? { lastActivityAt: s.lastActivityAt } : {}),
            ...(s.summarizeAttemptedAt !== undefined ? { summarizeAttemptedAt: s.summarizeAttemptedAt } : {}),
          }));
          activeSessionId =
            remoteActive && sessions.find((s) => s.id === remoteActive)
              ? remoteActive
              : null;
          try {
            localStorage.setItem(SESSION_STORAGE_KEY, JSON.stringify(sessions.slice(0, MAX_SESSIONS)));
            if (activeSessionId) {
              localStorage.setItem(ACTIVE_SESSION_KEY, activeSessionId);
            } else {
              localStorage.removeItem(ACTIVE_SESSION_KEY);
            }
          } catch {}
          sessionSyncReady = true;
          renderHistory();
          // 直接用 activeSessionId 查找，不走 getActiveSession（后者会过滤掉通话会话）；
          // 这里只是要恢复“上次看的那个会话”，loadSession 会自己把通话会话路由到通话视图。
          const current = activeSessionId
            ? sessions.find((s) => s.id === activeSessionId) || null
            : null;
          if (current) {
            loadSession(current.id);
          } else if (chatMessages) {
            chatMessages.innerHTML = "";
            ensureEmptyState();
          }
          return;
        }
      }
      // Server has no sessions → if we have local ones, seed the server.
      sessionSyncReady = true;
      if (sessions.length > 0) {
        scheduleSessionServerPush();
      }
    } catch (err) {
      console.warn("[sessions] server sync failed, using local only:", err);
      sessionSyncReady = true;
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
    const s = sessions.find((sess) => sess.id === activeSessionId) || null;
    // 通话会话只在“通话视图”里使用，聊天视图相关的 chat-flow（输入框、LLM 历史、
    // pending assistant entry 等）不应把消息塞进通话会话里。
    if (s && s.isCall) return null;
    return s;
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
    session.summarized = false;
    const isVoicePlaceholder = role === "user" && meta && meta.voice && !meta.voice_transcribed;
    if (session.title === "新会话" && role === "user" && !isVoicePlaceholder) {
      session.title = text.length > 28 ? text.slice(0, 28) + "…" : text;
    }
    persistSessions();
    renderHistory();
    markSessionActivity(session);
    return { session, entry };
  }

  // ---- Idle memory summarizer ----
  const IDLE_MINUTES = 3;
  let idleCheckTimer = null;
  let idleSummarizeInFlight = false;

  function markSessionActivity(session) {
    if (!session) return;
    session.lastActivityAt = Date.now();
    ensureIdleTimer();
  }

  function ensureIdleTimer() {
    if (idleCheckTimer) return;
    idleCheckTimer = setInterval(checkIdleSessions, 30000);
  }

  // 冷却窗口：一次 summarize 尝试后，至少隔这么久再试同一会话。
  // 避免模型偶发失败 / 还没值得记忆的短会话把 LLM 打爆。
  const SUMMARIZE_RETRY_COOLDOWN_MS = 15 * 60 * 1000;

  function shouldSummarizeSession(session) {
    if (!session || session.summarized) return false;
    if (!session.messages || session.messages.length < 2) return false;
    const hasUser = session.messages.some((m) => m.role === "user" && (m.text || "").trim());
    const hasAssistant = session.messages.some((m) => m.role === "assistant" && (m.text || "").trim());
    if (!hasUser || !hasAssistant) return false;
    const last = session.lastActivityAt || session.updatedAt || 0;
    if (!last) return false;
    if (Date.now() - last < IDLE_MINUTES * 60 * 1000) return false;
    // Cooldown after a previous empty-bullet attempt — but only when no new
    // activity has landed since. If the user kept chatting, the transcript has
    // grown and retry is worthwhile even inside the cooldown window.
    if (
      session.summarizeAttemptedAt &&
      session.summarizeAttemptedAt >= last &&
      Date.now() - session.summarizeAttemptedAt < SUMMARIZE_RETRY_COOLDOWN_MS
    ) {
      return false;
    }
    return true;
  }

  async function checkIdleSessions() {
    if (idleSummarizeInFlight) return;
    const candidates = sessions.filter(shouldSummarizeSession);
    if (!candidates.length) return;
    // prefer the most recently active one first
    candidates.sort((a, b) => (b.lastActivityAt || 0) - (a.lastActivityAt || 0));
    const session = candidates[0];
    idleSummarizeInFlight = true;
    try {
      const payload = {
        session_id: session.id,
        messages: session.messages
          .filter((m) => m.text && (m.role === "user" || m.role === "assistant"))
          .slice(-40)
          .map((m) => ({ role: m.role, content: m.text })),
      };
      const resp = await fetch("/api/memory/summarize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        // HTTP-level failure: try again next tick without burning the cooldown.
        console.warn("[memory] summarize http", resp.status);
        return;
      }
      const data = await resp.json().catch(() => null);
      const bullets = (data && data.result && data.result.bullets) || [];
      session.summarizeAttemptedAt = Date.now();
      if (bullets.length > 0) {
        // Only seal the session once bullets actually landed. Otherwise the same
        // session could silently "consume" its one-shot summary and never retry,
        // even after we've fixed LLM params or added more conversation.
        session.summarized = true;
      }
      persistSessions();
      // If the user has "每日记忆" open right now, refresh it so the new
      // bullet shows up without needing to click the 刷新 button.
      if (bullets.length > 0 && isMemoryDailyVisible()) {
        loadMemoryDailyList().catch(() => {});
      }
    } catch (err) {
      console.warn("[memory] idle summarize failed", err);
    } finally {
      idleSummarizeInFlight = false;
    }
  }

  function isMemoryDailyVisible() {
    const settingsPane = document.querySelector('[data-settings-pane="memory"]');
    if (!settingsPane || settingsPane.classList.contains("hidden")) return false;
    const dailyView = document.querySelector('[data-memory-view="daily"]');
    if (!dailyView || dailyView.classList.contains("hidden")) return false;
    const shell = document.querySelector(".app-shell");
    return shell && shell.getAttribute("data-view") === "settings";
  }

  // Manual trigger invoked from the "立即总结当前会话" button — lets the user
  // verify the summarize pipeline works without waiting IDLE_MINUTES.
  async function summarizeActiveSessionNow() {
    const btn = document.getElementById("btn-memory-daily-summarize-now");
    const viewer = document.getElementById("memory-daily-content");
    const session =
      sessions.find((s) => s.id === activeSessionId) ||
      sessions.find((s) => (s.messages || []).length > 0);
    if (!session || !session.messages || session.messages.length < 2) {
      if (viewer) viewer.textContent = "当前没有足够的对话可供摘要（至少需要 1 条用户 + 1 条助手消息）。";
      return;
    }
    const messages = session.messages
      .filter((m) => m.text && (m.role === "user" || m.role === "assistant"))
      .slice(-40)
      .map((m) => ({ role: m.role, content: m.text }));
    if (messages.length < 2) {
      if (viewer) viewer.textContent = "当前没有足够的对话可供摘要（语音未转写 / 消息为空）。";
      return;
    }
    if (btn) {
      btn.disabled = true;
      btn.textContent = "总结中…";
    }
    try {
      const resp = await fetch("/api/memory/summarize", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: session.id, messages }),
      });
      const data = await resp.json().catch(() => null);
      const bullets = (data && data.result && data.result.bullets) || [];
      if (!resp.ok) {
        if (viewer) viewer.textContent = `总结失败：HTTP ${resp.status}`;
      } else if (bullets.length === 0) {
        if (viewer) {
          viewer.textContent =
            "模型没有返回任何要点（skipped=" +
            ((data && data.result && data.result.skipped) || "no-summary") +
            "）。\n\n常见原因：\n" +
            "  · fast_model 是推理模型（如 mimo-v2-omni），服务端日志里会出现 memory.summarize.reasoning_only；\n" +
            "  · 对话过短，模型判断没有值得长期记忆的信息（会直接输出“无”）。";
        }
      } else {
        session.summarized = true;
        session.summarizeAttemptedAt = Date.now();
        persistSessions();
        await loadMemoryDailyList();
      }
    } catch (err) {
      if (viewer) viewer.textContent = `总结失败：${err && err.message ? err.message : err}`;
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = "立即总结当前会话";
      }
    }
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

  async function deleteSession(id) {
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
    try {
      await waitForSessionSyncIdle();
      const resp = await fetch(`/api/sessions/${encodeURIComponent(id)}`, {
        method: "DELETE",
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    } catch (err) {
      console.warn("[sessions] delete on server failed:", err);
      addSystemMessage("删除会话失败，刷新后历史可能恢复。");
    }
  }

  function loadSession(id) {
    const session = sessions.find((s) => s.id === id);
    if (!session) return;

    // 通话会话需要在“通话视图”里展示，这样它带的工具调用 activity_html 才能用同一份 DOM
    // 重新水合，并且用户随时能点视图右上角的“通话”按钮发起新一轮通话。
    if (session.isCall) {
      // 直播通话进行中（同一 session）：只切视图，不重建 DOM，避免抹掉正在进行的 bubble。
      const callHasBubbles = !!(
        callMessages && callMessages.querySelector(".msg-bubble-wrap")
      );
      const isLiveCall =
        prevCallState === "active" ||
        prevCallState === "joining" ||
        prevCallState === "leaving";
      if (id === callSessionId && callHasBubbles && isLiveCall) {
        activeSessionId = id;
        persistSessions();
        showView("call");
        renderHistory();
        scrollCallMessages();
        return;
      }
      // 用户在直播通话里点了另一个历史通话：先不动 DOM，仅切视图，避免误伤当前通话。
      if (isLiveCall && id !== callSessionId) {
        showView("call");
        renderHistory();
        return;
      }
      activeSessionId = id;
      persistSessions();
      showView("call");
      renderHistoricalCallSession(session);
      renderHistory();
      return;
    }

    // 守卫的意图：同一会话里已经渲染过真实 bubble → 不要重建 DOM，保留 in-progress thinking 状态。
    // 之前用 `childElementCount > 0` 会把 index.html 里静态存在的 `#empty-state` 当成 "已渲染"，
    // 导致启动后 syncSessionsFromServer 在 activeSessionId 刚被设置那一帧就 early-return，
    // 27 条历史 bubble 永远不 render —— 左侧能看到 "你好 5分钟前"，右侧却是欢迎语。
    const hasRealBubbles = !!(
      chatMessages && chatMessages.querySelector(".msg-bubble-wrap")
    );
    if (id === activeSessionId && hasRealBubbles) {
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

  async function clearAllHistory() {
    if (!confirm("确认清空全部会话历史？")) return;
    sessions = [];
    activeSessionId = null;
    persistSessions();
    chatMessages.innerHTML = "";
    ensureEmptyState();
    renderHistory();
    try {
      await waitForSessionSyncIdle();
      const resp = await fetch("/api/sessions", {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirm: true }),
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    } catch (err) {
      console.warn("[sessions] clear all on server failed:", err);
      addSystemMessage("清空历史失败，刷新后历史可能恢复。");
    }
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
      refreshOpenclawHealth();
      startOpenclawHealthPolling();
    } else {
      stopOpenclawHealthPolling();
    }
    if (name === "settings") {
      initSettingsView();
    }
  }

  /* ---- OpenClaw health ---- */

  let openclawHealthTimer = null;
  let openclawHealthLastStatus = null;
  // Remember the unhealthy-state key the user manually dismissed; we won't auto
  // re-expand the card for the same state again, but if the state transitions
  // (e.g. basic -> partial after a partial install) we'll nag once more.
  let openclawHealthDismissedKey = null;

  const OC_HEALTH_LABELS = {
    running: "运行中",
    idle: "就绪",
    ready: "就绪",
    degraded: "gateway 离线",
    not_installed: "未安装",
    missing: "未安装",
    partial: "未完全安装",
    basic: "缺插件",
    unknown: "检测中…",
  };

  function startOpenclawHealthPolling() {
    stopOpenclawHealthPolling();
    openclawHealthTimer = setInterval(refreshOpenclawHealth, 10000);
  }

  function stopOpenclawHealthPolling() {
    if (openclawHealthTimer) {
      clearInterval(openclawHealthTimer);
      openclawHealthTimer = null;
    }
  }

  async function refreshOpenclawHealth() {
    if (!btnOpenclawHealth) return;
    try {
      const resp = await fetch("/api/openclaw/health", { cache: "no-store" });
      if (!resp.ok) throw new Error("status " + resp.status);
      const data = await resp.json();
      if (!data || !data.ok) throw new Error(data && data.error || "bad response");
      applyOpenclawHealth(data.result);
    } catch (err) {
      applyOpenclawHealth(null, err);
    }
  }

  function applyOpenclawHealth(result, err) {
    openclawHealthLastStatus = result;
    if (!btnOpenclawHealth) return;
    const dot = document.getElementById("oc-health-dot");
    const labelEl = btnOpenclawHealth.querySelector(".device-chip-label");

    const setDot = (state) => {
      if (!dot) return;
      dot.classList.remove("is-online", "is-offline", "is-warn");
      if (state === "online") dot.classList.add("is-online");
      else if (state === "offline") dot.classList.add("is-offline");
      else if (state === "warn") dot.classList.add("is-warn");
    };

    if (err || !result) {
      setDot("offline");
      if (labelEl) labelEl.textContent = "检查失败";
      btnOpenclawHealth.title = err ? String(err) : "无法获取 OpenClaw 健康状态";
      return;
    }

    const conn = result.connection || "unknown";
    const integ = result.integration || {};
    const overall = integ.overall || "unknown";

    // Green only when fully wired up AND reachable (idle or running).
    // Everything else (not_installed / basic / partial / missing) is red.
    const isHealthy =
      (conn === "running" || conn === "idle") && overall === "ready";

    // Soft warning: 所有硬条件都满足了，只是 plugin 源码比安装版本新，
    // 或者 token 没对上。这种情况下 dot 不标红，但用黄色提示用户刷新。
    const needsRefresh =
      isHealthy &&
      ((integ.plugin_freshness && integ.plugin_freshness.ok === false) ||
        (integ.plugin_token && integ.plugin_token.ok === false));

    if (!isHealthy) setDot("offline");
    else if (needsRefresh) setDot("warn");
    else setDot("online");

    let label;
    if (!isHealthy) {
      label = OC_HEALTH_LABELS[overall] || OC_HEALTH_LABELS[conn] || "未知";
    } else if (needsRefresh) {
      label = "需刷新";
    } else {
      label = OC_HEALTH_LABELS[conn] || "就绪";
    }
    if (labelEl) labelEl.textContent = label;

    let title = `OpenClaw：${label}`;
    if (conn === "running" && isHealthy && !needsRefresh) {
      title += `（${result.running_tasks || 0} 个任务进行中）`;
    } else if (conn === "not_installed" || overall === "missing") {
      // 点开详情卡里有更完整的引导；tooltip 这里只给一句话摘要。
      if (integ && integ.binary && integ.binary.ok === false) {
        title += "（请先到 https://openclaw.ai/ 安装 openclaw CLI，再跑 `lampgo install-openclaw --yes`）";
      } else {
        title += "（请在终端运行 `lampgo install-openclaw --yes`）";
      }
    } else if (overall === "degraded") {
      title += "（配置齐全，但 gateway 守护进程无响应；点我查看如何拉起）";
    } else if (overall === "basic") {
      title += "（只装了 openclaw CLI，缺 lampgo plugin 和 skill；硬件控制不可用）";
    } else if (overall === "partial") {
      title += "（plugin / skill 未完全安装，点我查看详情）";
    } else if (needsRefresh) {
      title += "（plugin 有更新或 token 未同步，建议 `lampgo install-openclaw --yes`）";
    } else if (conn === "idle" && isHealthy) {
      title += "（等待任务，按需拉起）";
    }
    btnOpenclawHealth.title = title;

    // Auto-expand the detail card when something is wrong OR when a soft
    // refresh is recommended. Respect manual dismissal for the same state key;
    // re-nag when the state changes.
    if (openclawHealthCard) {
      let stateKey;
      if (!isHealthy) {
        stateKey = overall && overall !== "unknown" ? overall : conn;
      } else if (needsRefresh) {
        stateKey = "needs_refresh";
      } else {
        stateKey = "healthy";
      }

      if (isHealthy && !needsRefresh) {
        openclawHealthDismissedKey = null;
        if (!openclawHealthCard.classList.contains("hidden")) {
          renderOpenclawHealthCard(result);
        }
      } else if (stateKey !== openclawHealthDismissedKey) {
        renderOpenclawHealthCard(result);
        openclawHealthCard.classList.remove("hidden");
      } else if (!openclawHealthCard.classList.contains("hidden")) {
        renderOpenclawHealthCard(result);
      }
    }
  }

  function renderOpenclawHealthCard(result) {
    if (!openclawHealthCard || !result) return;
    const integ = result.integration || {};
    // step 对象里多一个 level 字段："bad"=红色阻塞, "warn"=黄色建议, 默认按 ok 推导。
    // 当 openclaw CLI 本身都没装时，plugin_freshness / plugin_token 的 warn 语气
    // 是误导性的——这两项跟所有其它项一样根本跑不起来。把它们升级成 bad 让整张
    // 详情卡读起来就是一面统一的红 ✗，用户不会误以为"有俩项还挺健康"。
    const noCli = !integ.binary?.ok;
    const softLevel = noCli ? "bad" : "warn";
    const steps = [
      integ.binary,
      integ.config_file,
      integ.skill,
      integ.plugin,
      integ.trusted,
      integ.gateway,
      integ.plugin_freshness && { ...integ.plugin_freshness, level: softLevel },
      integ.plugin_token && { ...integ.plugin_token, level: softLevel },
    ].filter(Boolean);

    const stepHtml = steps
      .map((s) => {
        let cls;
        let icon;
        if (s.ok) {
          cls = "is-ok";
          icon = "✓";
        } else if (s.level === "warn") {
          cls = "is-warn";
          icon = "!";
        } else {
          cls = "is-bad";
          icon = "✗";
        }
        return (
          `<li class="oc-health-step ${cls}">` +
          `<span class="oc-health-step-icon">${icon}</span>` +
          `<span><strong>${escapeHtml(s.label || "")}</strong> — ${escapeHtml(s.detail || "")}</span>` +
          `</li>`
        );
      })
      .join("");

    const notesHtml = (integ.notes || []).map((n) => `<div>• ${escapeHtml(n)}</div>`).join("");

    const needsInstall = !integ.plugin?.ok || !integ.skill?.ok || !integ.trusted?.ok;
    const gatewayDown = integ.gateway && integ.gateway.ok === false;
    const needsRefresh =
      (integ.plugin_freshness && integ.plugin_freshness.ok === false) ||
      (integ.plugin_token && integ.plugin_token.ok === false);
    let hintHtml;
    if (noCli) {
      // openclaw CLI 自己没装的时候，`lampgo install-openclaw --yes` 必然报错，
      // 所以要先把用户引导到官网装 CLI，再跑 lampgo 这边的一键集成。
      hintHtml =
        `<div class="oc-health-hint is-warn">还没安装 <code>openclaw</code> CLI，所有集成步骤都跑不起来。` +
        `<br>1. 先访问 <a href="https://openclaw.ai/" target="_blank" rel="noopener noreferrer">https://openclaw.ai/</a> 安装 openclaw；` +
        `<br>2. 再在终端运行 <code>uv run lampgo install-openclaw --yes</code>，把 lampgo 集成注册进去；` +
        `<br>3. 回来点 <em>刷新</em>。` +
        `</div>`;
    } else if (needsInstall) {
      hintHtml = `<div class="oc-health-hint">一键修复：在终端运行 <code>uv run lampgo install-openclaw --yes</code>，再点 <em>刷新</em>。</div>`;
    } else if (gatewayDown) {
      hintHtml =
        `<div class="oc-health-hint">配置都齐了，但 OpenClaw gateway 守护进程没在响应。` +
        `<br>在终端运行下面任一条，然后点 <em>刷新</em>：` +
        `<br>• <code>openclaw gateway start</code>（后台常驻，推荐日常使用）` +
        `<br>• <code>openclaw gateway restart</code>（守护进程卡死、端口却被占时用）` +
        `<br>• <code>openclaw gateway</code>（前台运行，方便看日志排查）` +
        `</div>`;
    } else if (needsRefresh) {
      // 动态地把"需要同步的 tool"和"已经同步的 tool"都列出来，避免把
      // 具体名字硬编码在前端代码里——每次 plugin 新增/删除 tool 都得来
      // 改这里。后端 `tool_sync` 已经算好了 source / installed 的对称差。
      const sync = integ.tool_sync || {};
      const missing = Array.isArray(sync.missing_in_installed) ? sync.missing_in_installed : [];
      const extra = Array.isArray(sync.extra_in_installed) ? sync.extra_in_installed : [];
      const installed = Array.isArray(sync.installed_tools) ? sync.installed_tools : [];
      // Token-out-of-sync alone can trigger this branch without any tool
      // diff — detect it so we don't tell the user "3 tools will be
      // registered" when actually it's just a token rotation.
      const tokenStale = integ.plugin_token && integ.plugin_token.ok === false;
      const freshnessStale = integ.plugin_freshness && integ.plugin_freshness.ok === false;

      const toolListHtml = (names) =>
        names.map((n) => `<code>${escapeHtml(n)}</code>`).join(" / ");

      const detailLines = [];
      if (missing.length) {
        detailLines.push(
          `<br>• <strong>需要同步进去</strong>（${missing.length} 个）：` +
          toolListHtml(missing),
        );
      }
      if (extra.length) {
        // Rare but worth surfacing: source removed a tool, installed
        // still has it.  Reinstall cleans it out.
        detailLines.push(
          `<br>• <strong>源码已移除、插件仍残留</strong>（${extra.length} 个）：` +
          toolListHtml(extra),
        );
      }
      if (installed.length && !missing.length && !extra.length && freshnessStale) {
        // Freshness says "source newer" but our tool diff is empty — the
        // change must be inside existing tools' schemas/descriptions, not
        // the tool list itself.  Be honest about that so the user doesn't
        // wonder why "needs sync" but "0 tools missing".
        detailLines.push(
          `<br>• 现有 ${installed.length} 个 tool 的 schema 或描述有更新，名字没变。`,
        );
      }
      if (installed.length) {
        detailLines.push(
          `<br>• <strong>已同步</strong>（${installed.length} 个）：` +
          toolListHtml(installed),
        );
      }
      if (tokenStale) {
        detailLines.push(`<br>• <strong>鉴权 token</strong> 需要重新写入。`);
      }

      hintHtml =
        `<div class="oc-health-hint is-warn">核心组件已装好，但 plugin 需要刷新一下：` +
        `<br>在终端运行 <code>uv run lampgo install-openclaw --yes</code>，再点 <em>刷新</em>。` +
        detailLines.join("") +
        `</div>`;
    } else {
      hintHtml = `<div class="oc-health-hint">所有组件已就绪。</div>`;
    }

    openclawHealthCard.innerHTML =
      `<div class="oc-health-card-title">` +
      `<span>OpenClaw 集成详情</span>` +
      `<button class="oc-health-card-close" type="button" aria-label="关闭">×</button>` +
      `</div>` +
      `<ul class="oc-health-steps">${stepHtml}</ul>` +
      (notesHtml ? `<div class="oc-health-hint">${notesHtml}</div>` : "") +
      hintHtml;

    const closeBtn = openclawHealthCard.querySelector(".oc-health-card-close");
    if (closeBtn) {
      closeBtn.addEventListener("click", () => {
        openclawHealthCard.classList.add("hidden");
        openclawHealthDismissedKey = _currentUnhealthyKey();
      });
    }
  }

  function _currentUnhealthyKey() {
    const r = openclawHealthLastStatus;
    if (!r) return null;
    const conn = r.connection || "unknown";
    const integ = r.integration || {};
    const overall = integ.overall || "unknown";
    const healthy = (conn === "running" || conn === "idle") && overall === "ready";
    if (!healthy) return overall !== "unknown" ? overall : conn;
    const needsRefresh =
      (integ.plugin_freshness && integ.plugin_freshness.ok === false) ||
      (integ.plugin_token && integ.plugin_token.ok === false);
    return needsRefresh ? "needs_refresh" : "healthy";
  }

  function escapeHtml(str) {
    return String(str == null ? "" : str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  if (btnOpenclawHealth) {
    btnOpenclawHealth.addEventListener("click", () => {
      if (!openclawHealthCard) return;
      const hidden = openclawHealthCard.classList.toggle("hidden");
      if (hidden) {
        // User closed the card; remember the dismissal for this state.
        openclawHealthDismissedKey = _currentUnhealthyKey();
      } else {
        if (openclawHealthLastStatus) {
          renderOpenclawHealthCard(openclawHealthLastStatus);
        }
        // User re-opened manually; clear dismissal so later auto-shows still work.
        openclawHealthDismissedKey = null;
        refreshOpenclawHealth();
      }
    });
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

  function setPlaybackMode(mode, { persist = true } = {}) {
    const nextMode = PLAYBACK_MODES.has(mode) ? mode : "cleaned";
    playbackMode = nextMode;
    playbackModeButtons.forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.playbackMode === nextMode);
    });
    if (persist) {
      // `persist` means "this is a user-driven session override". We write it
      // to localStorage so it survives reloads within this browser until the
      // server-side default changes (see saveCfgFromButton).
      localStorage.setItem(PLAYBACK_MODE_KEY, nextMode);
    }
  }

  /* ---- WebSocket ---- */

  function readWebPageOwner() {
    try {
      const raw = localStorage.getItem(WEB_PAGE_OWNER_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  }

  function writeWebPageOwner() {
    if (!webPageOwnerActive) return;
    try {
      const now = Date.now();
      localStorage.setItem(WEB_PAGE_OWNER_KEY, JSON.stringify({
        owner: webPageClientId,
        url: location.href,
        updatedAt: now,
        expiresAt: now + WEB_PAGE_OWNER_TTL_MS,
      }));
    } catch (err) {
      console.warn("[page] owner lock unavailable:", err);
    }
  }

  function releaseWebPageOwner() {
    if (webPageOwnerTimer) {
      clearInterval(webPageOwnerTimer);
      webPageOwnerTimer = null;
    }
    try {
      const current = readWebPageOwner();
      if (current && current.owner === webPageClientId) {
        localStorage.removeItem(WEB_PAGE_OWNER_KEY);
      }
    } catch {
      // Best effort only.
    }
  }

  function deactivateSecondaryPage(reason) {
    if (!webPageOwnerActive) return;
    webPageOwnerActive = false;
    releaseWebPageOwner();
    try { stopAllTts(); } catch (_) { /* ignore */ }
    try {
      if (lkRoom || browserCallStartPromise) stopBrowserLiveKitCall();
    } catch (_) {
      // The call module may not be initialized yet.
    }
    if (ws) {
      const oldWs = ws;
      ws = null;
      try { oldWs.close(); } catch (_) { /* ignore */ }
    }
    setConnected(false);
    if (btnCallStart) btnCallStart.disabled = true;
    if (btnCallEnd) btnCallEnd.disabled = true;
    console.info("[page] secondary Lampgo page disconnected:", reason || "another page is active");
  }

  function startWebPageOwnerHeartbeat() {
    writeWebPageOwner();
    if (webPageOwnerTimer) clearInterval(webPageOwnerTimer);
    webPageOwnerTimer = window.setInterval(writeWebPageOwner, WEB_PAGE_OWNER_REFRESH_MS);
    window.addEventListener("storage", (ev) => {
      if (ev.key !== WEB_PAGE_OWNER_KEY || !ev.newValue) return;
      try {
        const next = JSON.parse(ev.newValue);
        if (
          next &&
          next.owner &&
          next.owner !== webPageClientId &&
          Number(next.expiresAt || 0) > Date.now()
        ) {
          deactivateSecondaryPage("another page claimed ownership");
        }
      } catch {
        // Ignore malformed storage events.
      }
    });
    window.addEventListener("beforeunload", releaseWebPageOwner);
  }

  function claimTtsPlaybackClient(active = true) {
    if (!ws || ws.readyState !== WebSocket.OPEN || !webPageOwnerActive) return;
    const visible = document.visibilityState !== "hidden";
    ws.send(JSON.stringify({
      type: "tts_playback_client",
      active: !!active && visible,
      client_id: webPageClientId,
      visible,
      focused: typeof document.hasFocus === "function" ? document.hasFocus() : true,
    }));
  }

  window.addEventListener("focus", () => claimTtsPlaybackClient(true));
  document.addEventListener("visibilitychange", () => {
    claimTtsPlaybackClient(document.visibilityState !== "hidden");
  });

  function connect() {
    if (!webPageOwnerActive) return;
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/ws`);

    ws.onopen = () => {
      setConnected(true);
      // Re-run event backfill on every (re)connect so we pick up events
      // missed during the disconnect AND can detect server seq rollback
      // (see backfillEventsFromServer for the rollback guard).
      backfillEventsFromServer();
      ws.send(JSON.stringify({ type: "skills" }));
      ws.send(JSON.stringify({ type: "recordings" }));
      ws.send(JSON.stringify({ type: "expressions" }));
      ws.send(JSON.stringify({ type: "openclaw_tasks" }));
      ws.send(JSON.stringify({ type: "status" }));
      claimTtsPlaybackClient();
      // Pre-populate the Hardware settings dropdowns (camera.port /
      // voice.mic_device) so the user never sees "(自定义，保留原值)" as the
      // only option. These probes are cheap on the server and the results
      // are cached in `detectedDevices` / `cameraCache` for the chip
      // popovers, keeping both surfaces consistent.
      ws.send(JSON.stringify({ type: "list_cameras", request_id: `cam_boot_${Date.now()}` }));
      ws.send(JSON.stringify({ type: "list_mics", request_id: `mic_boot_${Date.now()}` }));
    };

    ws.onclose = () => {
      setConnected(false);
      if (webPageOwnerActive) setTimeout(connect, 2000);
    };

    ws.onerror = () => ws.close();

    ws.onmessage = (evt) => {
      if (evt.data instanceof ArrayBuffer) {
        feedEsp32Audio(new Uint8Array(evt.data));
        return;
      }
      if (evt.data instanceof Blob) {
        evt.data.arrayBuffer().then((buf) => feedEsp32Audio(new Uint8Array(buf)));
        return;
      }
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
    let prevGroup = undefined;
    items.forEach((item) => {
      if (item.group !== undefined && prevGroup !== undefined && item.group !== prevGroup) {
        const hr = document.createElement("hr");
        hr.className = "device-popover-sep";
        listEl.appendChild(hr);
      }
      prevGroup = item.group;
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
    const esp = cameraCache.esp32;
    const espVisible = !!(esp && !esp.hidden && !esp.needs_firmware_update);
    const espOnline = esp && esp.online;
    const espHost = (esp && esp.host) || "";
    const items = [
      { id: "", label: "系统默认麦克风", meta: "跟随浏览器/操作系统设置", group: "browser" },
      ...(espVisible ? [{ id: "esp32", label: esp32PeripheralLabel("mic", esp), meta: espOnline ? `${espHost} · 在线` : "离线", group: "esp32" }] : []),
      ...micDevicesCache.map((d, i) => ({
        id: d.deviceId,
        label: d.label || `麦克风 ${d.deviceId.slice(0, 8) || i + 1}`,
        meta: d.deviceId ? d.deviceId.slice(0, 24) + (d.deviceId.length > 24 ? "…" : "") : "",
        group: "browser",
      })),
    ];
    const subtitle = granted
      ? `检测到 ${micDevicesCache.length} 个本地${espVisible ? " + ESP32" : ""}`
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

  function normalizeDeviceName(name) {
    return String(name || "")
      .toLowerCase()
      .replace(/\s+/g, " ")
      .replace(/\s*\([^)]*\)\s*/g, " ")
      .trim();
  }

  function esp32DeviceDisplayId(esp) {
    const rawId = String((esp && esp.device_id) || "").trim();
    if (!rawId) return "lampgo";
    return rawId.toLowerCase().startsWith("lampgo-") ? rawId : `lampgo-${rawId}`;
  }

  function esp32PeripheralLabel(kind, esp) {
    return `${esp32DeviceDisplayId(esp)} ${kind === "mic" ? "麦克风" : "摄像头"}`;
  }

  function serverMicForBrowserChoice(id) {
    if (!id) return "";
    if (id === "esp32") return "esp32";
    const selected = micDevicesCache.find((d) => d.deviceId === id);
    const browserName = normalizeDeviceName(selected && selected.label);
    if (!browserName) return null;
    const exact = detectedDevices.mics.find((m) => normalizeDeviceName(m.name) === browserName);
    if (exact) return String(exact.index || "");
    const fuzzy = detectedDevices.mics.find((m) => {
      const serverName = normalizeDeviceName(m.name);
      return serverName && (serverName.includes(browserName) || browserName.includes(serverName));
    });
    return fuzzy ? String(fuzzy.index || "") : null;
  }

  function syncServerMicFromTopbar(id) {
    const serverMic = serverMicForBrowserChoice(id);
    if (serverMic === null) {
      console.warn("[voice] no matching server mic for browser device:", id);
      return;
    }
    send({ type: "set_mic", device: serverMic, request_id: `mic_set_${Date.now()}` });
  }

  function selectMicDevice(id) {
    preferredMicId = id || "";
    try {
      if (id) localStorage.setItem(MIC_DEVICE_KEY, id);
      else localStorage.removeItem(MIC_DEVICE_KEY);
    } catch (_) { /* ignore */ }
    window.dispatchEvent(new CustomEvent("lampgo:mic-selected", { detail: { deviceId: preferredMicId } }));
    syncServerMicFromTopbar(preferredMicId);
    if (id === "esp32") {
      if (chipMic) chipMic.title = `麦克风：${esp32PeripheralLabel("mic", cameraCache.esp32)}`;
    } else {
      const selected = micDevicesCache.find((d) => d.deviceId === id);
      if (chipMic) chipMic.title = selected && selected.label ? `麦克风：${selected.label}` : "系统默认麦克风";
    }
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
  let cameraCache = { cameras: [], active: "", available: true, reason: "", esp32: null };
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
    const { cameras, active, available, reason, esp32 } = cameraCache;
    const items = [];
    items.push({ id: "", label: "关闭摄像头", meta: "禁用视觉输入", group: "off" });
    const espVisible = !!(esp32 && !esp32.hidden && !esp32.needs_firmware_update);
    if (espVisible && esp32.enabled !== false) {
      const status = esp32.online ? "在线" : "离线";
      const host = esp32.host || "自动发现";
      items.push({ id: "esp32", label: esp32PeripheralLabel("camera", esp32), meta: `${host} · ${status}`, group: "esp32" });
    } else if (!esp32 || (!esp32.hidden && !esp32.needs_firmware_update)) {
      items.push({ id: "esp32", label: esp32PeripheralLabel("camera", esp32), meta: "未启用 · 点击启用", group: "esp32" });
    }
    cameras.forEach((c) => {
      items.push({
        id: c.port,
        label: c.name ? `${c.name} (port ${c.port})` : `摄像头 ${c.port}`,
        meta: `port = ${c.port}`,
        group: "local",
      });
    });
    const localCount = cameras.length;
    let subtitle = "";
    if (loading) subtitle = "正在探测可用摄像头...";
    else if (!available) subtitle = reason || "摄像头探测不可用";
    else subtitle = `已探测 ${localCount} 个本地${espVisible ? " + ESP32" : ""} · 运行时切换`;
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
      if (port === "esp32") chipCamera.title = esp32PeripheralLabel("camera", cameraCache.esp32);
      else chipCamera.title = port ? `摄像头 port = ${port}` : "摄像头已关闭";
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
      window.dispatchEvent(new CustomEvent("lampgo:status", { detail: msg.data || {} }));
      return;
    }

    if (msg.type === "pet_pose") {
      window.dispatchEvent(new CustomEvent("lampgo:pet-pose", { detail: msg.data || {} }));
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
          esp32: msg.result.esp32 || null,
        };
        // Share the server's camera list with the settings dropdown so both
        // surfaces stay consistent without a second probe.
        detectedDevices.cameras = (msg.result.cameras || []).map((c) => ({
          port: String(c.port || ""),
          name: c.name || "",
        }));
        repopulateCameraPortSelect();
        maybeSyncInitialEsp32Volume();
        if (isCameraPopoverOpen()) renderCameraPopover(false);
      }
      return;
    }

    if (msg.type === "set_camera") {
      if (msg.ok && msg.result) {
        cameraCache.active = msg.result.active || "";
        if (msg.result.esp32) cameraCache.esp32 = msg.result.esp32;
        // Mirror the chip's runtime switch into the settings dropdown.
        const displayPort = msg.result.active === "esp32" ? "" : (msg.result.active || "");
        repopulateCameraPortSelect(displayPort);
        maybeSyncInitialEsp32Volume();
        if (isCameraPopoverOpen()) renderCameraPopover(false);
        send({ type: "status", request_id: `cam_refresh_${Date.now()}` });
      }
      return;
    }

    if (msg.type === "list_mics") {
      if (msg.ok && msg.result) {
        // `voice.mic_device` is a server-side PyAudio index. Feed the same
        // list the settings hardware card uses so the dropdown matches
        // whatever the server can actually open.
        detectedDevices.mics = (msg.result.mics || []).map((m) => ({
          index: String(m.index || ""),
          name: m.name || "",
          is_default: !!m.is_default,
        }));
        repopulateMicDeviceSelect();
      }
      return;
    }

    if (msg.type === "set_mic") {
      if (msg.ok && msg.result) {
        detectedDevices.mics = (msg.result.mics || []).map((m) => ({
          index: String(m.index || ""),
          name: m.name || "",
          is_default: !!m.is_default,
        }));
        repopulateMicDeviceSelect(String(msg.result.active || ""));
        if (chipMic && msg.result.active !== undefined) {
          const active = String(msg.result.active || "");
          const matched = detectedDevices.mics.find((m) => String(m.index) === active);
          if (active === "esp32") chipMic.title = `麦克风：${esp32PeripheralLabel("mic", cameraCache.esp32)}（通话已同步）`;
          else if (matched && matched.name) chipMic.title = `麦克风：${matched.name}（通话已同步）`;
          else if (!active) chipMic.title = "系统默认麦克风（通话已同步）";
        }
      } else {
        console.warn("[voice] set server mic failed:", msg.error || msg);
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

    if (msg.ok && msg.result && (msg.result.expression_catalog || msg.result.expressions)) {
      renderExpressions(msg.result.expression_catalog || msg.result.expressions);
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

    if (evt === "TtsAudio" && data.audio && !lkRoom) {
      handleTtsAudio(data.audio, data.format || "mp3", data.sample_rate || data.sampleRate || 0);
    }
    if (evt === "ConversationStateChanged" && data.state && !lkRoom) updateCallViewState(data.state);
    if (evt === "WakeWordDetected") {
      if (
        isFreshLiveEvent(msg, CALL_WAKE_FRESH_MS) &&
        !lkRoom &&
        !browserCallStartPromise &&
        prevCallState !== "joining" &&
        prevCallState !== "active" &&
        prevCallState !== "leaving"
      ) {
        enterBrowserLiveKitCall("wake");
      }
      return;
    }

    if (evt === "Esp32AudioRelayStatus") {
      if (data.state === "connecting") addCallSystemNote("正在连接 ESP32 麦克风…");
      else if (data.state === "connected") addCallSystemNote("ESP32 麦克风已接入，正在听你说话…");
      else if (data.state === "closed") addCallSystemNote("ESP32 麦克风通道已关闭");
      return;
    }
    if (evt === "Esp32AudioRelayError") {
      addCallSystemNote(`ESP32 麦克风连接失败：${data.error || "未知错误"}`);
      return;
    }

    if (evt === "VoiceUserText" && data.user_text && data.request_id) {
      touchBrowserCallActivity("voice_user_text");
      // A new user utterance arrived — if we were already counting down to a
      // goodbye-induced hangup, abort it so the conversation can continue.
      if (hangupPending) hangupCancelled = true;
      ensureLiveCallSessionVisible();
      if (!callUserRequestIds.has(data.request_id)) {
        addCallUserBubble(data.user_text);
        callUserRequestIds.add(data.request_id);
        pushCallMessage("user", data.user_text);
      }
      const existingBubble = pendingMessages.get(data.request_id);
      if (existingBubble) {
        updateCallAssistantQuote(existingBubble, data.user_text);
        clearAssistantLoadingDots(existingBubble);
      } else {
        addCallAssistantBubble(data.request_id, data.user_text, callPreemptedAwaitingResponse);
      }
      callPreemptedAwaitingResponse = false;
      return;
    }

    const requestId = data.request_id || "";
    let bubble = requestId ? pendingMessages.get(requestId) : null;
    if (!bubble && requestId && shouldCreateCallBubbleForEvent(evt)) {
      ensureLiveCallSessionVisible();
      bubble = addCallAssistantBubble(requestId, "正在识别语音…", true);
    }
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
          touchBrowserCallActivity("intent_progress");
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
        if (data.content) {
          touchBrowserCallActivity("chat_message");
          appendTextToBubble(bubble, data.content);
        }
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
    if (evtName === "WakeWordDetected") return data.model ? `model=${data.model}` : "wake detected";
    if (evtName === "VoiceUserText") return data.user_text || "";
    if (evtName === "ConversationStateChanged") return data.state || "";
    if (evtName === "Esp32AudioRelayStatus") {
      const state = data.state || "";
      const frames = data.frames != null ? ` · frames=${data.frames}` : "";
      return `${state}${frames}`;
    }
    if (evtName === "Esp32AudioRelayError") return data.error || "";
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

  // --- event replay across browsers / process restarts ---
  const EVENT_SEQ_KEY = "lampgo.lastEventSeq";
  let lastEventSeq = (() => {
    try {
      const v = parseInt(localStorage.getItem(EVENT_SEQ_KEY) || "0", 10);
      return Number.isFinite(v) && v > 0 ? v : 0;
    } catch {
      return 0;
    }
  })();

  function bumpEventSeq(seq) {
    if (typeof seq !== "number" || !Number.isFinite(seq)) return;
    if (seq <= lastEventSeq) return;
    lastEventSeq = seq;
    try {
      localStorage.setItem(EVENT_SEQ_KEY, String(lastEventSeq));
    } catch {}
  }

  // How many most-recent events to always replay on a fresh page load, even
  // if localStorage says we've already seen them. Gives the user a continuous
  // session across reloads without re-rendering thousands of old events.
  const EVENT_REPLAY_WINDOW = 200;

  async function backfillEventsFromServer() {
    // Fetches events missed while this browser was away, plus the last
    // EVENT_REPLAY_WINDOW events as context after a hard reload. Live events
    // are persisted by the server into ~/.lampgo/events.log; we just paint
    // them into the DOM. Dedup against lastEventSeq handles the backfill-HTTP
    // vs live-WS race for anything strictly newer than the cursor.

    // Probe the server's current seq first. If it's strictly below our cached
    // lastEventSeq, the server's seq counter rolled back at some point (log
    // corruption, events.log wiped, dev reset, ...). Our old cursor would
    // filter out every new event as "already seen" and the UI event log would
    // look frozen. Reset the cursor in that case so we realign with the server.
    try {
      const probe = await fetch("/api/events?since=0&limit=1");
      if (probe.ok) {
        const probeBody = await probe.json();
        const serverLatest = probeBody && probeBody.result && probeBody.result.latest_seq;
        if (typeof serverLatest === "number" && serverLatest >= 0 && serverLatest < lastEventSeq) {
          console.warn("[events] server seq rollback detected:", {
            client_last: lastEventSeq,
            server_latest: serverLatest,
          });
          lastEventSeq = 0;
          try {
            localStorage.setItem(EVENT_SEQ_KEY, "0");
          } catch {}
        }
      }
    } catch {
      // Probe failure is non-fatal; fall through to regular backfill.
    }

    const replayFromScratch = !!eventLog && eventLog.childElementCount === 0;
    const cursor = replayFromScratch
      ? Math.max(0, lastEventSeq - EVENT_REPLAY_WINDOW)
      : lastEventSeq;

    const chunks = [];
    let paging = cursor;
    for (let round = 0; round < 10; round += 1) {
      let resp;
      try {
        resp = await fetch(`/api/events?since=${encodeURIComponent(paging)}&limit=500`);
      } catch (err) {
        console.warn("[events] backfill fetch failed:", err);
        return;
      }
      if (!resp.ok) return;
      let body;
      try {
        body = await resp.json();
      } catch {
        return;
      }
      const result = body && body.result;
      if (!result || !Array.isArray(result.events)) return;
      chunks.push(...result.events);
      // Advance the paging cursor past the events we just received. We can't
      // use result.latest_seq here because that's the absolute tail, not the
      // last seq we actually pulled — using it would skip events when the
      // server logs new ones while we page.
      const lastInChunk = result.events.length
        ? result.events[result.events.length - 1].seq
        : paging;
      if (typeof lastInChunk === "number" && lastInChunk > paging) {
        paging = lastInChunk;
      }
      if (!result.truncated) break;
    }
    for (const e of chunks) {
      if (!e || typeof e.seq !== "number") continue;
      // In normal catch-up mode we skip anything already seen; in replay mode
      // we intentionally render the window even if it's behind the cursor.
      if (!replayFromScratch && e.seq <= lastEventSeq) continue;
      try {
        logEvent(e, { allowReplay: replayFromScratch });
      } catch (err) {
        console.warn("[events] replay logEvent failed:", err);
      }
    }
  }

  function logEvent(msg, { allowReplay = false } = {}) {
    if (!eventLog || !msg || !msg.event) return;
    if (msg.event === "IntentProgress" && msg.data && NOISY_PROGRESS_STAGES.has(msg.data.stage)) {
      return; // skip high-frequency streaming chunks from the activity log
    }
    // Dedup when the same event arrives twice (backfill HTTP + live WS race).
    // Replay mode (page reload) explicitly asks to render history *behind*
    // the already-seen cursor, so we skip this guard but still bump the
    // cursor forward for events that are actually newer than it.
    if (typeof msg.seq === "number") {
      if (!allowReplay && msg.seq <= lastEventSeq) return;
      bumpEventSeq(msg.seq);
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
    eventLog.insertBefore(item, eventLog.firstChild);

    while (eventLog.childElementCount > EVENT_LOG_MAX) {
      eventLog.removeChild(eventLog.lastElementChild);
    }
    eventLog.scrollTop = 0;
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

    if (data.conversation_state != null && !lkRoom) {
      updateCallViewState(data.conversation_state);
    }
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
  let latestUserSkills = [];
  let latestRecordings = [];
  let latestExpressions = [];
  let skillQuery = "";
  let userSkillQuery = "";
  let recordingQuery = "";
  let expressionQuery = "";
  const FACTORY_SKILL_VISIBLE_IDS = new Set(["return_safe", "idle_sway"]);

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

  function normalizeRecordingEntry(entry) {
    if (typeof entry === "string") {
      return { name: entry.trim(), source: "builtin", description: "" };
    }
    if (!entry || typeof entry !== "object") return { name: "", source: "builtin", description: "" };
    const name = String(entry.name || "").trim();
    const source = String(entry.source || "builtin").trim() || "builtin";
    const description = String(entry.description || "").trim();
    return { name, source, description };
  }

  function renderSkills(skills) {
    if (Array.isArray(skills)) {
      // Split by provenance so the UI can show factory (locked) vs user
      // (editable) separately; ``source`` is set by the server — default
      // to "factory" for any skill that pre-dates the feature flag, so
      // older daemons stay backwards-compatible on UI refresh.
      const visible = skills.filter(
        (s) => !["estop", "play_recording"].includes(s.skill_id),
      );
      latestSkills = visible.filter(
        (s) => (s.source || "factory") === "factory" && FACTORY_SKILL_VISIBLE_IDS.has(s.skill_id),
      );
      latestUserSkills = visible.filter((s) => s.source === "user");
    }
    renderFactorySkills();
    renderUserSkills();
  }

  function renderFactorySkills() {
    skillGrid.innerHTML = "";
    const q = skillQuery.trim().toLowerCase();
    const filtered = q
      ? latestSkills.filter((s) => {
          const label = skillLabel(s);
          return (
            (s.skill_id || "").toLowerCase().includes(q) ||
            (s.description || "").toLowerCase().includes(q) ||
            label.title.toLowerCase().includes(q) ||
            label.description.toLowerCase().includes(q)
          );
        })
      : latestSkills;
    filtered.forEach((skill) => {
      const label = skillLabel(skill);
      const card = makeSkillCard({
        title: label.title,
        meta: label.description,
        tooltip: `${label.description}（${skill.skill_id}）`,
        onClick: () => invokeSkill(skill.skill_id),
      });
      skillGrid.appendChild(card);
    });
    if (!filtered.length) renderEmptyCell(skillGrid, q ? `无匹配「${q}」的技能` : "暂无技能");
    updateCount(skillCountEl, filtered.length, latestSkills.length);
  }

  function renderUserSkills() {
    if (!userSkillGrid) return;
    userSkillGrid.innerHTML = "";
    const q = userSkillQuery.trim().toLowerCase();
    const filtered = q
      ? latestUserSkills.filter((s) => {
          const title = s.label || s.skill_id || "";
          return (
            title.toLowerCase().includes(q) ||
            (s.skill_id || "").toLowerCase().includes(q) ||
            (s.description || "").toLowerCase().includes(q)
          );
        })
      : latestUserSkills;

    filtered.forEach((skill) => {
      const title = skill.label || skill.skill_id;
      const steps = Array.isArray(skill.steps) ? skill.steps : [];
      const stepCount = steps.length;
      // Count trajectory steps separately — they're the Level 2 feature and
      // worth flagging so the user knows the skill includes a custom motion,
      // not just a chain of built-ins.
      const trajCount = steps.filter((s) => s && s.trajectory).length;
      const metaParts = [];
      if (stepCount) metaParts.push(`${stepCount} 步`);
      if (trajCount) metaParts.push(`含 ${trajCount} 段轨迹`);
      const meta = skill.description || metaParts.join(" · ");

      // Plan summary — one line per step.  Factory steps show the target
      // skill_id; trajectory steps show "[轨迹: N 个关键帧]" so the reader
      // can tell the two shapes apart without opening the JSON.
      const planPreview = steps
        .slice(0, 4)
        .map((s) => {
          if (s && s.trajectory) {
            const wpCount = Array.isArray(s.trajectory.waypoints)
              ? s.trajectory.waypoints.length
              : 0;
            return `[轨迹:${wpCount}帧]`;
          }
          return s && s.skill_id ? s.skill_id : "?";
        })
        .join(" → ") + (stepCount > 4 ? " → …" : "");
      const tooltip = `${skill.description || ""}\n流程：${planPreview}\n(${skill.skill_id})`;

      const card = makeSkillCard({
        title,
        meta,
        tooltip,
        onClick: () => invokeSkill(skill.skill_id),
      });
      card.classList.add("skill-card--user");

      // Overlay a delete button in the top-right corner; stopPropagation
      // so clicking it never also fires the card's invoke handler.
      const del = document.createElement("button");
      del.className = "skill-card-action skill-card-action--delete";
      del.type = "button";
      del.title = "删除这个技能";
      del.textContent = "✕";
      del.addEventListener("click", (ev) => {
        ev.stopPropagation();
        ev.preventDefault();
        void deleteUserSkill(skill);
      });
      card.appendChild(del);
      userSkillGrid.appendChild(card);
    });

    if (!filtered.length && !q) {
      // Empty state: show the friendly "让 AI 帮你攒一个" hint instead of
      // the generic "无技能" so users know this grid is *meant* to start
      // empty and fill up over time.
      if (userSkillEmpty) userSkillEmpty.classList.remove("hidden");
    } else {
      if (userSkillEmpty) userSkillEmpty.classList.add("hidden");
      if (!filtered.length) {
        renderEmptyCell(userSkillGrid, `无匹配「${q}」的技能`);
      }
    }
    updateCount(userSkillCountEl, filtered.length, latestUserSkills.length);
  }

  async function deleteUserSkill(skill) {
    const title = skill.label || skill.skill_id;
    if (!window.confirm(`确认删除"${title}"（skill_id=${skill.skill_id}）？此操作不可恢复。`)) {
      return;
    }
    try {
      const resp = await fetch("/api/skills/delete", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ skill_id: skill.skill_id }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) {
        window.alert(`删除失败：${data.error || resp.statusText}`);
        return;
      }
      await refreshSkillsAndRecordings();
    } catch (err) {
      window.alert(`删除失败：${err && err.message ? err.message : err}`);
    }
  }

  async function reloadUserSkills() {
    try {
      const resp = await fetch("/api/skills/reload", { method: "POST" });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) {
        window.alert(`重新加载失败：${data.error || resp.statusText}`);
        return;
      }
      const errs = (data.result && data.result.errors) || [];
      if (errs.length) {
        const preview = errs.slice(0, 3).map(([p, m]) => `• ${p}: ${m}`).join("\n");
        window.alert(`重新加载完成，但 ${errs.length} 个文件有问题：\n${preview}`);
      }
      await refreshSkillsAndRecordings();
    } catch (err) {
      window.alert(`重新加载失败：${err && err.message ? err.message : err}`);
    }
  }

  async function refreshSkillsAndRecordings() {
    // Delete/reload paths don't emit events today — pull /api/skills once
    // directly so the grid reflects disk truth without waiting for the
    // next status broadcast.
    try {
      const resp = await fetch("/api/skills", { cache: "no-store" });
      const data = await resp.json().catch(() => ({}));
      if (data && data.ok && data.result && Array.isArray(data.result.skills)) {
        renderSkills(data.result.skills);
      }
    } catch (err) {
      // Non-fatal: grid will catch up on the next websocket status tick.
      console.warn("refreshSkillsAndRecordings failed", err);
    }
    try {
      const resp = await fetch("/api/recordings", { cache: "no-store" });
      const data = await resp.json().catch(() => ({}));
      if (data && data.ok && data.result && Array.isArray(data.result.recordings)) {
        renderRecordings(data.result.recordings);
      }
    } catch (err) {
      console.warn("refreshRecordings failed", err);
    }
  }

  function renderRecordings(recordings) {
    if (Array.isArray(recordings)) {
      latestRecordings = recordings.map(normalizeRecordingEntry).filter((r) => r.name);
    }
    recordingGrid.innerHTML = "";
    const q = recordingQuery.trim().toLowerCase();
    const filtered = q
      ? latestRecordings.filter((recording) => {
          const name = recording.name;
          const expr = getRecordingExpression(name) || "";
          const labelCn = recordingLabel(name);
          const exprCn = expressionLabel(expr);
          const description = recording.description || "";
          return (
            name.toLowerCase().includes(q) ||
            expr.toLowerCase().includes(q) ||
            labelCn.toLowerCase().includes(q) ||
            exprCn.toLowerCase().includes(q) ||
            description.toLowerCase().includes(q)
          );
        })
      : latestRecordings;
    filtered.forEach((recording) => {
      const name = recording.name;
      const expression = getRecordingExpression(name);
      const labelCn = recordingLabel(name);
      const exprCn = expressionLabel(expression);
      const isUserRecording = recording.source === "user";
      const description = recording.description || "";
      const card = makeSkillCard({
        title: labelCn,
        meta: description
          ? `${isUserRecording ? "我的录制" : "内置动作"} · ${description}`
          : `${isUserRecording ? "我的录制" : "内置动作"} · ${exprCn}`,
        tooltip: description
          ? `播放录制动作：${labelCn}（${name}） · ${description}`
          : `播放录制动作：${labelCn}（${name}） · 推荐表情：${exprCn}`,
        onClick: () => invokeRecording(name),
      });
      if (isUserRecording) {
        card.classList.add("skill-card--recording-user");
        const del = document.createElement("button");
        del.className = "skill-card-action skill-card-action--delete";
        del.type = "button";
        del.title = "删除这个录制动作";
        del.textContent = "✕";
        del.addEventListener("click", (ev) => {
          ev.stopPropagation();
          ev.preventDefault();
          void deleteRecording(recording);
        });
        card.appendChild(del);
      }
      recordingGrid.appendChild(card);
    });
    if (!filtered.length) renderEmptyCell(recordingGrid, q ? `无匹配「${q}」的录制动作` : "暂无录制动作");
    updateCount(recordingCountEl, filtered.length, latestRecordings.length);
  }

  async function deleteRecording(recording) {
    const name = recording && recording.name ? recording.name : "";
    if (!name) return;
    const title = recordingLabel(name);
    if (!window.confirm(`确认删除录制动作"${title}"（${name}）？此操作不可恢复。`)) {
      return;
    }
    try {
      const resp = await fetch("/api/recordings/delete", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ name }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok || !data.ok) {
        window.alert(`删除失败：${data.error || resp.statusText}`);
        return;
      }
      if (data.result && Array.isArray(data.result.recordings)) {
        renderRecordings(data.result.recordings);
      } else {
        await refreshSkillsAndRecordings();
      }
      const removedAliases = data.result && Array.isArray(data.result.removed_aliases)
        ? data.result.removed_aliases
        : [];
      addSystemMessage(removedAliases.length
        ? `录制动作已删除：${title}（同时移除 ${removedAliases.length} 个别名）`
        : `录制动作已删除：${title}`);
    } catch (err) {
      window.alert(`删除失败：${err && err.message ? err.message : err}`);
    }
  }

  function renderExpressions(expressions) {
    if (Array.isArray(expressions)) latestExpressions = normalizeExpressionEntries(expressions);
    expressionGrid.innerHTML = "";
    const q = expressionQuery.trim().toLowerCase();
    const filtered = q
      ? latestExpressions.filter((name) => {
          const meta = expressionMeta(name);
          const labelCn = expressionLabel(name);
          return (
            name.toLowerCase().includes(q) ||
            labelCn.includes(q)
          );
        })
      : latestExpressions;
    filtered.forEach((name) => {
      const meta = expressionMeta(name);
      const labelCn = expressionLabel(name);
      const metaParts = ["LED 表情"];
      if (meta && meta.mode !== null && meta.mode !== undefined) metaParts.push(`m${meta.mode}`);
      if (meta) metaParts.push(meta.animated ? "动态" : "静态");
      const card = makeSkillCard({
        title: labelCn,
        meta: metaParts.join(" · "),
        tooltip: `切换灯光表情：${labelCn}（${name}）`,
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

  wireSkillSearch(skillSearchEl, (v) => { skillQuery = v; renderFactorySkills(); });
  wireSkillSearch(userSkillSearchEl, (v) => { userSkillQuery = v; renderUserSkills(); });
  wireSkillSearch(recordingSearchEl, (v) => { recordingQuery = v; renderRecordings(); });
  wireSkillSearch(expressionSearchEl, (v) => { expressionQuery = v; renderExpressions(); });
  if (btnUserSkillsReload) {
    btnUserSkillsReload.addEventListener("click", () => { void reloadUserSkills(); });
  }

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

  async function invokeSkillViaHttp(skillId, params, requestId) {
    const resp = await fetch("/api/invoke", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ skill_id: skillId, params: params || {}, wait: true, request_id: requestId }),
    });
    const data = await resp.json().catch(() => ({
      ok: false,
      error: resp.ok ? "invalid_json_response" : resp.statusText,
    }));
    data.request_id = requestId;
    if (!resp.ok && data.ok !== false) {
      data.ok = false;
      data.error = data.error || resp.statusText || `HTTP ${resp.status}`;
    }
    return data;
  }

  function invokeRecording(name) {
    clearEmptyState();
    const requestId = nextId();
    const bubble = addAssistantBubble(requestId);
    const expression = getRecordingExpression(name);
    const labelCn = recordingLabel(name);
    const exprCn = expressionLabel(expression);
    addStep(
      getPreludeArea(ensureActivityLog(bubble)),
      `播放录制动作 ${labelCn}（${name}） · 模式 ${playbackModeLabel(playbackMode)} · 表情 ${exprCn}`,
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

  async function invokeExpression(name) {
    clearEmptyState();
    const requestId = nextId();
    const bubble = addAssistantBubble(requestId);
    addStep(getPreludeArea(ensureActivityLog(bubble)), `切换灯光表情 ${expressionLabel(name)}（${name}）`, "active");
    try {
      const msg = await invokeSkillViaHttp("set_expression", { expression: name }, requestId);
      finishPending(msg);
      if (msg.ok) send({ type: "status", request_id: `led_status_${Date.now()}` });
    } catch (err) {
      finishPending({
        ok: false,
        error: err && err.message ? err.message : String(err),
        result: {},
        request_id: requestId,
      });
    }
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
    if (recordDescriptionInput) recordDescriptionInput.value = "";
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

  function saveMotionRecording(name, description, overwrite = false) {
    send({ type: "recording_save", name, description, overwrite, request_id: nextId() });
  }

  function discardMotionRecording() {
    send({ type: "recording_discard", request_id: nextId() });
  }

  /* ---- Chat form ---- */

  // Build the recent conversation history for the backend, honoring the
  // user-configured `history_turns` (one turn = one user+assistant exchange).
  // We take the last `2*turns` real messages from the active session, keep
  // only role=user|assistant with non-empty text, strip the placeholder user
  // entry the frontend pushes for in-flight voice input, and emit the plain
  // OpenAI-compatible {role, content} shape the LLM expects.
  function buildChatHistoryForLlm() {
    const turns = Math.max(0, Number(settingsHistoryTurns) || 0);
    if (turns === 0) return [];
    const session = getActiveSession();
    if (!session || !Array.isArray(session.messages)) return [];
    const maxMessages = turns * 2;
    const out = [];
    for (const m of session.messages) {
      if (!m || (m.role !== "user" && m.role !== "assistant")) continue;
      const text = typeof m.text === "string" ? m.text.trim() : "";
      if (!text) continue;
      // Skip the transient voice placeholder ("[语音消息]") that hasn't been
      // back-filled with the real transcript yet — sending it would teach the
      // model a meaningless literal phrase.
      if (m.role === "user" && m.meta && m.meta.voice && !m.meta.voice_transcribed) continue;
      out.push({ role: m.role, content: text });
    }
    // Tail-trim to last N messages; this may leave an assistant-led slice but
    // most providers tolerate that just fine.
    return out.length > maxMessages ? out.slice(-maxMessages) : out;
  }

  chatForm.addEventListener("submit", (e) => {
    e.preventDefault();
    void unlockTtsPlayback();

    if (isVoiceMode) {
      if (esp32Recording) {
        stopEsp32VoiceMode();
        return;
      }
      if (mediaRecorder && mediaRecorder.state !== "inactive") mediaRecorder.stop();
      stopVoiceMode();
      return;
    }

    const text = chatInput.value.trim();
    if (!text) return;

    // Snapshot history BEFORE pushing the new user message into the session,
    // otherwise the current turn would duplicate itself as the last history entry.
    const history = buildChatHistoryForLlm();

    clearEmptyState();
    chatInput.value = "";
    addUserBubble(text);
    pushMessageToSession("user", text);

    const requestId = nextId();
    addAssistantBubble(requestId);
    send({
      type: "text",
      input: text,
      request_id: requestId,
      history,
    });
  });

  const VOICE_DOTS_HTML = '<span class="voice-dots"><span class="dot"></span><span class="dot"></span><span class="dot"></span></span>';

  function clearAssistantLoadingDots(bubble) {
    if (!bubble) return;
    bubble.querySelectorAll(".assistant-loading-dots").forEach((el) => el.remove());
  }

  function showAssistantLoadingDots(bubble) {
    if (!bubble) return;
    const el = bubble.querySelector(".response-text");
    if (!el || el.querySelector(".assistant-loading-dots")) return;
    const dots = document.createElement("span");
    dots.className = "assistant-loading-dots";
    dots.innerHTML = VOICE_DOTS_HTML;
    el.appendChild(dots);
  }

  function addUserBubble(text, requestId) {
    const isVoice = text === "[语音消息]";
    const row = document.createElement("div");
    row.className = "flex justify-end mb-4";
    const content = isVoice ? VOICE_DOTS_HTML : esc(text);
    row.innerHTML = `<div class="msg-bubble-wrap"><div class="msg-user">${content}</div><span class="msg-time">${formatTime()}</span></div>`;
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

    const isPreempted = !!(result.preempted);
    const isUserCancelled = result.stop_reason === "user_cancelled";
    const isCancelled = isPreempted || isUserCancelled;
    const isCallBubble = bubble.dataset.callView === "true";
    if (isPreempted && isCallBubble) callPreemptedAwaitingResponse = true;

    if (log) finalizeActivity(log, { cancelled: isCancelled });
    clearAssistantLoadingDots(bubble);

    if (text) {
      appendTextToBubble(bubble, text);
    }

    if (isPreempted) {
      const tag = document.createElement("span");
      tag.className = "preempted-tag";
      tag.textContent = "已中止";
      bubble.appendChild(tag);
    }

    if (isCallBubble) {
      const replyText = text || (isPreempted ? "[已中止]" : "");
      if (replyText) {
        const meta = {};
        if (isPreempted) meta.preempted = true;
        if (result.end_conversation) meta.end_conversation = true;
        if (ocTask) {
          meta.openclaw_task_id = ocTask.task_id;
          meta.openclaw_user_text = ocTask.user_text || "";
          meta.openclaw_status = ocTask.status || "";
        }
        const activityHtml = captureActivityHtml(bubble);
        if (activityHtml) meta.activity_html = activityHtml;
        pushCallMessage(
          "assistant",
          replyText,
          Object.keys(meta).length ? meta : undefined,
        );
      }
      // Hang up only after the goodbye TTS has actually played out (or after a
      // generous fallback timeout). 2.5s used to be hard-coded which cut the
      // farewell mid-sentence; instead watch the remote track's energy.
      if (result.end_conversation && lkRoom && !hangupPending) {
        hangupPending = true;
        hangupCancelled = false;
        scheduleHangupAfterTtsPlayout().finally(() => {
          hangupPending = false;
        });
      }
    } else {
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
    }

    pendingMessages.delete(msg.request_id);
    pendingUserMessages.delete(msg.request_id);
    if (isCallBubble) scrollCallMessages();
    else scrollChat();
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
    clearAssistantLoadingDots(bubble);
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

  function finalizeActivity(log, opts) {
    if (!log || log.dataset.finalized === "1") return;
    log.dataset.finalized = "1";
    const cancelled = !!(opts && opts.cancelled);
    // 兜底：把 .route-trail 里残留的 is-active 节点降级。
    // 正常链路里 IntentResolved 会调 setRouteTrail(buildResolvedTrail(...)) 完成降级，
    // 但某些分支（错误退出 / OpenClaw handoff 中断 / IntentResolved 丢失）拿不到这个事件，
    // 结果 .route-node.is-active 的 route-pulse 动画会在对话结束后继续闪。
    // 如果对话是被抢占或用户手动停止的，节点降级为 is-cancelled（灰色 ✕，表示未完成）；
    // 否则视为已成功收尾，降级为 is-done（绿色 ✓）。
    log.querySelectorAll(".route-node.is-active").forEach((el) => {
      el.classList.remove("is-active");
      el.classList.add(cancelled ? "is-cancelled" : "is-done");
    });
    // Drop placeholder turn cards that never produced any tool/thinking content.
    // The agent loop opens a new turn via `IntentProgress(llm_request)` before
    // calling the model; if that turn gets preempted by a new user utterance
    // (CancelledError on the in-flight chat/completions request) the card
    // stays empty. Removing them keeps the activity log honest about what
    // actually ran.
    log.querySelectorAll(".turn-card").forEach((card) => {
      const body = card.querySelector(".turn-body");
      const hasBody = body && body.children.length > 0;
      const hasTools = (card.dataset.tools || "").trim().length > 0;
      if (!hasBody && !hasTools) card.remove();
    });
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
    if (confirm("确认强行停止？将立即切断电机力矩，终止一切动作。")) {
      send({ type: "estop" });
      addSystemMessage("已发送强行停止命令");
    }
  });

  /* ── Call view ── */
  const callMessages = document.getElementById("call-messages");
  const callEmptyState = document.getElementById("call-empty-state");
  const callDot = document.getElementById("call-dot");
  const callStatusText = document.getElementById("call-status-text");
  const btnCallStart = document.getElementById("btn-call-start");
  const btnCallEnd = document.getElementById("btn-call-end");

  let callSessionId = null;
  let prevCallState = "idle";
  const callUserRequestIds = new Set();
  let lkRoom = null;
  let lkLocalTrack = null;
  let lkModulePromise = null;
  const lkAudioEls = new Set();
  const CALL_LOCK_KEY = "lampgo.livekitCallOwner";
  const CALL_LOCK_TTL_MS = 45000;
  const CALL_LOCK_REFRESH_MS = 10000;
  const CALL_WAKE_FRESH_MS = 15000;
  const CALL_IDLE_TIMEOUT_MS = 5 * 60 * 1000;
  const CALL_AUDIO_ACTIVITY_THROTTLE_MS = 1000;
  const browserCallClientId = (() => {
    try {
      let id = sessionStorage.getItem("lampgo.browserCallClientId");
      if (!id) {
        id = `web_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 9)}`;
        sessionStorage.setItem("lampgo.browserCallClientId", id);
      }
      return id;
    } catch {
      return `web_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 9)}`;
    }
  })();
  let browserCallStartPromise = null;
  let browserCallPendingId = "";
  let browserCallLockTimer = null;
  let browserCallIdleTimer = null;
  let browserCallLastActivityAt = 0;
  let browserCallLastAudioActivityAt = 0;
  let lkRoomName = "";
  let lkClientCallId = "";

  let esp32AudioCtx = null;
  let esp32WorkletNode = null;
  let esp32MediaStream = null;
  let esp32RelayActive = false;
  let esp32MicRelayStats = null;
  let esp32SpeakerWs = null;
  let esp32SpeakerWsOpening = null;
  let esp32SpeakerPendingFrames = [];
  let esp32SpeakerPcmRemainder = new Int16Array(0);
  let esp32SpeakerCtx = null;
  let esp32SpeakerSource = null;
  let esp32SpeakerProcessor = null;
  let esp32SpeakerKeepaliveEl = null;
  let esp32SpeakerRelayStats = null;
  let remoteAudioTrack = null;
  let hangupPending = false;
  let hangupCancelled = false;

  const ESP32_WORKLET_CODE = `
class Esp32PcmProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._buffer = new Float32Array(0);
    this.port.onmessage = (e) => {
      const incoming = e.data;
      const merged = new Float32Array(this._buffer.length + incoming.length);
      merged.set(this._buffer);
      merged.set(incoming, this._buffer.length);
      this._buffer = merged;
    };
  }
  process(inputs, outputs) {
    const out = outputs[0][0];
    if (!out) return true;
    const needed = out.length;
    if (this._buffer.length >= needed) {
      out.set(this._buffer.subarray(0, needed));
      this._buffer = this._buffer.subarray(needed);
    } else {
      out.set(this._buffer);
      out.fill(0, this._buffer.length);
      this._buffer = new Float32Array(0);
    }
    return true;
  }
}
registerProcessor("esp32-pcm-processor", Esp32PcmProcessor);
`;

  function setBrowserCallState(state) {
    updateCallViewState(state);
  }

  function clearBrowserCallIdleTimer() {
    if (browserCallIdleTimer) {
      window.clearTimeout(browserCallIdleTimer);
      browserCallIdleTimer = null;
    }
  }

  function scheduleBrowserCallIdleTimer() {
    clearBrowserCallIdleTimer();
    if (!lkRoom && !browserCallStartPromise) return;
    const last = browserCallLastActivityAt || Date.now();
    const waitMs = Math.max(1000, CALL_IDLE_TIMEOUT_MS - (Date.now() - last));
    browserCallIdleTimer = window.setTimeout(() => {
      browserCallIdleTimer = null;
      if (!lkRoom) return;
      const idleMs = Date.now() - (browserCallLastActivityAt || Date.now());
      if (idleMs >= CALL_IDLE_TIMEOUT_MS) {
        console.info("[call] idle timeout; closing LiveKit room", { idleMs });
        addCallSystemNote("5 分钟没有输入或输出，已自动结束通话");
        stopBrowserLiveKitCall("idle_timeout");
      } else {
        scheduleBrowserCallIdleTimer();
      }
    }, waitMs);
  }

  function touchBrowserCallActivity(source, options = {}) {
    if (!lkRoom && !browserCallStartPromise) return;
    const now = Date.now();
    if (options.audio) {
      const throttleMs = Number(options.throttleMs || CALL_AUDIO_ACTIVITY_THROTTLE_MS);
      if (now - browserCallLastAudioActivityAt < throttleMs) return;
      browserCallLastAudioActivityAt = now;
    }
    browserCallLastActivityAt = now;
    scheduleBrowserCallIdleTimer();
  }

  function readBrowserCallLock() {
    try {
      const raw = localStorage.getItem(CALL_LOCK_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  }

  function refreshBrowserCallLock(reason) {
    try {
      const now = Date.now();
      const current = readBrowserCallLock();
      const expiresAt = Number(current && current.expiresAt ? current.expiresAt : 0);
      if (current && current.owner && current.owner !== browserCallClientId && expiresAt > now) {
        return false;
      }
      localStorage.setItem(CALL_LOCK_KEY, JSON.stringify({
        owner: browserCallClientId,
        reason: reason || "call",
        updatedAt: now,
        expiresAt: now + CALL_LOCK_TTL_MS,
      }));
      const next = readBrowserCallLock();
      return !next || !next.owner || next.owner === browserCallClientId;
    } catch (err) {
      console.warn("[call] call owner lock unavailable:", err);
      return true;
    }
  }

  function acquireBrowserCallLock(reason) {
    if (!refreshBrowserCallLock(reason)) return false;
    if (browserCallLockTimer) clearInterval(browserCallLockTimer);
    browserCallLockTimer = window.setInterval(() => {
      if (!refreshBrowserCallLock(reason || "call")) {
        console.warn("[call] lost call owner lock; stopping local call");
        if (lkRoom || browserCallStartPromise) stopBrowserLiveKitCall();
      }
    }, CALL_LOCK_REFRESH_MS);
    return true;
  }

  function releaseBrowserCallLock() {
    if (browserCallLockTimer) {
      clearInterval(browserCallLockTimer);
      browserCallLockTimer = null;
    }
    try {
      const current = readBrowserCallLock();
      if (current && current.owner === browserCallClientId) {
        localStorage.removeItem(CALL_LOCK_KEY);
      }
    } catch {
      // Best effort only.
    }
  }

  function isFreshLiveEvent(msg, maxAgeMs) {
    const ts = Number(msg && msg.ts ? msg.ts : 0);
    if (!ts || !Number.isFinite(ts)) return true;
    const eventMs = ts > 1000000000000 ? ts : ts * 1000;
    return Date.now() - eventMs <= maxAgeMs;
  }

  async function loadLiveKitClient() {
    if (!lkModulePromise) {
      lkModulePromise = import("https://cdn.jsdelivr.net/npm/livekit-client/dist/livekit-client.esm.mjs");
    }
    return lkModulePromise;
  }

  function getEsp32WsUrl(path, portOffset) {
    const esp = cameraCache && cameraCache.esp32 ? cameraCache.esp32 : null;
    let host = (esp && (esp.ip || esp.host)) || "";
    host = String(host).replace(/^https?:\/\//, "").replace(/^wss?:\/\//, "").replace(/\/.*$/, "");
    if (!host) throw new Error("ESP32 设备未发现或离线");
    const port = Number((esp && esp.port) || 80) + (portOffset || 0);
    const needsPort = !/:\d+$/.test(host);
    return `ws://${host}${needsPort ? `:${port}` : ""}${path}`;
  }

  function floatToPcm16(samples) {
    const out = new Int16Array(samples.length);
    for (let i = 0; i < samples.length; i++) {
      const s = Math.max(-1, Math.min(1, samples[i]));
      out[i] = s < 0 ? s * 32768 : s * 32767;
    }
    return out;
  }

  function downsampleFloat32(input, inputRate, outputRate) {
    if (inputRate === outputRate) return input;
    const ratio = inputRate / outputRate;
    const outLen = Math.max(1, Math.floor(input.length / ratio));
    const out = new Float32Array(outLen);
    for (let i = 0; i < outLen; i++) {
      const start = Math.floor(i * ratio);
      const end = Math.min(input.length, Math.floor((i + 1) * ratio));
      let sum = 0;
      let count = 0;
      for (let j = start; j < end; j++) {
        sum += input[j];
        count += 1;
      }
      out[i] = count ? sum / count : 0;
    }
    return out;
  }

  function resampleFloat32(input, inputRate, outputRate) {
    if (!input || input.length === 0 || inputRate === outputRate) return input;
    const ratio = outputRate / inputRate;
    const outLen = Math.max(1, Math.round(input.length * ratio));
    const out = new Float32Array(outLen);
    for (let i = 0; i < outLen; i++) {
      const srcPos = i / ratio;
      const left = Math.floor(srcPos);
      const right = Math.min(input.length - 1, left + 1);
      const frac = srcPos - left;
      out[i] = input[left] + (input[right] - input[left]) * frac;
    }
    return out;
  }

  const ESP32_CALL_SPEAKER_SAMPLE_RATE = 16000;
  const ESP32_CALL_SPEAKER_FRAME_SAMPLES = 320; // 20 ms @ 16 kHz.
  const ESP32_CALL_SPEAKER_MAX_PENDING_FRAMES = 80;
  const ESP32_CALL_SPEAKER_MAX_BUFFERED_BYTES = ESP32_CALL_SPEAKER_FRAME_SAMPLES * 2 * 24;
  const ESP32_CALL_SPEAKER_FULL_SCALE_PEAK = 0.82;

  function openEsp32CallSpeakerWs() {
    if (esp32SpeakerWs && esp32SpeakerWs.readyState === WebSocket.OPEN) {
      return Promise.resolve(esp32SpeakerWs);
    }
    if (esp32SpeakerWsOpening) return esp32SpeakerWsOpening;
    if (esp32SpeakerWs) {
      try { esp32SpeakerWs.close(); } catch (_) { /* ignore */ }
      esp32SpeakerWs = null;
    }

    const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
    const speakerWs = new WebSocket(`${scheme}//${window.location.host}/api/device/speaker`);
    speakerWs.binaryType = "arraybuffer";
    esp32SpeakerWs = speakerWs;

    esp32SpeakerWsOpening = new Promise((resolve, reject) => {
      let settled = false;
      const timeout = window.setTimeout(() => {
        if (settled) return;
        settled = true;
        if (esp32SpeakerWs === speakerWs) esp32SpeakerWs = null;
        esp32SpeakerWsOpening = null;
        try { speakerWs.close(); } catch (_) { /* ignore */ }
        reject(new Error("ESP32 speaker WS 连接超时"));
      }, 2000);

      const fail = (message) => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timeout);
        if (esp32SpeakerWs === speakerWs) esp32SpeakerWs = null;
        esp32SpeakerWsOpening = null;
        reject(new Error(message));
      };

      speakerWs.onopen = () => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timeout);
        esp32SpeakerWsOpening = null;
        flushEsp32SpeakerPendingFrames();
        resolve(speakerWs);
      };
      speakerWs.onerror = () => fail("ESP32 speaker WS 连接失败");
      speakerWs.onclose = () => {
        if (esp32SpeakerWs === speakerWs) esp32SpeakerWs = null;
        fail("ESP32 speaker WS 已断开");
      };
    });

    return esp32SpeakerWsOpening;
  }

  function closeEsp32CallSpeakerWs(clearPending = false) {
    if (esp32SpeakerWs) {
      try { esp32SpeakerWs.close(); } catch (_) { /* ignore */ }
      esp32SpeakerWs = null;
    }
    esp32SpeakerWsOpening = null;
    if (clearPending) esp32SpeakerPendingFrames = [];
  }

  function flushEsp32SpeakerPendingFrames() {
    if (!esp32SpeakerWs || esp32SpeakerWs.readyState !== WebSocket.OPEN) return;
    const pending = esp32SpeakerPendingFrames.splice(0);
    for (let i = 0; i < pending.length; i++) {
      const frame = pending[i];
      if (!esp32SpeakerWs || esp32SpeakerWs.readyState !== WebSocket.OPEN) {
        esp32SpeakerPendingFrames = pending.slice(i).concat(esp32SpeakerPendingFrames);
        break;
      }
      try {
        esp32SpeakerWs.send(frame);
      } catch (err) {
        console.warn("[call] ESP32 speaker WS send failed:", err);
        esp32SpeakerPendingFrames = pending.slice(i).concat(esp32SpeakerPendingFrames);
        closeEsp32CallSpeakerWs(false);
        reopenEsp32CallSpeakerWs();
        break;
      }
    }
  }

  function sendOrQueueEsp32SpeakerFrame(frame) {
    if (esp32SpeakerWs && esp32SpeakerWs.readyState === WebSocket.OPEN) {
      if (esp32SpeakerWs.bufferedAmount > ESP32_CALL_SPEAKER_MAX_BUFFERED_BYTES) {
        return;
      }
      try {
        esp32SpeakerWs.send(frame);
        return;
      } catch (err) {
        console.warn("[call] ESP32 speaker WS send failed:", err);
        closeEsp32CallSpeakerWs(false);
      }
    }

    esp32SpeakerPendingFrames.push(frame);
    while (esp32SpeakerPendingFrames.length > ESP32_CALL_SPEAKER_MAX_PENDING_FRAMES) {
      esp32SpeakerPendingFrames.shift();
    }
    if (!esp32SpeakerWsOpening) {
      openEsp32CallSpeakerWs().catch((err) => {
        console.warn("[call] ESP32 speaker WS open failed:", err);
        esp32SpeakerPendingFrames = [];
      });
    }
  }

  function reopenEsp32CallSpeakerWs() {
    if (esp32SpeakerWsOpening) return;
    openEsp32CallSpeakerWs().catch((err) => {
      console.warn("[call] ESP32 speaker WS reopen failed:", err);
      esp32SpeakerPendingFrames = [];
    });
  }

  function flushEsp32SpeakerPcmRemainder() {
    if (!esp32SpeakerPcmRemainder.length) return;
    const frame = esp32SpeakerPcmRemainder;
    esp32SpeakerPcmRemainder = new Int16Array(0);
    sendOrQueueEsp32SpeakerFrame(frame.buffer.slice(frame.byteOffset, frame.byteOffset + frame.byteLength));
  }

  function enqueueEsp32SpeakerPcm(pcm16) {
    if (!pcm16 || !pcm16.length) return;
    const merged = new Int16Array(esp32SpeakerPcmRemainder.length + pcm16.length);
    merged.set(esp32SpeakerPcmRemainder);
    merged.set(pcm16, esp32SpeakerPcmRemainder.length);

    let offset = 0;
    while (offset + ESP32_CALL_SPEAKER_FRAME_SAMPLES <= merged.length) {
      const frame = merged.subarray(offset, offset + ESP32_CALL_SPEAKER_FRAME_SAMPLES);
      sendOrQueueEsp32SpeakerFrame(frame.buffer.slice(frame.byteOffset, frame.byteOffset + frame.byteLength));
      offset += ESP32_CALL_SPEAKER_FRAME_SAMPLES;
    }
    esp32SpeakerPcmRemainder = merged.subarray(offset);
  }

  function sendEsp32SpeakerSamples(samples, inputRate) {
    if (!samples || !samples.length) return;
    const resampled = downsampleFloat32(samples, inputRate, ESP32_CALL_SPEAKER_SAMPLE_RATE);
    let sumSq = 0;
    let peak = 0;
    for (let i = 0; i < resampled.length; i++) {
      const v = resampled[i];
      sumSq += v * v;
      const abs = Math.abs(v);
      if (abs > peak) peak = abs;
    }
    const rms = Math.sqrt(sumSq / resampled.length);
    const volumePct = esp32VolumeSlider ? clampEsp32Volume(esp32VolumeSlider.value) : 70;
    const targetPeak = ESP32_CALL_SPEAKER_FULL_SCALE_PEAK * (volumePct / 100);
    const adaptiveGain = peak > targetPeak && peak > 0 ? targetPeak / peak : 1.0;
    if (esp32SpeakerRelayStats) {
      esp32SpeakerRelayStats.frames += 1;
      if (rms > 0.01) {
        esp32SpeakerRelayStats.voicedFrames += 1;
        esp32SpeakerRelayStats.lastVoiceAt = Date.now();
        touchBrowserCallActivity("tts_audio", { audio: true });
      }
      if (
        esp32SpeakerRelayStats.frames === 1 ||
        esp32SpeakerRelayStats.frames % 100 === 0 ||
        (rms > 0.01 && esp32SpeakerRelayStats.voicedFrames <= 3)
      ) {
        console.log(
          "[call] ESP32 speaker relay audio",
          `frames=${esp32SpeakerRelayStats.frames}`,
          `voiced=${esp32SpeakerRelayStats.voicedFrames}`,
          `rms=${rms.toFixed(4)}`,
          `peak=${peak.toFixed(4)}`,
          `target=${targetPeak.toFixed(4)}`,
          `gain=${adaptiveGain.toFixed(2)}`,
          `vol=${volumePct}`,
          `rate=${inputRate}`,
        );
      }
    }
    const normalized = new Float32Array(resampled.length);
    for (let i = 0; i < resampled.length; i++) {
      normalized[i] = Math.max(-1, Math.min(1, resampled[i] * adaptiveGain));
    }
    const pcm16 = floatToPcm16(normalized);
    enqueueEsp32SpeakerPcm(pcm16);
  }

  function primeEsp32SpeakerRelay() {
    const silence = new Int16Array(ESP32_CALL_SPEAKER_FRAME_SAMPLES);
    sendOrQueueEsp32SpeakerFrame(silence.buffer);
  }

  async function startEsp32SpeakerRelay(remoteTrack) {
    cleanupEsp32SpeakerRelay();
    esp32SpeakerPendingFrames = [];
    esp32SpeakerPcmRemainder = new Int16Array(0);
    esp32SpeakerRelayStats = { frames: 0, voicedFrames: 0, lastVoiceAt: 0 };
    const mediaTrack = remoteTrack && (remoteTrack.mediaStreamTrack || remoteTrack._mediaStreamTrack);
    if (!mediaTrack) {
      console.warn("[call] ESP32 speaker relay skipped: no MediaStreamTrack on remote track");
      return false;
    }

    try {
      esp32SpeakerKeepaliveEl = remoteTrack.attach();
      esp32SpeakerKeepaliveEl.autoplay = true;
      esp32SpeakerKeepaliveEl.muted = true;
      esp32SpeakerKeepaliveEl.volume = 0;
      esp32SpeakerKeepaliveEl.playsInline = true;
      esp32SpeakerKeepaliveEl.style.display = "none";
      document.body.appendChild(esp32SpeakerKeepaliveEl);
    } catch (err) {
      console.warn("[call] ESP32 speaker relay keepalive attach failed:", err);
    }

    esp32SpeakerCtx = new AudioContext({ sampleRate: 48000 });
    if (esp32SpeakerCtx.state === "suspended") {
      try { await esp32SpeakerCtx.resume(); } catch (_) { /* ignore */ }
    }
    const stream = new MediaStream([mediaTrack]);
    esp32SpeakerSource = esp32SpeakerCtx.createMediaStreamSource(stream);
    esp32SpeakerProcessor = esp32SpeakerCtx.createScriptProcessor(2048, 1, 1);
    esp32SpeakerProcessor.onaudioprocess = (event) => {
      const input = event.inputBuffer.getChannelData(0);
      sendEsp32SpeakerSamples(input, esp32SpeakerCtx.sampleRate);
    };
    esp32SpeakerSource.connect(esp32SpeakerProcessor);
    // Keep the processor alive without audible local playback.
    const silentGain = esp32SpeakerCtx.createGain();
    silentGain.gain.value = 0.00001;
    esp32SpeakerProcessor.connect(silentGain);
    silentGain.connect(esp32SpeakerCtx.destination);
    esp32SpeakerProcessor._silentGain = silentGain;
    primeEsp32SpeakerRelay();
    console.log("[call] ESP32 speaker relay started");
    return true;
  }

  function cleanupEsp32SpeakerRelay() {
    if (esp32SpeakerProcessor) {
      try { esp32SpeakerProcessor.disconnect(); } catch (_) { /* ignore */ }
      const silentGain = esp32SpeakerProcessor._silentGain;
      if (silentGain) {
        try { silentGain.disconnect(); } catch (_) { /* ignore */ }
      }
      esp32SpeakerProcessor.onaudioprocess = null;
      esp32SpeakerProcessor = null;
      esp32SpeakerPcmRemainder = new Int16Array(0);
    }
    if (esp32SpeakerSource) {
      try { esp32SpeakerSource.disconnect(); } catch (_) { /* ignore */ }
      esp32SpeakerSource = null;
    }
    if (esp32SpeakerCtx) {
      try { esp32SpeakerCtx.close(); } catch (_) { /* ignore */ }
      esp32SpeakerCtx = null;
    }
    if (esp32SpeakerKeepaliveEl) {
      try {
        if (remoteAudioTrack) remoteAudioTrack.detach(esp32SpeakerKeepaliveEl);
      } catch (_) { /* ignore */ }
      try { esp32SpeakerKeepaliveEl.remove(); } catch (_) { /* ignore */ }
      esp32SpeakerKeepaliveEl = null;
    }
    closeEsp32CallSpeakerWs(true);
    esp32SpeakerRelayStats = null;
  }

  function feedEsp32Audio(pcmBytes) {
    if (!esp32WorkletNode) return;
    const int16 = new Int16Array(pcmBytes.buffer, pcmBytes.byteOffset, pcmBytes.byteLength / 2);
    if (esp32MicRelayStats) {
      let sumSq = 0;
      let peak = 0;
      for (let i = 0; i < int16.length; i++) {
        const v = int16[i] / 32768;
        sumSq += v * v;
        const abs = Math.abs(v);
        if (abs > peak) peak = abs;
      }
      const rms = int16.length ? Math.sqrt(sumSq / int16.length) : 0;
      esp32MicRelayStats.frames += 1;
      if (rms > 0.01) {
        esp32MicRelayStats.voicedFrames += 1;
        esp32MicRelayStats.lastVoiceAt = Date.now();
        touchBrowserCallActivity("mic_audio", { audio: true });
      }
      if (
        esp32MicRelayStats.frames === 1 ||
        esp32MicRelayStats.frames % 100 === 0 ||
        (rms > 0.01 && esp32MicRelayStats.voicedFrames <= 5)
      ) {
        console.log(
          "[call] ESP32 mic relay audio",
          `frames=${esp32MicRelayStats.frames}`,
          `voiced=${esp32MicRelayStats.voicedFrames}`,
          `rms=${rms.toFixed(4)}`,
          `peak=${peak.toFixed(4)}`,
          `ctx=${esp32AudioCtx ? esp32AudioCtx.state : "none"}`,
        );
      }
    }
    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) {
      float32[i] = int16[i] / 32768;
    }
    const outputRate = esp32AudioCtx ? esp32AudioCtx.sampleRate : 16000;
    esp32WorkletNode.port.postMessage(resampleFloat32(float32, 16000, outputRate));
  }

  async function createEsp32AudioTrack() {
    esp32AudioCtx = new AudioContext({ sampleRate: 16000 });
    if (esp32AudioCtx.state === "suspended") {
      try { await esp32AudioCtx.resume(); } catch (_) { /* ignore */ }
    }
    const blob = new Blob([ESP32_WORKLET_CODE], { type: "application/javascript" });
    const url = URL.createObjectURL(blob);
    await esp32AudioCtx.audioWorklet.addModule(url);
    URL.revokeObjectURL(url);

    esp32WorkletNode = new AudioWorkletNode(esp32AudioCtx, "esp32-pcm-processor");
    const dest = esp32AudioCtx.createMediaStreamDestination();
    esp32WorkletNode.connect(dest);
    esp32MediaStream = dest.stream;
    esp32MicRelayStats = { frames: 0, voicedFrames: 0, lastVoiceAt: 0 };

    send({ type: "start_esp32_relay" });
    esp32RelayActive = true;

    return esp32MediaStream.getAudioTracks()[0];
  }

  function cleanupEsp32AudioTrack() {
    if (esp32RelayActive) {
      send({ type: "stop_esp32_relay" });
      esp32RelayActive = false;
    }
    if (esp32WorkletNode) {
      try { esp32WorkletNode.disconnect(); } catch (_) { /* ignore */ }
      esp32WorkletNode = null;
    }
    if (esp32AudioCtx) {
      try { esp32AudioCtx.close(); } catch (_) { /* ignore */ }
      esp32AudioCtx = null;
    }
    esp32MicRelayStats = null;
    esp32MediaStream = null;
  }

  function enterBrowserLiveKitCall(reason = "manual") {
    if (reason === "wake") {
      if (browserCallStartPromise || lkRoom || prevCallState === "joining" || prevCallState === "active") {
        addCallSystemNote("通话正在接入中…");
        return browserCallStartPromise || lkRoom;
      }
      ensureLiveCallSessionVisible();
      addCallSystemNote("听到唤醒词，正在接入通话…");
    }
    return startBrowserLiveKitCall({ reason });
  }

  async function startBrowserLiveKitCall(options = {}) {
    const reason = options.reason || "manual";
    if (browserCallStartPromise) return browserCallStartPromise;
    if (lkRoom || prevCallState === "joining" || prevCallState === "active" || prevCallState === "leaving") {
      return lkRoom;
    }
    if (!acquireBrowserCallLock(reason)) {
      console.info("[call] ignored start; another browser tab owns the call");
      if (reason === "manual") addCallSystemNote("已有另一个页面正在通话，已忽略本次启动");
      return null;
    }

    browserCallStartPromise = (async () => {
      if (btnCallStart) btnCallStart.disabled = true;
      setBrowserCallState("joining");
      const useEsp32 = selectedMicId === "esp32";
      const callAttemptId = browserCallPendingId || `${browserCallClientId}_${Date.now().toString(36)}`;
      browserCallPendingId = callAttemptId;
      const roomName = `lampgo-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
      let room = null;
      try {
        console.log("[call] starting LiveKit call", { reason, roomName, mic: useEsp32 ? "esp32" : (selectedMicId || "default") });
        addCallSystemNote("正在获取 LiveKit 通话令牌…");
        const resp = await fetch("/api/livekit/token", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            room_name: roomName,
            user_identity: `lampgo-web-${Date.now().toString(36)}`,
            voice_agent: "lampgo-jarvis",
            client_call_id: callAttemptId,
            reason,
          }),
        });
        const body = await resp.json();
        if (!resp.ok || !body.ok) {
          const error = (body && body.error) || `HTTP ${resp.status}`;
          if (resp.status === 409 && error === "another call is already starting") {
            addCallSystemNote("通话正在接入中，请稍候…");
            return null;
          }
          throw new Error(error);
        }
        const { Room, Track, LocalAudioTrack, createLocalAudioTrack } = await loadLiveKitClient();
        const { token, serverUrl } = body.result;
        room = new Room({ adaptiveStream: false, dynacast: false });
        room.on("trackSubscribed", (track) => {
          if (track.kind !== Track.Kind.Audio) return;
          remoteAudioTrack = track;
          addCallSystemNote("收到 Agent 语音轨道，正在转发到 ESP32 扬声器…");
          if (useEsp32) {
            startEsp32SpeakerRelay(track).catch((err) => {
              console.error("[call] ESP32 speaker relay failed:", err);
              addCallSystemNote(`ESP32 扬声器连接失败：${err.message || err}`);
            });
            return;
          }
          const el = track.attach();
          el.autoplay = true;
          el.playsInline = true;
          el.style.display = "none";
          document.body.appendChild(el);
          lkAudioEls.add(el);
        });
        room.on("trackUnsubscribed", (track) => {
          if (remoteAudioTrack === track) remoteAudioTrack = null;
          if (useEsp32) {
            cleanupEsp32SpeakerRelay();
            return;
          }
          track.detach().forEach((el) => {
            lkAudioEls.delete(el);
            el.remove();
          });
        });
        room.on("disconnected", () => {
          const endedRoomName = lkRoomName;
          const endedClientCallId = lkClientCallId;
          cleanupBrowserLiveKitCall();
          setBrowserCallState("idle");
          notifyLiveKitRoomEnded(endedRoomName, "livekit_disconnected", endedClientCallId);
        });
        await room.connect(serverUrl, token);
        lkRoomName = roomName;
        lkClientCallId = callAttemptId;
        browserCallPendingId = "";
        addCallSystemNote("LiveKit 房间已连接，正在发布麦克风…");

        if (useEsp32) {
          const mediaTrack = await createEsp32AudioTrack();
          lkLocalTrack = new LocalAudioTrack(mediaTrack);
          console.log("[call] using ESP32 wireless mic via AudioWorklet");
          addCallSystemNote("ESP32 无线麦克风轨道已创建");
        } else {
          const audioOptions = {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          };
          if (selectedMicId) audioOptions.deviceId = selectedMicId;
          lkLocalTrack = await createLocalAudioTrack(audioOptions);
        }

        await room.localParticipant.publishTrack(lkLocalTrack, { source: Track.Source.Microphone });
        console.log("[call] local microphone track published", { reason, roomName, mic: useEsp32 ? "esp32" : (selectedMicId || "default") });
        lkRoom = room;
        setBrowserCallState("active");
        touchBrowserCallActivity("room_connected");
        addCallSystemNote("通话已连接，正在等待语音识别…");
        console.log("[call] LiveKit call started, mic:", useEsp32 ? "esp32" : "browser");
        return room;
      } catch (err) {
        browserCallPendingId = "";
        console.error("[call] browser LiveKit start failed:", err);
        addCallSystemNote(`通话启动失败：${err.message || err}`);
        if (room) {
          try { room.disconnect(); } catch (_) { /* ignore */ }
        }
        cleanupBrowserLiveKitCall();
        setBrowserCallState("idle");
        return null;
      }
    })();

    try {
      return await browserCallStartPromise;
    } finally {
      browserCallStartPromise = null;
      if (!lkRoom) releaseBrowserCallLock();
    }
  }

  async function scheduleHangupAfterTtsPlayout() {
    // Wait until the remote (Agent) audio track has been observed playing and
    // then stays silent for ~1.5s, with a hard cap of 20s. Falls back to a
    // simple delay if no track is available.
    const MAX_WAIT_MS = 20000;
    const IDLE_MS = 1500;
    const SAMPLE_MS = 80;
    const ENERGY_THRESHOLD = 0.012;
    const FIRST_FRAME_TIMEOUT_MS = 4000;

    const startTs = Date.now();
    const track = remoteAudioTrack;
    const mediaStreamTrack = track && (track.mediaStreamTrack || track._mediaStreamTrack);
    if (!mediaStreamTrack) {
      await new Promise((resolve) => window.setTimeout(resolve, 7000));
      stopBrowserLiveKitCall();
      return;
    }

    let ctx = null;
    let analyser = null;
    let source = null;
    let cleaned = false;
    const cleanup = () => {
      if (cleaned) return;
      cleaned = true;
      try { source && source.disconnect(); } catch (_) { /* ignore */ }
      try { analyser && analyser.disconnect(); } catch (_) { /* ignore */ }
      try { ctx && ctx.close(); } catch (_) { /* ignore */ }
    };

    try {
      ctx = new (window.AudioContext || window.webkitAudioContext)();
      if (ctx.state === "suspended") {
        try { await ctx.resume(); } catch (_) { /* ignore */ }
      }
      const stream = new MediaStream([mediaStreamTrack]);
      source = ctx.createMediaStreamSource(stream);
      analyser = ctx.createAnalyser();
      analyser.fftSize = 1024;
      source.connect(analyser);
      const buf = new Float32Array(analyser.fftSize);

      let sawEnergy = false;
      let lastEnergyTs = 0;

      while (true) {
        const elapsed = Date.now() - startTs;
        if (elapsed >= MAX_WAIT_MS) break;
        if (!lkRoom) return;
        // The user kept talking after saying goodbye — abandon the hangup
        // and let the conversation continue normally.
        if (hangupCancelled) {
          console.log("[call] pending hangup cancelled by new user utterance");
          return;
        }

        analyser.getFloatTimeDomainData(buf);
        let sumSq = 0;
        for (let i = 0; i < buf.length; i++) sumSq += buf[i] * buf[i];
        const rms = Math.sqrt(sumSq / buf.length);

        if (rms > ENERGY_THRESHOLD) {
          sawEnergy = true;
          lastEnergyTs = Date.now();
        } else if (sawEnergy && Date.now() - lastEnergyTs >= IDLE_MS) {
          break;
        } else if (!sawEnergy && elapsed >= FIRST_FRAME_TIMEOUT_MS) {
          // TTS never arrived — give a short grace then hang up.
          await new Promise((resolve) => window.setTimeout(resolve, 600));
          break;
        }

        await new Promise((resolve) => window.setTimeout(resolve, SAMPLE_MS));
      }
    } catch (err) {
      console.warn("[call] tts playout watcher failed, hanging up after fallback delay:", err);
      await new Promise((resolve) => window.setTimeout(resolve, 6000));
    } finally {
      cleanup();
    }

    if (lkRoom) stopBrowserLiveKitCall("goodbye");
  }

  function cleanupBrowserLiveKitCall() {
    clearBrowserCallIdleTimer();
    browserCallLastActivityAt = 0;
    browserCallLastAudioActivityAt = 0;
    lkRoomName = "";
    lkClientCallId = "";
    if (lkLocalTrack) {
      try { lkLocalTrack.stop(); } catch (_) { /* ignore */ }
      lkLocalTrack = null;
    }
    remoteAudioTrack = null;
    hangupPending = false;
    cleanupEsp32AudioTrack();
    cleanupEsp32SpeakerRelay();
    lkAudioEls.forEach((el) => el.remove());
    lkAudioEls.clear();
    lkRoom = null;
    browserCallPendingId = "";
    releaseBrowserCallLock();
  }

  function stopBrowserLiveKitCall(reason = "manual") {
    if (lkRoom) {
      const room = lkRoom;
      const roomName = lkRoomName;
      const clientCallId = lkClientCallId;
      cleanupBrowserLiveKitCall();
      try { room.disconnect(); } catch (_) { /* ignore */ }
      notifyLiveKitRoomEnded(roomName, reason, clientCallId);
    }
    setBrowserCallState("idle");
  }

  function notifyLiveKitRoomEnded(roomName, reason = "manual", clientCallId = "") {
    if (!roomName) return;
    const payload = JSON.stringify({
      room_name: roomName,
      reason,
      client_call_id: clientCallId,
    });
    try {
      if (navigator.sendBeacon) {
        const blob = new Blob([payload], { type: "application/json" });
        if (navigator.sendBeacon("/api/livekit/room/end", blob)) return;
      }
    } catch (_) {
      // Fall through to fetch.
    }
    fetch("/api/livekit/room/end", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: payload,
      keepalive: true,
    }).catch((err) => {
      console.warn("[call] failed to notify room end:", err);
    });
  }

  window.addEventListener("beforeunload", () => {
    if (lkRoomName) notifyLiveKitRoomEnded(lkRoomName, "page_unload", lkClientCallId);
  });

  function createCallSession() {
    const id = `call_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 7)}`;
    const session = {
      id,
      title: "语音通话",
      messages: [],
      createdAt: Date.now(),
      updatedAt: Date.now(),
      isCall: true,
    };
    sessions.unshift(session);
    callSessionId = id;
    activeSessionId = id;
    persistSessions();
    renderHistory();
    return session;
  }

  function pushCallMessage(role, text, meta) {
    if (!callSessionId || !text) return;
    const session = sessions.find((s) => s.id === callSessionId);
    if (!session) return;
    const entry = { role, text, ts: Date.now() };
    if (meta && typeof meta === "object") entry.meta = meta;
    session.messages.push(entry);
    session.updatedAt = Date.now();
    if (session.title === "语音通话" && role === "user") {
      session.title = "\u{1F4DE} " + (text.length > 24 ? text.slice(0, 24) + "…" : text);
    }
    persistSessions();
  }

  function ensureLiveCallSessionVisible() {
    if (!callSessionId || !sessions.find((s) => s.id === callSessionId)) {
      createCallSession();
    }
    if (currentView !== "call") showView("call");
    if (callEmptyState) callEmptyState.style.display = "none";
  }

  function finalizeCallSession() {
    if (!callSessionId) return;
    const finishingId = callSessionId;
    const session = sessions.find((s) => s.id === callSessionId);
    if (session && session.messages.length === 0) {
      sessions.splice(sessions.indexOf(session), 1);
      if (activeSessionId === finishingId) activeSessionId = sessions[0] ? sessions[0].id : null;
    }
    callSessionId = null;
    persistSessions();
    renderHistory();
  }

  function updateCallViewState(state) {
    if (!callDot) return;
    callDot.classList.remove("is-active", "is-joining");
    if (state === "active") {
      callDot.classList.add("is-active");
      callStatusText.textContent = "通话中";
      btnCallStart.classList.add("hidden");
      btnCallEnd.classList.remove("hidden");
      btnCallEnd.disabled = false;
      if (!callSessionId) {
        resetCallView();
        createCallSession();
      }
      showView("call");
    } else if (state === "joining") {
      if (prevCallState === "idle") {
        resetCallView();
        createCallSession();
      }
      showView("call");
      callDot.classList.add("is-joining");
      callStatusText.textContent = "连接中…";
      btnCallStart.classList.add("hidden");
      btnCallEnd.classList.remove("hidden");
      btnCallEnd.disabled = true;
    } else if (state === "leaving") {
      callPreemptedAwaitingResponse = false;
      callStatusText.textContent = "结束中…";
      btnCallStart.classList.add("hidden");
      btnCallEnd.classList.remove("hidden");
      btnCallEnd.disabled = true;
    } else {
      callPreemptedAwaitingResponse = false;
      callStatusText.textContent = "未连接";
      btnCallStart.classList.remove("hidden");
      btnCallStart.disabled = false;
      btnCallEnd.classList.add("hidden");
      if (prevCallState === "active" || prevCallState === "joining" || prevCallState === "leaving") finalizeCallSession();
    }
    prevCallState = state;
  }

  if (btnCallStart) {
    btnCallStart.addEventListener("click", () => {
      enterBrowserLiveKitCall("manual");
    });
  }
  if (btnCallEnd) {
    btnCallEnd.addEventListener("click", () => {
      btnCallEnd.disabled = true;
      if (lkRoom) stopBrowserLiveKitCall();
      else send({ type: "stop_conversation" });
    });
  }

  function scrollCallMessages() {
    if (!callMessages) return;
    requestAnimationFrame(() => {
      callMessages.scrollTop = callMessages.scrollHeight;
    });
  }

  function addCallSystemNote(text) {
    if (!callMessages) return;
    const note = document.createElement("div");
    note.className = "system-note";
    note.textContent = text;
    callMessages.appendChild(note);
    scrollCallMessages();
  }

  function addCallUserBubble(text) {
    if (!callMessages) return;
    if (callEmptyState) callEmptyState.style.display = "none";
    // A new utterance arrived (either typed in chat or via wake-word). Clear
    // stale system notes (e.g. an earlier failed-call error) so they don't
    // mislead the user into thinking this conversation is broken.
    callMessages.querySelectorAll(".system-note").forEach((el) => el.remove());
    const row = document.createElement("div");
    row.className = "flex justify-end mb-4";
    row.innerHTML = `<div class="msg-bubble-wrap"><div class="msg-user">${esc(text)}</div><span class="msg-time">${formatTime()}</span></div>`;
    callMessages.appendChild(row);
    scrollCallMessages();
  }

  function addCallAssistantBubble(requestId, quoteText, showLoading = false) {
    if (!callMessages) return;
    if (callEmptyState) callEmptyState.style.display = "none";
    const row = document.createElement("div");
    row.className = "flex justify-start mb-4";
    const wrap = document.createElement("div");
    wrap.className = "msg-bubble-wrap";
    const bubble = document.createElement("div");
    bubble.className = "msg-assistant";

    const truncated = quoteText.length > 40 ? quoteText.slice(0, 40) + "…" : quoteText;
    bubble.innerHTML =
      `<div class="reply-quote"><span class="reply-quote-text">${esc(truncated)}</span></div>` +
      '<div class="steps"></div><div class="response-text"></div>';

    const time = document.createElement("span");
    time.className = "msg-time";
    time.textContent = formatTime();
    wrap.appendChild(bubble);
    wrap.appendChild(time);
    row.appendChild(wrap);
    callMessages.appendChild(row);
    pendingMessages.set(requestId, bubble);
    bubble.dataset.requestId = requestId;
    bubble.dataset.callView = "true";
    if (showLoading) showAssistantLoadingDots(bubble);
    scrollCallMessages();
    return bubble;
  }

  function updateCallAssistantQuote(bubble, quoteText) {
    if (!bubble || !quoteText) return;
    const quoteEl = bubble.querySelector(".reply-quote-text");
    if (!quoteEl) return;
    const truncated = quoteText.length > 40 ? quoteText.slice(0, 40) + "…" : quoteText;
    quoteEl.textContent = truncated;
  }

  function shouldCreateCallBubbleForEvent(evt) {
    if (
      evt !== "IntentProgress" &&
      evt !== "ToolCallPlanned" &&
      evt !== "ToolCallFinished" &&
      evt !== "AgentFinished" &&
      evt !== "ChatMessage"
    ) {
      return false;
    }
    return !!(
      callSessionId ||
      browserCallStartPromise ||
      lkRoom ||
      prevCallState === "joining" ||
      prevCallState === "active" ||
      prevCallState === "leaving"
    );
  }

  // 把通话视图恢复到“空白起点”：移除所有 bubble、显示 empty-state、清掉当前关联的 callSessionId。
  // 注意不要在直播通话进行中（active/joining/leaving）调用这个函数，否则会抹掉正在进行的 DOM。
  function resetCallView() {
    if (!callMessages) {
      callSessionId = null;
      return;
    }
    Array.from(callMessages.children).forEach((child) => {
      if (child.id !== "call-empty-state") child.remove();
    });
    if (callEmptyState) callEmptyState.style.display = "";
    callSessionId = null;
    callPreemptedAwaitingResponse = false;
    callUserRequestIds.clear();
  }

  function renderHistoricalCallUserBubble(text, ts) {
    if (!callMessages || !text) return;
    const row = document.createElement("div");
    row.className = "flex justify-end mb-4";
    row.innerHTML = `<div class="msg-bubble-wrap"><div class="msg-user">${esc(text)}</div><span class="msg-time">${formatTime(new Date(ts))}</span></div>`;
    callMessages.appendChild(row);
  }

  function renderHistoricalCallAssistantBubble(text, ts, meta) {
    if (!callMessages) return;
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
    callMessages.appendChild(row);
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
    if (bubble && meta && meta.preempted) {
      const tag = document.createElement("span");
      tag.className = "preempted-tag";
      tag.textContent = "已中止";
      bubble.appendChild(tag);
    }
  }

  function renderHistoricalCallSession(session) {
    if (!session || !callMessages) return;
    Array.from(callMessages.children).forEach((child) => {
      if (child.id !== "call-empty-state") child.remove();
    });
    if (!session.messages || !session.messages.length) {
      if (callEmptyState) callEmptyState.style.display = "";
      callSessionId = session.id;
      return;
    }
    if (callEmptyState) callEmptyState.style.display = "none";
    session.messages.forEach((m) => {
      if (m.role === "user") {
        renderHistoricalCallUserBubble(m.text, m.ts);
      } else if (m.role === "assistant") {
        renderHistoricalCallAssistantBubble(m.text, m.ts, m.meta);
      }
    });
    callSessionId = session.id;
    scrollCallMessages();
  }

  // sidebar 的 “通话” 导航按钮：在不打断直播通话的前提下，每次点击都回到一个全新的通话页面。
  // - 直播中 / 连接中 / 挂断中：仅切换 view，保留 DOM；
  // - 其他状态（idle 或正在浏览历史通话）：把 DOM 重置成空白起点。
  function openFreshCallView() {
    showView("call");
    if (
      prevCallState === "active" ||
      prevCallState === "joining" ||
      prevCallState === "leaving"
    ) {
      return;
    }
    resetCallView();
  }

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
      const description = ((recordDescriptionInput && recordDescriptionInput.value) || "").trim();
      if (!description) {
        recordNameError.textContent = "请输入动作说明，AI 会根据它判断什么时候播放这个动作";
        if (recordDescriptionInput) recordDescriptionInput.focus();
        return;
      }
      recordNameError.textContent = "";
      saveMotionRecording(name, description, pendingOverwriteSave);
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
    btnRefreshOpenclaw.addEventListener("click", async () => {
      if (btnRefreshOpenclaw.classList.contains("is-loading")) return;
      btnRefreshOpenclaw.classList.add("is-loading");
      btnRefreshOpenclaw.disabled = true;
      if (openclawHealthCard) openclawHealthCard.classList.add("is-refreshing");
      if (openclawTaskList) openclawTaskList.classList.add("is-refreshing");
      send({ type: "openclaw_tasks" });
      try {
        // Keep a minimum visible spin so the user gets feedback even when the
        // server answers in <50ms.
        await Promise.all([
          refreshOpenclawHealth(),
          new Promise((r) => setTimeout(r, 450)),
        ]);
      } finally {
        btnRefreshOpenclaw.classList.remove("is-loading");
        btnRefreshOpenclaw.disabled = false;
        if (openclawHealthCard) openclawHealthCard.classList.remove("is-refreshing");
        if (openclawTaskList) openclawTaskList.classList.remove("is-refreshing");
      }
    });
  }

  /* ---- Navigation + hints + history ---- */

  navButtons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const view = btn.dataset.view;
      if (!view) return;
      if (btn.dataset.action === "new-chat") startNewSession();
      else if (view === "call") openFreshCallView();
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
      if (callMessages) callMessages.scrollTop = callMessages.scrollHeight;
    });
  }

  /* ---- Voice Recording ---- */
  let mediaRecorder = null;
  let audioChunks = [];
  let voiceHintEl = document.querySelector(".voice-hint");
  let voiceTimerId = null;
  let voiceStartTime = 0;
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
        const espOpt = document.createElement("option");
        espOpt.value = "esp32";
        espOpt.textContent = esp32PeripheralLabel("mic", cameraCache.esp32);
        micSelect.appendChild(espOpt);
        mics.forEach((d) => {
          const opt = document.createElement("option");
          opt.value = d.deviceId;
          opt.textContent = d.label || `麦克风 ${d.deviceId.slice(0, 8)}`;
          micSelect.appendChild(opt);
        });
        if (selectedMicId === "esp32") {
          micSelect.value = "esp32";
        } else if (selectedMicId && mics.some((m) => m.deviceId === selectedMicId)) {
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

  let esp32Recording = false;

  btnMic.addEventListener("click", () => {
    stopAllTts();
    void unlockTtsPlayback();
    if (!isVoiceMode) {
      if (selectedMicId === "esp32") {
        startEsp32VoiceMode();
      } else {
        startVoiceMode();
      }
    }
  });

  btnVoiceCancel.addEventListener("click", () => {
    if (!isVoiceMode) return;
    voiceCancelled = true;
    if (esp32Recording) {
      fetch("/api/device/capture-audio/cancel", { method: "POST" }).catch(() => {});
      esp32Recording = false;
    }
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

    startVoiceTimer();
    requestAnimationFrame(() => drawWaveform());

    audioChunks = [];
    mediaRecorder = new MediaRecorder(micStream, { mimeType: pickMimeType() });
    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) audioChunks.push(e.data);
    };
    mediaRecorder.onstop = () => finishRecording();
    mediaRecorder.start();
  }

  function startVoiceTimer() {
    voiceStartTime = Date.now();
    if (voiceHintEl) voiceHintEl.textContent = "0:00";
    voiceTimerId = setInterval(() => {
      const elapsed = Math.floor((Date.now() - voiceStartTime) / 1000);
      const m = Math.floor(elapsed / 60);
      const s = elapsed % 60;
      if (voiceHintEl) voiceHintEl.textContent = `${m}:${s.toString().padStart(2, "0")}`;
    }, 500);
  }

  function stopVoiceTimer() {
    if (voiceTimerId) {
      clearInterval(voiceTimerId);
      voiceTimerId = null;
    }
    if (voiceHintEl) voiceHintEl.textContent = "聆听中…";
  }

  function stopVoiceMode() {
    isVoiceMode = false;
    micGroup.style.display = "";
    voiceWave.classList.add("hidden");
    btnVoiceCancel.classList.add("hidden");
    chatInput.style.display = "";
    stopVoiceTimer();

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

  async function startEsp32VoiceMode() {
    try {
      const resp = await fetch("/api/device/capture-audio/start", { method: "POST" });
      const data = await resp.json();
      if (!data.ok) {
        addSystemMessage("ESP32 录音启动失败：" + (data.error || "未知错误"));
        return;
      }
    } catch (err) {
      console.error("[voice] ESP32 start error:", err);
      addSystemMessage("ESP32 录音出错，请检查设备连接");
      return;
    }

    esp32Recording = true;
    isVoiceMode = true;
    voiceCancelled = false;
    micGroup.style.display = "none";
    chatInput.style.display = "none";
    voiceWave.classList.remove("hidden");
    btnVoiceCancel.classList.remove("hidden");

    startVoiceTimer();
    drawEsp32Waveform();
    console.log("[voice] ESP32 recording started — press send to stop");
  }

  async function stopEsp32VoiceMode() {
    if (!esp32Recording) return;
    esp32Recording = false;
    stopVoiceMode();

    if (voiceCancelled) {
      console.log("[voice] ESP32 recording cancelled");
      return;
    }

    try {
      const resp = await fetch("/api/device/capture-audio/stop", { method: "POST" });
      const data = await resp.json();
      if (!data.ok || !data.result || !data.result.audio_data) {
        const message = data.error === "capture_no_frames"
          ? "没有收到 ESP32 音频帧，请检查端侧麦克风流"
          : "ESP32 录音太短或失败：" + (data.error || "未知错误");
        addSystemMessage(message);
        return;
      }
      submitAudioData(data.result.audio_data);
    } catch (err) {
      console.error("[voice] ESP32 stop error:", err);
      addSystemMessage("ESP32 录音停止出错");
    }
  }

  function drawEsp32Waveform() {
    if (!voiceCanvas) return;
    const ctx = voiceCanvas.getContext("2d");
    const W = voiceCanvas.width;
    const H = voiceCanvas.height;
    let t = 0;
    function frame() {
      if (!isVoiceMode) return;
      ctx.clearRect(0, 0, W, H);
      ctx.strokeStyle = "#4ecdc4";
      ctx.lineWidth = 2;
      ctx.beginPath();
      for (let x = 0; x < W; x++) {
        const y = H / 2 + Math.sin(x * 0.04 + t) * (H * 0.2) * (0.5 + 0.5 * Math.sin(t * 0.3));
        x === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      }
      ctx.stroke();
      t += 0.1;
      waveAnimId = requestAnimationFrame(frame);
    }
    frame();
  }

  function submitAudioData(b64) {
    clearEmptyState();
    const history = buildChatHistoryForLlm();
    const requestId = nextId();
    addUserBubble("[语音消息]", requestId);
    const pushed = pushMessageToSession("user", "[语音消息]", { voice: true });
    if (pushed) pendingUserEntries.set(requestId, pushed);
    addAssistantBubble(requestId);
    send({
      type: "audio",
      audio_data: b64,
      request_id: requestId,
      history,
    });
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

    submitAudioData(b64);
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
  let ttsCurrentEsp32Ws = null;
  let ttsEsp32IdleCloseTimer = null;
  let ttsStopped = false;
  const ESP32_TTS_SAMPLE_RATE = 16000;
  const ESP32_TTS_FRAME_SAMPLES = 320; // 20 ms @ 16 kHz, safely under ESP32 frame limit.

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

  function handleTtsAudio(audioB64, format, sampleRate) {
    const mimeMap = { mp3: "audio/mpeg", wav: "audio/wav", opus: "audio/ogg" };
    const mime = mimeMap[format] || "audio/mpeg";
    const bytes = Uint8Array.from(atob(audioB64), (c) => c.charCodeAt(0));
    ttsStopped = false;
    ttsQueue.push({ bytes, mime, format, sampleRate: Number(sampleRate) || 24000 });
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

  function sleepMs(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
  }

  async function openEsp32TtsSpeakerWs() {
    if (ttsEsp32IdleCloseTimer) {
      window.clearTimeout(ttsEsp32IdleCloseTimer);
      ttsEsp32IdleCloseTimer = null;
    }
    if (ttsCurrentEsp32Ws && ttsCurrentEsp32Ws.readyState === WebSocket.OPEN) {
      return ttsCurrentEsp32Ws;
    }
    const esp = cameraCache && cameraCache.esp32 ? cameraCache.esp32 : null;
    if (esp && esp.online === false) throw new Error("ESP32 设备离线");
    const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
    const speakerWs = new WebSocket(`${scheme}//${window.location.host}/api/device/speaker`);
    speakerWs.binaryType = "arraybuffer";
    await new Promise((resolve, reject) => {
      const timeout = window.setTimeout(() => {
        try { speakerWs.close(); } catch (_) { /* ignore */ }
        reject(new Error("ESP32 speaker WS 连接超时"));
      }, 1500);
      speakerWs.onopen = () => {
        window.clearTimeout(timeout);
        resolve();
      };
      speakerWs.onerror = () => {
        window.clearTimeout(timeout);
        try { speakerWs.close(); } catch (_) { /* ignore */ }
        reject(new Error("ESP32 speaker WS 连接失败"));
      };
    });
    ttsCurrentEsp32Ws = speakerWs;
    return speakerWs;
  }

  function scheduleEsp32TtsSpeakerClose() {
    if (ttsEsp32IdleCloseTimer) window.clearTimeout(ttsEsp32IdleCloseTimer);
    ttsEsp32IdleCloseTimer = window.setTimeout(() => {
      ttsEsp32IdleCloseTimer = null;
      if (ttsCurrentEsp32Ws) {
        try { ttsCurrentEsp32Ws.close(); } catch (_) { /* ignore */ }
        ttsCurrentEsp32Ws = null;
      }
    }, 800);
  }

  function mixAudioBufferToMono(audioBuffer) {
    const channelCount = Math.max(1, audioBuffer.numberOfChannels || 1);
    const mono = new Float32Array(audioBuffer.length);
    for (let ch = 0; ch < channelCount; ch++) {
      const data = audioBuffer.getChannelData(ch);
      for (let i = 0; i < mono.length; i++) {
        mono[i] += data[i] / channelCount;
      }
    }
    return mono;
  }

  function pcm16BytesToFloat32(bytes) {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    const samples = Math.floor(bytes.byteLength / 2);
    const out = new Float32Array(samples);
    for (let i = 0; i < samples; i++) {
      out[i] = Math.max(-1, Math.min(1, view.getInt16(i * 2, true) / 32768));
    }
    return out;
  }

  async function decodeTtsChunkToEsp32Pcm(chunk) {
    if (chunk.format === "pcm16") {
      const mono = pcm16BytesToFloat32(chunk.bytes);
      return floatToPcm16(downsampleFloat32(mono, chunk.sampleRate || 24000, ESP32_TTS_SAMPLE_RATE));
    }
    const ctx = await unlockTtsPlayback();
    if (!ctx) throw new Error("浏览器不支持 AudioContext 解码");
    const blob = new Blob([chunk.bytes], { type: chunk.mime });
    const buffer = await blob.arrayBuffer();
    const audioBuffer = await ctx.decodeAudioData(buffer.slice(0));
    const mono = mixAudioBufferToMono(audioBuffer);
    const pcm16 = floatToPcm16(downsampleFloat32(mono, audioBuffer.sampleRate, ESP32_TTS_SAMPLE_RATE));
    return pcm16;
  }

  async function streamPcm16ToEsp32Speaker(pcm16, keepOpen) {
    const speakerWs = await openEsp32TtsSpeakerWs();
    try {
      for (let offset = 0; offset < pcm16.length && !ttsStopped; offset += ESP32_TTS_FRAME_SAMPLES) {
        if (speakerWs.readyState !== WebSocket.OPEN) {
          throw new Error("ESP32 speaker WS 已断开");
        }
        const frame = pcm16.subarray(offset, Math.min(offset + ESP32_TTS_FRAME_SAMPLES, pcm16.length));
        speakerWs.send(frame.buffer.slice(frame.byteOffset, frame.byteOffset + frame.byteLength));
        await sleepMs((frame.length / ESP32_TTS_SAMPLE_RATE) * 1000);
      }
      return !ttsStopped;
    } finally {
      if (keepOpen && !ttsStopped) {
        scheduleEsp32TtsSpeakerClose();
      } else {
        if (ttsCurrentEsp32Ws === speakerWs) ttsCurrentEsp32Ws = null;
        try { speakerWs.close(); } catch (_) { /* ignore */ }
      }
    }
  }

  async function playTtsChunkViaEsp32(chunk) {
    const pcm16 = await decodeTtsChunkToEsp32Pcm(chunk);
    if (ttsStopped) return true;
    const ok = await streamPcm16ToEsp32Speaker(pcm16, chunk.format === "pcm16");
    if (ok) console.log("[tts] played through ESP32 speaker");
    return ok;
  }

  async function playRawPcm16ChunkInBrowser(chunk) {
    const ctx = await unlockTtsPlayback();
    if (!ctx) throw new Error("浏览器不支持 AudioContext 播放 PCM");
    const samples = pcm16BytesToFloat32(chunk.bytes);
    const sampleRate = chunk.sampleRate || 24000;
    const audioBuffer = ctx.createBuffer(1, samples.length, sampleRate);
    audioBuffer.copyToChannel(samples, 0);
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
  }

  async function playTtsChunk(chunk) {
    try {
      if (await playTtsChunkViaEsp32(chunk)) return;
    } catch (err) {
      if (!ttsStopped) console.warn("[tts] ESP32 speaker playback failed, fallback to browser:", err);
    }
    if (ttsStopped) return;

    if (chunk.format === "pcm16") {
      await playRawPcm16ChunkInBrowser(chunk);
      return;
    }

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
      while (ttsQueue.length && !ttsStopped) {
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
    ttsStopped = true;
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
    if (ttsCurrentEsp32Ws) {
      try { ttsCurrentEsp32Ws.close(); } catch (_) {}
      ttsCurrentEsp32Ws = null;
    }
    if (ttsEsp32IdleCloseTimer) {
      window.clearTimeout(ttsEsp32IdleCloseTimer);
      ttsEsp32IdleCloseTimer = null;
    }
    ttsPlaying = false;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "stop_tts" }));
    }
  }

  /* ---- Settings view ---- */

  let settingsInited = false;
  let settingsProviderPresets = null;
  let settingsCurrentPersonaFile = "SOUL";
  let settingsPersonaCache = {};
  let settingsMemoryDates = [];
  let settingsSelectedMemoryDate = "";
  let settingsApiKeyIsSet = false;
  // Mirror of LLMConfig.history_turns. Cached so the chat send path can attach
  // the right amount of history without hitting /api/config/llm every send.
  // Seeded from GET /api/config/llm on boot (see loadLlmConfig) and updated
  // whenever the user saves the settings form; default 30 matches the backend.
  let settingsHistoryTurns = 30;
  let memoryCoreLoadedValue = null;

  function setSettingsStatus(el, message, kind) {
    if (!el) return;
    el.textContent = message || "";
    el.classList.remove("is-ok", "is-error");
    if (kind === "ok") el.classList.add("is-ok");
    if (kind === "error") el.classList.add("is-error");
  }

  async function fetchJson(url, options) {
    const resp = await fetch(url, options || {});
    let data = null;
    try { data = await resp.json(); } catch (_) { data = null; }
    if (!resp.ok || !data || data.ok === false) {
      const err = (data && (data.error || data.detail)) || `HTTP ${resp.status}`;
      throw new Error(err);
    }
    return data.result || {};
  }

  function applyLlmRuntimeDefaults(result) {
    if (!result) return;
    if (typeof result.history_turns === "number") settingsHistoryTurns = result.history_turns;
  }

  async function refreshLlmRuntimeDefaults() {
    try {
      applyLlmRuntimeDefaults(await fetchJson("/api/config/llm"));
    } catch (err) {
      console.warn("[settings] failed to load LLM runtime defaults", err);
    }
  }

  // Pick the Base URL for a (provider, message_type) pair from the
  // presets the backend handed us.  Returns an empty string if the
  // provider doesn't advertise a URL for that format (happens e.g. for
  // Anthropic + OpenAI-compat combo, since real Anthropic has no OpenAI
  // endpoint) — callers treat empty as "don't touch the field".
  function resolveBaseUrl(provider, messageType) {
    const preset = settingsProviderPresets && settingsProviderPresets[provider];
    if (!preset) return "";
    const urls = preset.api_urls || {};
    if (messageType && urls[messageType]) return urls[messageType];
    const fallbackType = preset.default_message_type;
    if (fallbackType && urls[fallbackType]) return urls[fallbackType];
    // Legacy top-level base_url mirrors api_urls[default_message_type]
    // on new-style presets, and is the only URL we have on old-style
    // presets loaded from pinned configs.
    return preset.base_url || "";
  }

  // Does this provider actually expose the requested message_type?
  // Used to flag "Anthropic provider + OpenAI format" style mismatches
  // inline instead of letting the user hit a 404 on test connection.
  function providerSupportsMessageType(provider, messageType) {
    const preset = settingsProviderPresets && settingsProviderPresets[provider];
    if (!preset) return true; // unknown presets (custom) → don't warn
    const urls = preset.api_urls || {};
    // If the preset predates the api_urls field, fall back to trusting
    // the old top-level message_type.
    if (!urls || Object.keys(urls).length === 0) {
      return !preset.message_type || preset.message_type === messageType;
    }
    return !!urls[messageType];
  }

  // Apply the Base URL / model defaults whenever Provider or Message
  // Type changes.  `source` tells us which dropdown fired so we know
  // whether to also re-default the other one (Provider → apply its
  // default_message_type if the user hasn't locked a choice yet).
  function applyProviderPreset(provider, { source = "provider" } = {}) {
    if (!settingsProviderPresets || !settingsProviderPresets[provider]) return;
    const preset = settingsProviderPresets[provider];
    const baseEl = document.getElementById("cfg-llm-base-url");
    const mtEl = document.getElementById("cfg-llm-message-type");
    const modelEl = document.getElementById("cfg-llm-model");
    const fastEl = document.getElementById("cfg-llm-fast-model");

    // Custom provider: leave everything to the user — we have no URL
    // to suggest, and overwriting a user-typed Base URL would be
    // surprising.
    if (provider === "custom") {
      if (modelEl && !modelEl.value.trim() && preset.default_model) modelEl.value = preset.default_model;
      if (fastEl && !fastEl.value.trim() && preset.default_fast_model) fastEl.value = preset.default_fast_model;
      return;
    }

    // Message Type: when the user switches Provider, adopt the new
    // provider's default format UNLESS the provider also supports the
    // user's current choice (then keep the choice — the user may have
    // deliberately picked a format the provider supports as an
    // alternative).  When the user changed Message Type directly, never
    // re-override it here.
    if (mtEl && source === "provider") {
      const currentMt = mtEl.value;
      if (!providerSupportsMessageType(provider, currentMt) && preset.default_message_type) {
        mtEl.value = preset.default_message_type;
      } else if (!currentMt && preset.default_message_type) {
        mtEl.value = preset.default_message_type;
      }
    }

    // Base URL: recomputed from the final (provider, message_type) pair.
    if (baseEl) {
      const mt = mtEl ? mtEl.value : preset.default_message_type;
      const url = resolveBaseUrl(provider, mt);
      if (url) {
        baseEl.value = url;
        baseEl.dataset.autofilled = "1";
      }
      // If the provider genuinely doesn't support this message_type
      // (empty url), we intentionally leave the field alone so the user
      // can still test a hand-crafted endpoint — the validation layer
      // below surfaces the mismatch.
    }

    if (modelEl && !modelEl.value.trim() && preset.default_model) modelEl.value = preset.default_model;
    if (fastEl && !fastEl.value.trim() && preset.default_fast_model) fastEl.value = preset.default_fast_model;
  }

  // Surface a soft warning in the LLM status strip when the chosen
  // (provider, message_type) pair is known not to work without a custom
  // Base URL, so the user sees the problem before they click Save.
  function refreshLlmFormatMismatchWarning() {
    const provEl = document.getElementById("cfg-llm-provider");
    const mtEl = document.getElementById("cfg-llm-message-type");
    const status = document.getElementById("cfg-llm-status");
    if (!provEl || !mtEl || !status) return;
    const provider = provEl.value;
    const mt = mtEl.value;
    if (!provider || provider === "custom" || !mt) {
      if (status.dataset.kind === "format-warn") setSettingsStatus(status, "");
      return;
    }
    if (!providerSupportsMessageType(provider, mt)) {
      setSettingsStatus(
        status,
        `提示：${provider} 未官方支持 ${mt === "anthropic" ? "Anthropic Messages" : "OpenAI chat.completions"} 格式，需要你自己填一个能用的 Base URL。`,
      );
      status.dataset.kind = "format-warn";
    } else if (status.dataset.kind === "format-warn") {
      setSettingsStatus(status, "");
      delete status.dataset.kind;
    }
  }

  function syncCustomProviderField(selectedProvider, customValue) {
    const fieldEl = document.getElementById("cfg-llm-provider-custom-field");
    const inputEl = document.getElementById("cfg-llm-provider-custom");
    const isCustom = selectedProvider === "custom";
    if (fieldEl) {
      fieldEl.hidden = !isCustom;
      fieldEl.classList.toggle("hidden", !isCustom);
      fieldEl.style.display = isCustom ? "" : "none";
    }
    if (inputEl) {
      inputEl.disabled = !isCustom;
      if (typeof customValue === "string") inputEl.value = customValue;
    }
  }

  function resolveProviderInput() {
    const provEl = document.getElementById("cfg-llm-provider");
    const customEl = document.getElementById("cfg-llm-provider-custom");
    const status = document.getElementById("cfg-llm-status");
    const selected = provEl ? provEl.value : "";
    if (selected !== "custom") return selected;
    const customValue = customEl ? customEl.value.trim() : "";
    if (!customValue) {
      setSettingsStatus(status, "请选择“自定义”后填写 Provider ID。", "error");
      if (customEl) customEl.focus();
      return "";
    }
    return customValue;
  }

  async function loadLlmConfig() {
    const provEl = document.getElementById("cfg-llm-provider");
    const customProvEl = document.getElementById("cfg-llm-provider-custom");
    const baseEl = document.getElementById("cfg-llm-base-url");
    const keyEl = document.getElementById("cfg-llm-api-key");
    const modelEl = document.getElementById("cfg-llm-model");
    const fastEl = document.getElementById("cfg-llm-fast-model");
    const mtEl = document.getElementById("cfg-llm-message-type");
    const ctxEl = document.getElementById("cfg-llm-context-window");
    const maxTokEl = document.getElementById("cfg-llm-max-tokens");
    const sumMaxTokEl = document.getElementById("cfg-llm-summary-max-tokens");
    const tempEl = document.getElementById("cfg-llm-temperature");
    const timeoutEl = document.getElementById("cfg-llm-timeout");
    const historyEl = document.getElementById("cfg-llm-history-turns");
    const thinkingEl = document.getElementById("cfg-llm-enable-thinking");
    const shareEl = document.getElementById("cfg-share-openclaw-memory");
    const status = document.getElementById("cfg-llm-status");
    setSettingsStatus(status, "加载中…");
    try {
      const result = await fetchJson("/api/config/llm");
      applyLlmRuntimeDefaults(result);
      settingsProviderPresets = result.provider_presets || null;
      if (provEl && result.provider) {
        const known = Array.from(provEl.options).some((opt) => opt.value === result.provider);
        if (known && result.provider !== "custom") {
          provEl.value = result.provider;
          syncCustomProviderField(result.provider, "");
        } else {
          provEl.value = "custom";
          syncCustomProviderField("custom", result.provider === "custom" ? "" : result.provider);
        }
      } else {
        syncCustomProviderField(provEl ? provEl.value : "", customProvEl ? customProvEl.value : "");
      }
      if (baseEl) { baseEl.value = result.api_base || ""; baseEl.dataset.autofilled = "0"; }
      settingsApiKeyIsSet = !!result.api_key_is_set;
      if (keyEl) {
        keyEl.value = "";
        keyEl.placeholder = result.api_key_is_set ? (result.api_key_preview || "已设置（留空保持不变）") : "api-key-placeholder";
      }
      if (modelEl) modelEl.value = result.model || "";
      if (fastEl) fastEl.value = result.fast_model || "";
      if (mtEl && result.message_type) mtEl.value = result.message_type;
      if (ctxEl && typeof result.context_window === "number") {
        ctxEl.value = String(result.context_window);
      }
      if (maxTokEl && typeof result.max_tokens === "number") {
        maxTokEl.value = String(result.max_tokens);
      }
      if (sumMaxTokEl && typeof result.summary_max_tokens === "number") {
        sumMaxTokEl.value = String(result.summary_max_tokens);
      }
      if (tempEl && typeof result.temperature === "number") {
        tempEl.value = String(result.temperature);
      }
      if (timeoutEl && typeof result.timeout_s === "number") {
        timeoutEl.value = String(result.timeout_s);
      }
      if (historyEl && typeof result.history_turns === "number") {
        historyEl.value = String(result.history_turns);
      }
      if (thinkingEl) thinkingEl.checked = !!result.enable_thinking;
      if (shareEl) shareEl.checked = !!result.share_openclaw_memory;
      if (provEl && provEl.value !== "custom") {
        applyProviderPreset(provEl.value, { source: "load" });
      }
      setSettingsStatus(status, "");
      refreshLlmFormatMismatchWarning();
    } catch (err) {
      setSettingsStatus(status, `加载失败：${err.message}`, "error");
    }
  }

  async function saveLlmConfig(validate) {
    const baseEl = document.getElementById("cfg-llm-base-url");
    const keyEl = document.getElementById("cfg-llm-api-key");
    const modelEl = document.getElementById("cfg-llm-model");
    const fastEl = document.getElementById("cfg-llm-fast-model");
    const mtEl = document.getElementById("cfg-llm-message-type");
    const ctxEl = document.getElementById("cfg-llm-context-window");
    const maxTokEl = document.getElementById("cfg-llm-max-tokens");
    const sumMaxTokEl = document.getElementById("cfg-llm-summary-max-tokens");
    const tempEl = document.getElementById("cfg-llm-temperature");
    const timeoutEl = document.getElementById("cfg-llm-timeout");
    const historyEl = document.getElementById("cfg-llm-history-turns");
    const thinkingEl = document.getElementById("cfg-llm-enable-thinking");
    const shareEl = document.getElementById("cfg-share-openclaw-memory");
    const status = document.getElementById("cfg-llm-status");
    const btnSave = document.getElementById("btn-cfg-llm-save");
    const btnTest = document.getElementById("btn-cfg-llm-test");
    const providerValue = resolveProviderInput();
    if (!providerValue) return;
    const normalizedProvider = String(providerValue || "").trim().toLowerCase();
    const useMimoWebSearch = (
      normalizedProvider === "mimo" ||
      normalizedProvider === "mimo" ||
      normalizedProvider === "mimo-anthropic" ||
      normalizedProvider === "mimo-mimo"
    );
    const parseIntOrNull = (el) => {
      if (!el || el.value === "" || el.value == null) return null;
      const v = parseInt(el.value, 10);
      return Number.isFinite(v) && v > 0 ? v : null;
    };
    const parseFloatOrNull = (el) => {
      if (!el || el.value === "" || el.value == null) return null;
      const v = parseFloat(el.value);
      return Number.isFinite(v) ? v : null;
    };
    const body = {
      validate: !!validate,
      provider: providerValue,
      api_base: baseEl ? baseEl.value.trim() : "",
      api_key: keyEl && keyEl.value ? keyEl.value : "",
      model: modelEl ? modelEl.value.trim() : "",
      fast_model: fastEl ? fastEl.value.trim() : "",
      message_type: mtEl ? mtEl.value : "openai",
      context_window: parseIntOrNull(ctxEl),
      max_tokens: parseIntOrNull(maxTokEl),
      summary_max_tokens: parseIntOrNull(sumMaxTokEl),
      temperature: parseFloatOrNull(tempEl),
      timeout_s: parseFloatOrNull(timeoutEl),
      // history_turns allows 0 (disable short-term memory), so we can't use parseIntOrNull.
      history_turns: historyEl && historyEl.value !== ""
        ? (() => {
            const v = parseInt(historyEl.value, 10);
            return Number.isFinite(v) && v >= 0 ? v : null;
          })()
        : null,
      enable_thinking: thinkingEl ? !!thinkingEl.checked : false,
      share_openclaw_memory: shareEl ? shareEl.checked : undefined,
      web_search_enabled: useMimoWebSearch,
    };
    setSettingsStatus(status, validate ? "正在测试连接…" : "保存中…");
    if (btnSave) btnSave.disabled = true;
    if (btnTest) btnTest.disabled = true;
    try {
      const result = await fetchJson("/api/config/llm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (keyEl) {
        keyEl.value = "";
        keyEl.placeholder = result.api_key_is_set ? (result.api_key_preview || "已设置（留空保持不变）") : "api-key-placeholder";
      }
      settingsApiKeyIsSet = !!result.api_key_is_set;
      if (ctxEl && typeof result.context_window === "number") ctxEl.value = String(result.context_window);
      if (maxTokEl && typeof result.max_tokens === "number") maxTokEl.value = String(result.max_tokens);
      if (sumMaxTokEl && typeof result.summary_max_tokens === "number") sumMaxTokEl.value = String(result.summary_max_tokens);
      if (tempEl && typeof result.temperature === "number") tempEl.value = String(result.temperature);
      if (timeoutEl && typeof result.timeout_s === "number") timeoutEl.value = String(result.timeout_s);
      if (historyEl && typeof result.history_turns === "number") {
        historyEl.value = String(result.history_turns);
      }
      applyLlmRuntimeDefaults(result);
      if (thinkingEl) thinkingEl.checked = !!result.enable_thinking;
      setSettingsStatus(
        status,
        validate ? "已应用，下一条消息即生效。" : "已保存。",
        "ok",
      );
    } catch (err) {
      setSettingsStatus(status, `失败：${err.message}`, "error");
    } finally {
      if (btnSave) btnSave.disabled = false;
      if (btnTest) btnTest.disabled = false;
    }
  }

  async function testLlmConfig() {
    const baseEl = document.getElementById("cfg-llm-base-url");
    const keyEl = document.getElementById("cfg-llm-api-key");
    const modelEl = document.getElementById("cfg-llm-model");
    const fastEl = document.getElementById("cfg-llm-fast-model");
    const mtEl = document.getElementById("cfg-llm-message-type");
    const status = document.getElementById("cfg-llm-status");
    const providerValue = resolveProviderInput();
    if (!providerValue) return;
    setSettingsStatus(status, "正在测试连接…");
    try {
      const resp = await fetch("/api/config/llm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          validate: true,
          dry_run: true,
          provider: providerValue,
          api_base: baseEl ? baseEl.value.trim() : "",
          api_key: keyEl && keyEl.value ? keyEl.value : "",
          model: modelEl ? modelEl.value.trim() : "",
          fast_model: fastEl ? fastEl.value.trim() : "",
          message_type: mtEl ? mtEl.value : "openai",
        }),
      });
      const data = await resp.json();
      if (resp.ok && data.ok) {
        setSettingsStatus(status, "连接成功。记得点“保存并生效”。", "ok");
      } else {
        setSettingsStatus(status, `连接失败：${(data && data.error) || resp.status}`, "error");
      }
    } catch (err) {
      setSettingsStatus(status, `连接失败：${err.message}`, "error");
    }
  }

  async function loadPersonaAll(opts) {
    const force = !!(opts && opts.force);
    const status = document.getElementById("persona-editor-status");
    const editor = document.getElementById("persona-editor");
    // Detect "dirty" state: editor content differs from the last loaded
    // server snapshot. If the user has unsaved edits, we still refresh the
    // background cache (so other files are up-to-date) but DO NOT stomp on
    // the textarea — instead show a hint that the file changed on disk.
    // `force` overrides this and always re-renders (used by the manual
    // "刷新" button after the user explicitly opted in).
    const prevCached = settingsPersonaCache[settingsCurrentPersonaFile];
    const editorVal = editor ? editor.value : "";
    const isDirty = !force && editor && prevCached !== undefined && editorVal !== prevCached;
    setSettingsStatus(status, isDirty ? "" : "加载中…");
    try {
      const result = await fetchJson("/api/persona");
      const fresh = result.files || {};
      const newCurrent = fresh[settingsCurrentPersonaFile];
      const remoteChanged = newCurrent !== undefined && newCurrent !== prevCached;
      settingsPersonaCache = fresh;
      if (isDirty && remoteChanged) {
        setSettingsStatus(
          status,
          "文件被外部修改（保存会覆盖磁盘最新内容；点「刷新」可丢弃当前编辑加载磁盘版）",
          "error",
        );
      } else if (isDirty) {
        setSettingsStatus(status, "");
      } else {
        renderPersonaEditor();
        setSettingsStatus(status, force ? "已重新加载磁盘版本。" : "", force ? "ok" : "");
      }
    } catch (err) {
      setSettingsStatus(status, `加载失败：${err.message}`, "error");
    }
  }

  async function reloadPersonaFromDisk() {
    const editor = document.getElementById("persona-editor");
    const prevCached = settingsPersonaCache[settingsCurrentPersonaFile];
    const isDirty = editor && prevCached !== undefined && editor.value !== prevCached;
    if (isDirty) {
      const ok = confirm(
        `${settingsCurrentPersonaFile}.md 有未保存的编辑，刷新会丢弃。继续？`,
      );
      if (!ok) return;
    }
    await loadPersonaAll({ force: true });
  }

  function renderPersonaEditor() {
    const editor = document.getElementById("persona-editor");
    const title = document.getElementById("persona-editor-title");
    if (editor) editor.value = settingsPersonaCache[settingsCurrentPersonaFile] || "";
    if (title) title.textContent = `${settingsCurrentPersonaFile}.md`;
    document.querySelectorAll(".persona-file").forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.personaFile === settingsCurrentPersonaFile);
    });
  }

  async function savePersona() {
    const editor = document.getElementById("persona-editor");
    const status = document.getElementById("persona-editor-status");
    if (!editor) return;
    const content = editor.value;
    setSettingsStatus(status, "保存中…");
    try {
      await fetchJson(`/api/persona/${settingsCurrentPersonaFile}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
      settingsPersonaCache[settingsCurrentPersonaFile] = content;
      // Reset dirty baseline so the manual reload button doesn't ask for
      // confirmation right after a successful save.
      setSettingsStatus(status, "已保存。下一条消息即生效。", "ok");
    } catch (err) {
      setSettingsStatus(status, `保存失败：${err.message}`, "error");
    }
  }

  async function importPersonaFromOpenclaw() {
    const status = document.getElementById("persona-editor-status");
    if (!confirm(
      "只从 OpenClaw 导入 PROFILE.md（关于主人的信息）。\n" +
      "SOUL.md / AGENTS.md 保留 lampgo 自己的台灯身份不被覆盖。\n" +
      "记忆文件请在「记忆」页单独导入。\n" +
      "原文件会先备份到 ~/.lampgo/.backups/。确定？"
    )) return;
    setSettingsStatus(status, "正在从 OpenClaw 导入…");
    try {
      const result = await fetchJson("/api/persona/import-openclaw", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ which: "safe" }),
      });
      const imported = result.imported || {};
      const okList = Object.keys(imported).filter((k) => imported[k]);
      const failList = Object.keys(imported).filter((k) => !imported[k]);
      const parts = [];
      if (okList.length) parts.push(`成功：${okList.join(", ")}`);
      if (failList.length) parts.push(`未找到：${failList.join(", ")}`);
      if (result.backup) parts.push(`已备份 → ${result.backup}`);
      setSettingsStatus(status, parts.join("；") || "完成。", okList.length ? "ok" : "error");
      await loadPersonaAll();
    } catch (err) {
      setSettingsStatus(status, `导入失败：${err.message}`, "error");
    }
  }

  async function importMemoryCoreFromOpenclaw() {
    const status = document.getElementById("memory-core-status");
    if (!confirm(
      "从 ~/.openclaw/MEMORY.md 拷过来，覆盖当前 lampgo 核心记忆。\n" +
      "原文件会先备份到 ~/.lampgo/.backups/。确定？"
    )) return;
    setSettingsStatus(status, "正在从 OpenClaw 导入…");
    try {
      const result = await fetchJson("/api/memory/core/import", { method: "POST" });
      const parts = [];
      parts.push("已导入 OpenClaw 记忆");
      if (result.source) parts.push(`源：${result.source}`);
      if (result.backup) parts.push(`已备份 → ${result.backup}`);
      setSettingsStatus(status, parts.join("；"), "ok");
      await loadMemoryCore({ force: true });
    } catch (err) {
      setSettingsStatus(status, `导入失败：${err.message}`, "error");
    }
  }

  async function resetPersonaToDefault() {
    const status = document.getElementById("persona-editor-status");
    if (!confirm("将把 SOUL.md / AGENTS.md / PROFILE.md 恢复为出厂默认模板。\n原文件会自动备份到 ~/.lampgo/.backups/。确定？")) return;
    setSettingsStatus(status, "正在恢复默认…");
    try {
      const result = await fetchJson("/api/persona/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ which: "all" }),
      });
      const reset = result.reset || {};
      const okList = Object.keys(reset).filter((k) => reset[k]);
      const parts = [];
      if (okList.length) parts.push(`已恢复：${okList.join(", ")}`);
      if (result.backup) parts.push(`已备份 → ${result.backup}`);
      setSettingsStatus(status, parts.join("；") || "完成。", okList.length ? "ok" : "error");
      await loadPersonaAll();
    } catch (err) {
      setSettingsStatus(status, `恢复失败：${err.message}`, "error");
    }
  }

  async function resetMemoryCoreToDefault() {
    const status = document.getElementById("memory-core-status");
    if (!confirm("将把 MEMORY.md 恢复为出厂默认模板（只动核心记忆，不碰每日记忆）。\n原文件会自动备份到 ~/.lampgo/.backups/。确定？")) return;
    setSettingsStatus(status, "正在恢复默认…");
    try {
      const result = await fetchJson("/api/memory/core/reset", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      const parts = ["已恢复 MEMORY.md"];
      if (result.backup) parts.push(`已备份 → ${result.backup}`);
      setSettingsStatus(status, parts.join("；"), "ok");
      await loadMemoryCore();
    } catch (err) {
      setSettingsStatus(status, `恢复失败：${err.message}`, "error");
    }
  }

  async function loadMemoryCore(opts) {
    const force = !!(opts && opts.force);
    const editor = document.getElementById("memory-core-editor");
    const status = document.getElementById("memory-core-status");
    if (!editor) return;
    const prev = memoryCoreLoadedValue;
    const isDirty = !force && prev !== null && editor.value !== prev;
    setSettingsStatus(status, isDirty ? "" : "加载中…");
    try {
      const result = await fetchJson("/api/memory/core");
      const fresh = result.content || "";
      const remoteChanged = prev !== null && fresh !== prev;
      memoryCoreLoadedValue = fresh;
      if (isDirty && remoteChanged) {
        setSettingsStatus(
          status,
          "文件被外部修改（保存会覆盖磁盘最新内容；点「刷新」可丢弃当前编辑加载磁盘版）",
          "error",
        );
      } else if (isDirty) {
        setSettingsStatus(status, "");
      } else {
        editor.value = fresh;
        setSettingsStatus(status, force ? "已重新加载磁盘版本。" : "", force ? "ok" : "");
      }
    } catch (err) {
      setSettingsStatus(status, `加载失败：${err.message}`, "error");
    }
  }

  async function reloadMemoryCoreFromDisk() {
    const editor = document.getElementById("memory-core-editor");
    const isDirty = editor && memoryCoreLoadedValue !== null && editor.value !== memoryCoreLoadedValue;
    if (isDirty) {
      const ok = confirm("MEMORY.md 有未保存的编辑，刷新会丢弃。继续？");
      if (!ok) return;
    }
    await loadMemoryCore({ force: true });
  }

  async function reloadMemoryDailyFromDisk() {
    await loadMemoryDailyList();
    if (settingsSelectedMemoryDate) {
      await showMemoryDaily(settingsSelectedMemoryDate);
    }
  }

  async function saveMemoryCore() {
    const editor = document.getElementById("memory-core-editor");
    const status = document.getElementById("memory-core-status");
    if (!editor) return;
    setSettingsStatus(status, "保存中…");
    try {
      await fetchJson("/api/memory/core", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: editor.value }),
      });
      memoryCoreLoadedValue = editor.value;
      setSettingsStatus(status, "已保存。下一条消息即生效。", "ok");
    } catch (err) {
      setSettingsStatus(status, `保存失败：${err.message}`, "error");
    }
  }

  async function loadMemoryDailyList() {
    const listEl = document.getElementById("memory-daily-list");
    const countEl = document.getElementById("memory-daily-count");
    if (!listEl) return;
    try {
      const result = await fetchJson("/api/memory/daily");
      settingsMemoryDates = result.dates || [];
      if (countEl) {
        countEl.textContent = settingsMemoryDates.length
          ? `共 ${settingsMemoryDates.length} 天`
          : "";
      }
      if (!settingsMemoryDates.length) {
        listEl.innerHTML = '<div class="memory-daily-empty">暂无日记</div>';
      } else {
        listEl.innerHTML = "";
        settingsMemoryDates.forEach((d) => {
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "memory-daily-item";
          btn.textContent = d;
          btn.dataset.date = d;
          btn.addEventListener("click", () => showMemoryDaily(d));
          listEl.appendChild(btn);
        });
      }
      if (result.today) {
        const viewer = document.getElementById("memory-daily-content");
        const title = document.getElementById("memory-daily-title");
        const source = document.getElementById("memory-daily-source");
        if (viewer) viewer.textContent = result.today;
        if (title) title.textContent = "今日（实时）";
        if (source) source.textContent = "~/.lampgo/memory/today";
      }
    } catch (err) {
      listEl.innerHTML = `<div class="memory-daily-empty">加载失败：${err.message}</div>`;
    }
  }

  async function showMemoryDaily(date) {
    settingsSelectedMemoryDate = date;
    document.querySelectorAll(".memory-daily-item").forEach((btn) => {
      btn.classList.toggle("is-active", btn.dataset.date === date);
    });
    const viewer = document.getElementById("memory-daily-content");
    const title = document.getElementById("memory-daily-title");
    const source = document.getElementById("memory-daily-source");
    if (title) title.textContent = date;
    if (source) source.textContent = `~/.lampgo/memory/${date}.md`;
    if (viewer) viewer.textContent = "加载中…";
    try {
      const result = await fetchJson(`/api/memory/daily?date=${encodeURIComponent(date)}`);
      if (viewer) viewer.textContent = result.content || "（空）";
    } catch (err) {
      if (viewer) viewer.textContent = `加载失败：${err.message}`;
    }
  }

  function bootSettingsPanes() {
    // Provider change — apply the new provider's preset (Base URL,
    // default_message_type if incompatible, default model/fast_model).
    const provEl = document.getElementById("cfg-llm-provider");
    if (provEl && !provEl._bound) {
      provEl._bound = true;
      provEl.addEventListener("change", () => {
        const baseEl = document.getElementById("cfg-llm-base-url");
        const customProvEl = document.getElementById("cfg-llm-provider-custom");
        if (baseEl) baseEl.dataset.autofilled = "1";
        const customValue = provEl.value === "custom" ? (customProvEl ? customProvEl.value : "") : "";
        syncCustomProviderField(provEl.value, customValue);
        applyProviderPreset(provEl.value, { source: "provider" });
        refreshLlmFormatMismatchWarning();
      });
    }

    // Message Type change — re-derive Base URL from the CURRENT
    // provider paired with the new format.  This is what lets the user
    // switch "OpenAI 兼容 ↔ Anthropic Messages" and have the Base URL
    // follow automatically (MiMo is the canonical case, but OpenRouter
    // and any other dual-protocol provider benefits for free).
    const mtEl = document.getElementById("cfg-llm-message-type");
    if (mtEl && !mtEl._bound) {
      mtEl._bound = true;
      mtEl.addEventListener("change", () => {
        const curProvEl = document.getElementById("cfg-llm-provider");
        const provider = curProvEl ? curProvEl.value : "";
        if (provider && provider !== "custom") {
          applyProviderPreset(provider, { source: "message_type" });
        }
        refreshLlmFormatMismatchWarning();
      });
    }
    const btnSave = document.getElementById("btn-cfg-llm-save");
    if (btnSave && !btnSave._bound) {
      btnSave._bound = true;
      btnSave.addEventListener("click", () => saveLlmConfig(true));
    }
    const btnTest = document.getElementById("btn-cfg-llm-test");
    if (btnTest && !btnTest._bound) {
      btnTest._bound = true;
      btnTest.addEventListener("click", () => testLlmConfig());
    }
    // Persona file switches
    document.querySelectorAll(".persona-file").forEach((btn) => {
      if (btn._bound) return;
      btn._bound = true;
      btn.addEventListener("click", () => {
        const editor = document.getElementById("persona-editor");
        if (editor) settingsPersonaCache[settingsCurrentPersonaFile] = editor.value;
        settingsCurrentPersonaFile = btn.dataset.personaFile || "SOUL";
        renderPersonaEditor();
      });
    });
    const btnPersonaSave = document.getElementById("btn-persona-save");
    if (btnPersonaSave && !btnPersonaSave._bound) {
      btnPersonaSave._bound = true;
      btnPersonaSave.addEventListener("click", savePersona);
    }
    const btnPersonaImport = document.getElementById("btn-persona-import");
    if (btnPersonaImport && !btnPersonaImport._bound) {
      btnPersonaImport._bound = true;
      btnPersonaImport.addEventListener("click", importPersonaFromOpenclaw);
    }
    const btnPersonaReset = document.getElementById("btn-persona-reset");
    if (btnPersonaReset && !btnPersonaReset._bound) {
      btnPersonaReset._bound = true;
      btnPersonaReset.addEventListener("click", resetPersonaToDefault);
    }
    const btnPersonaReload = document.getElementById("btn-persona-reload");
    if (btnPersonaReload && !btnPersonaReload._bound) {
      btnPersonaReload._bound = true;
      btnPersonaReload.addEventListener("click", () => reloadPersonaFromDisk());
    }
    document.querySelectorAll(".memory-section-item").forEach((btn) => {
      if (btn._bound) return;
      btn._bound = true;
      btn.addEventListener("click", () => {
        const target = btn.dataset.memorySection || "core";
        document.querySelectorAll(".memory-section-item").forEach((b) => {
          b.classList.toggle("is-active", b === btn);
        });
        document.querySelectorAll("[data-memory-view]").forEach((view) => {
          view.classList.toggle("hidden", view.dataset.memoryView !== target);
        });
        if (target === "daily") {
          loadMemoryDailyList().catch(() => {});
        }
      });
    });
    const btnMemCoreSave = document.getElementById("btn-memory-core-save");
    if (btnMemCoreSave && !btnMemCoreSave._bound) {
      btnMemCoreSave._bound = true;
      btnMemCoreSave.addEventListener("click", saveMemoryCore);
    }
    const btnMemCoreReset = document.getElementById("btn-memory-core-reset");
    if (btnMemCoreReset && !btnMemCoreReset._bound) {
      btnMemCoreReset._bound = true;
      btnMemCoreReset.addEventListener("click", resetMemoryCoreToDefault);
    }
    const btnMemCoreImport = document.getElementById("btn-memory-core-import");
    if (btnMemCoreImport && !btnMemCoreImport._bound) {
      btnMemCoreImport._bound = true;
      btnMemCoreImport.addEventListener("click", importMemoryCoreFromOpenclaw);
    }
    const btnMemCoreReload = document.getElementById("btn-memory-core-reload");
    if (btnMemCoreReload && !btnMemCoreReload._bound) {
      btnMemCoreReload._bound = true;
      btnMemCoreReload.addEventListener("click", () => reloadMemoryCoreFromDisk());
    }
    const btnMemDailyReload = document.getElementById("btn-memory-daily-reload");
    if (btnMemDailyReload && !btnMemDailyReload._bound) {
      btnMemDailyReload._bound = true;
      btnMemDailyReload.addEventListener("click", () => reloadMemoryDailyFromDisk());
    }
    const btnMemDailySummarizeNow = document.getElementById("btn-memory-daily-summarize-now");
    if (btnMemDailySummarizeNow && !btnMemDailySummarizeNow._bound) {
      btnMemDailySummarizeNow._bound = true;
      btnMemDailySummarizeNow.addEventListener("click", () => summarizeActiveSessionNow());
    }
    const shareEl = document.getElementById("cfg-share-openclaw-memory");
    if (shareEl && !shareEl._bound) {
      shareEl._bound = true;
      shareEl.addEventListener("change", () => {
        // Just persist share toggle without forcing ping validate.
        fetch("/api/config/llm", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ validate: false, share_openclaw_memory: shareEl.checked }),
        }).catch(() => {});
      });
    }
    // ---- generic config panes (hardware / voice / motion / safety) ----
    document.querySelectorAll("[data-cfg-save]").forEach((btn) => {
      if (btn._bound) return;
      btn._bound = true;
      btn.addEventListener("click", () => saveCfgFromButton(btn));
    });
    const ttsProviderSel = document.querySelector('[data-cfg-input="voice.tts_provider"]');
    if (ttsProviderSel && !ttsProviderSel._ttsBound) {
      ttsProviderSel._ttsBound = true;
      // Switching provider invalidates the old voice list, so force-select the
      // first option of the new provider instead of preserving a stale value.
      ttsProviderSel.addEventListener("change", () => {
        repopulateTtsVoiceSelect("");
        syncTtsModelEnabled();
      });
      // Honour the initial provider value on first bind too — otherwise the
      // tts_model input stays enabled during the flash between DOM ready and
      // the first loadCfgAll() response.
      syncTtsModelEnabled();
    }
    const ttsVoiceSel = document.querySelector("[data-cfg-tts-voice]");
    if (ttsVoiceSel && !ttsVoiceSel._ttsVoiceBound) {
      ttsVoiceSel._ttsVoiceBound = true;
      ttsVoiceSel.addEventListener("change", syncTtsVoiceCustomInput);
    }
    const ttsVoiceCustomInput = document.querySelector("[data-cfg-tts-voice-custom]");
    if (ttsVoiceCustomInput && !ttsVoiceCustomInput._ttsVoiceCustomBound) {
      ttsVoiceCustomInput._ttsVoiceCustomBound = true;
      ttsVoiceCustomInput.addEventListener("blur", restoreTtsVoiceSelectIfCustomEmpty);
      ttsVoiceCustomInput.addEventListener("keydown", (ev) => {
        if (ev.key !== "Escape") return;
        ev.preventDefault();
        restoreTtsVoiceSelectFromCustom();
      });
    }
    const btnDismiss = document.getElementById("btn-cfg-restart-dismiss");
    if (btnDismiss && !btnDismiss._bound) {
      btnDismiss._bound = true;
      btnDismiss.addEventListener("click", () => {
        const banner = document.getElementById("cfg-restart-banner");
        if (banner) banner.classList.add("hidden");
      });
    }
    refreshSettingsData();
  }

  function refreshSettingsData() {
    // Re-pull from disk every time. Persona / memory files can be modified
    // out-of-band (OpenClaw plugin tools, manual edits, summarizer), so the
    // local in-memory cache must not be trusted across view re-entries.
    Promise.all([
      loadLlmConfig(),
      loadPersonaAll(),
      loadMemoryCore(),
      loadMemoryDailyList(),
      loadCfgAll().then(() => autoFillMotorPortFromDetect()),
    ]).catch((err) => console.warn("[settings] refresh failed", err));
  }

  // ---------------- generic config (hardware / voice / motion / safety) ----------------

  // Map each section → list of status element + form container.
  const CFG_SECTION_DOM = {
    device: {
      status: "cfg-hw-status",
      form: '[data-cfg-section="hardware"]',
    },
    voice: {
      status: "cfg-voice-status",
      form: '[data-cfg-section="voice"]',
    },
    motion: {
      status: "cfg-motion-status",
      form: '[data-cfg-section="motion"]',
    },
    safety: {
      status: "cfg-safety-status",
      form: '[data-cfg-section="safety"]',
    },
    web: {
      status: "cfg-web-status",
      form: '[data-cfg-section="web"]',
    },
    device_esp32: {
      status: "cfg-esp32-status",
      form: '[data-cfg-section="device_esp32"]',
    },
  };

  let cfgColdRestartFields = [];
  let cfgMotorAutodetectStarted = false;

  // `camera.port` lives in the backend's "voice" section allowlist (camera is
  // voice-adjacent — vision input for the voice assistant). The generic save
  // grouper would otherwise POST it to /api/config/camera, which 404s.
  const CFG_SECTION_OVERRIDE = {
    "camera.port": "voice",
  };

  // Latest server-side device enumeration, keyed by kind. Kept as module
  // state so the topbar chips (camera list) and the hardware settings selects
  // can share the same source of truth.
  const detectedDevices = {
    cameras: [], // [{port: string, name: string}]
    mics: [],    // [{index: string, name: string}]
  };

  function populateSerialPortDatalist(ports) {
    const list = document.getElementById("cfg-hw-serial-list");
    if (!list || !Array.isArray(ports)) return;
    const seen = new Set();
    list.innerHTML = "";
    ports.forEach((port) => {
      const value = String(port || "").trim();
      if (!value || seen.has(value)) return;
      seen.add(value);
      const opt = document.createElement("option");
      opt.value = value;
      list.appendChild(opt);
    });
  }

  async function autoFillMotorPortFromDetect() {
    if (cfgMotorAutodetectStarted) return;
    cfgMotorAutodetectStarted = true;
    const input = document.querySelector('[data-cfg-input="device.motor_port"]');
    if (!input || input.disabled || String(input.value || "").trim()) return;
    const statusEl = document.getElementById("cfg-hw-status");
    const previousStatus = statusEl ? statusEl.textContent : "";
    if (statusEl) statusEl.textContent = "正在自动检测电机串口…";
    try {
      const res = await fetch("/api/config/detect", { method: "POST" });
      const body = await res.json();
      if (!res.ok || !body.ok) throw new Error((body && body.error) || `HTTP ${res.status}`);
      const result = body.result || {};
      populateSerialPortDatalist(result.all_ports || []);
      const motorPort = String(result.motor_port || "").trim();
      if (motorPort && !input.disabled && !String(input.value || "").trim()) {
        input.value = motorPort;
        if (statusEl) statusEl.textContent = `已检测到电机串口 ${motorPort}，保存后会立即热重连。`;
      } else if (statusEl) {
        statusEl.textContent = previousStatus || "";
      }
    } catch (err) {
      console.warn("[settings] motor autodetect failed", err);
      if (statusEl) statusEl.textContent = previousStatus || "";
    }
  }

  function repopulateCameraPortSelect(desiredValue) {
    const sel = document.querySelector("[data-cfg-camera-port]");
    if (!sel) return;
    let keep = desiredValue !== undefined ? desiredValue : sel.value;
    if (!keep && cameraCache.esp32 && cameraCache.esp32.enabled) keep = "esp32";
    sel.innerHTML = "";
    const addOpt = (value, label) => {
      const opt = document.createElement("option");
      opt.value = value;
      opt.textContent = label;
      sel.appendChild(opt);
    };
    addOpt("", "关闭 / 禁用视觉输入");
    const esp = cameraCache.esp32;
    const espStatus = esp && esp.online ? "在线" : "离线";
    const espHost = (esp && esp.host) || "";
    const espLabel = espHost
      ? `${esp32PeripheralLabel("camera", esp)} (${espHost} · ${espStatus})`
      : `${esp32PeripheralLabel("camera", esp)} (${espStatus})`;
    addOpt("esp32", espLabel);
    const seen = new Set(["", "esp32"]);
    detectedDevices.cameras.forEach((cam) => {
      const port = String(cam.port || "");
      if (!port || seen.has(port)) return;
      seen.add(port);
      addOpt(port, cam.name ? `${port} (${cam.name})` : `摄像头 ${port}`);
    });
    if (keep && !seen.has(keep)) {
      addOpt(keep, `${keep}（自定义，保留原值）`);
    }
    sel.value = keep || "";
  }

  function repopulateMicDeviceSelect(desiredValue) {
    const sel = document.querySelector("[data-cfg-mic-device]");
    if (!sel) return;
    const keep = desiredValue !== undefined ? desiredValue : sel.value;
    sel.innerHTML = "";
    const addOpt = (value, label) => {
      const opt = document.createElement("option");
      opt.value = value;
      opt.textContent = label;
      sel.appendChild(opt);
    };
    addOpt("", "系统默认（跟随 sounddevice 默认输入）");
    const esp = cameraCache.esp32;
    const espStatus = esp && esp.online ? "在线" : "离线";
    const espHost = (esp && esp.host) || "";
    const espLabel = espHost
      ? `${esp32PeripheralLabel("mic", esp)} (${espHost} · ${espStatus})`
      : `${esp32PeripheralLabel("mic", esp)} (${espStatus})`;
    addOpt("esp32", espLabel);
    const seen = new Set(["", "esp32"]);
    detectedDevices.mics.forEach((m) => {
      const idx = String(m.index);
      if (seen.has(idx)) return;
      seen.add(idx);
      const label = m.name ? `${idx} (${m.name})` : `麦克风 ${idx}`;
      addOpt(idx, m.is_default ? `${label} · 默认` : label);
    });
    if (keep && !seen.has(keep)) {
      addOpt(keep, `${keep}（自定义，保留原值）`);
    }
    sel.value = keep || "";
  }

  // Voices offered per TTS provider. Volcengine supports many more voices; we
  // surface voices that have been verified with the default Seed-TTS 2 grant,
  // plus keep custom stored values.
  const TTS_VOICE_CUSTOM_VALUE = "__custom__";
  const TTS_VOICE_OPTIONS = {
    volcengine: [
      { value: "zh_male_lubanqihao_uranus_bigtts", label: "搞怪（鲁班七号）：zh_male_lubanqihao_uranus_bigtts" },
      { value: "zh_male_liangsangmengzai_uranus_bigtts", label: "海绵（亮嗓萌仔）：zh_male_liangsangmengzai_uranus_bigtts" },
      { value: "zh_female_jitangnv_uranus_bigtts", label: "电台：zh_female_jitangnv_uranus_bigtts" },
      { value: "zh_female_vv_uranus_bigtts", label: "vivi：zh_female_vv_uranus_bigtts（默认）" },
      { value: "zh_male_taocheng_uranus_bigtts", label: "小天：zh_male_taocheng_uranus_bigtts" },
      { value: "saturn_zh_female_qingyingduoduo_cs_tob", label: "朵朵：saturn_zh_female_qingyingduoduo_cs_tob" },
      { value: "zh_male_wennuanahu_uranus_bigtts", label: "阿虎：zh_male_wennuanahu_uranus_bigtts" },
      { value: TTS_VOICE_CUSTOM_VALUE, label: "自定义…" },
    ],
    "edge-tts": [
      { value: "zh-CN-XiaoxiaoNeural", label: "zh-CN-XiaoxiaoNeural（晓晓 · 中文女声）" },
      { value: "zh-CN-YunxiNeural", label: "zh-CN-YunxiNeural（云希 · 中文男声，年轻）" },
      { value: "zh-CN-XiaoyiNeural", label: "zh-CN-XiaoyiNeural（晓伊 · 中文女声）" },
      { value: "zh-CN-YunjianNeural", label: "zh-CN-YunjianNeural（云健 · 中文男声）" },
      { value: "zh-CN-YunyangNeural", label: "zh-CN-YunyangNeural（云扬 · 中文男声，播音）" },
      { value: "zh-CN-XiaomengNeural", label: "zh-CN-XiaomengNeural（晓梦 · 中文女声）" },
      { value: "en-US-JennyNeural", label: "en-US-JennyNeural（Jenny · 英文女声）" },
      { value: "en-US-GuyNeural", label: "en-US-GuyNeural（Guy · 英文男声）" },
    ],
  };

  const ESP32_WAKE_MODEL_BY_CONFIG = {
    hey_jarvis: "wn9_jarvis_tts",
    wn9_jarvis_tts: "wn9_jarvis_tts",
    wn9_xiaomeitongxue_tts: "wn9_xiaomeitongxue_tts",
    wn9_xiaoyaxiaoya_tts2: "wn9_xiaoyaxiaoya_tts2",
    wn9_xiaoluxiaolu_tts2: "wn9_xiaoluxiaolu_tts2",
    wn9_hixiaoxing_tts: "wn9_hixiaoxing_tts",
  };

  // Toggle the TTS Model ID input based on the current provider.
  function syncTtsModelEnabled() {
    const providerSel = document.querySelector('[data-cfg-input="voice.tts_provider"]');
    const modelInput = document.querySelector('[data-cfg-input="voice.tts_model"]');
    const fieldWrap = document.querySelector('[data-cfg-field="voice.tts_model"]');
    if (!providerSel || !modelInput) return;
    if (!providerSel.value && providerSel.querySelector('option[value="volcengine"]')) {
      providerSel.value = "volcengine";
    }
    const provider = String(providerSel.value || "").toLowerCase();
    const isVolcengine = provider === "volcengine" || provider === "volc";
    modelInput.disabled = !isVolcengine;
    if (fieldWrap) fieldWrap.classList.toggle("is-disabled", !isVolcengine);
  }

  function syncTtsVoiceCustomInput() {
    const voiceSel = document.querySelector("[data-cfg-tts-voice]");
    const customInput = document.querySelector("[data-cfg-tts-voice-custom]");
    if (!voiceSel || !customInput) return;
    const isCustom = voiceSel.value === TTS_VOICE_CUSTOM_VALUE;
    voiceSel.classList.toggle("hidden", isCustom);
    customInput.classList.toggle("hidden", !isCustom);
    customInput.disabled = voiceSel.disabled || !isCustom;
    if (isCustom) customInput.focus();
  }

  function restoreTtsVoiceSelectIfCustomEmpty() {
    const voiceSel = document.querySelector("[data-cfg-tts-voice]");
    const customInput = document.querySelector("[data-cfg-tts-voice-custom]");
    if (!voiceSel || !customInput) return;
    if (voiceSel.value !== TTS_VOICE_CUSTOM_VALUE || customInput.value.trim()) return;
    const fallback = Array.from(voiceSel.options).find((opt) => opt.value !== TTS_VOICE_CUSTOM_VALUE);
    voiceSel.value = fallback ? fallback.value : "";
    syncTtsVoiceCustomInput();
  }

  function restoreTtsVoiceSelectFromCustom() {
    const voiceSel = document.querySelector("[data-cfg-tts-voice]");
    const customInput = document.querySelector("[data-cfg-tts-voice-custom]");
    if (!voiceSel || !customInput || voiceSel.value !== TTS_VOICE_CUSTOM_VALUE) return;
    customInput.value = "";
    restoreTtsVoiceSelectIfCustomEmpty();
    voiceSel.focus();
  }

  function repopulateTtsVoiceSelect(desiredValue) {
    const providerSel = document.querySelector('[data-cfg-input="voice.tts_provider"]');
    const voiceSel = document.querySelector("[data-cfg-tts-voice]");
    const customInput = document.querySelector("[data-cfg-tts-voice-custom]");
    if (!voiceSel) return;
    let provider = (providerSel && providerSel.value) || "volcengine";
    if (provider === "mimo") provider = "volcengine";
    const options = TTS_VOICE_OPTIONS[provider] || [];
    const keep = desiredValue !== undefined ? desiredValue : voiceSel.value;
    voiceSel.innerHTML = "";
    options.forEach((opt) => {
      const el = document.createElement("option");
      el.value = opt.value;
      el.textContent = opt.label;
      voiceSel.appendChild(el);
    });
    const isKnown = options.some((o) => o.value === keep);
    if (keep && !isKnown) {
      const el = document.createElement("option");
      el.value = keep;
      el.textContent = `${keep}（自定义，保留原值）`;
      voiceSel.appendChild(el);
    } else if (!keep && customInput) {
      customInput.value = "";
    }
    voiceSel.value = keep || (options[0] && options[0].value) || "";
    syncTtsVoiceCustomInput();
  }

  function coerceCfgInputValue(inputEl, rawValue) {
    if (inputEl.type === "checkbox") return Boolean(rawValue);
    if (inputEl.type === "number") {
      if (rawValue === null || rawValue === undefined || rawValue === "") return "";
      return String(rawValue);
    }
    if (rawValue === null || rawValue === undefined) return "";
    return String(rawValue);
  }

  function extractCfgInputValue(inputEl) {
    if (inputEl.type === "checkbox") return inputEl.checked;
    if (inputEl.type === "number") {
      const v = inputEl.value.trim();
      if (v === "") return null;
      return Number(v);
    }
    if (inputEl.matches("[data-cfg-tts-voice]") && inputEl.value === TTS_VOICE_CUSTOM_VALUE) {
      const customInput = document.querySelector("[data-cfg-tts-voice-custom]");
      return customInput ? customInput.value.trim() : "";
    }
    // textarea or text input
    if (inputEl.tagName === "TEXTAREA") return inputEl.value;
    return inputEl.value;
  }

  function applyCfgFieldValue(dotted, cell) {
    const input = document.querySelector(`[data-cfg-input="${dotted}"]`);
    if (!input) return;
    input.value = "";
    input.checked = false;
    const value = cell ? cell.value : null;
    if (input.type === "checkbox") {
      input.checked = Boolean(value);
    } else {
      input.value = coerceCfgInputValue(input, value);
    }
    // Mark override state (grey out + hint) if source === "env".
    const source = cell ? cell.source : "default";
    const wrap = input.closest("[data-cfg-field]");
    const hint = document.querySelector(`[data-cfg-override-for="${dotted}"]`);
    if (source === "env") {
      input.disabled = true;
      input.classList.add("is-override-env");
      if (wrap) wrap.classList.add("is-override-env");
      if (hint) {
        hint.textContent = "该字段被 .env / 环境变量覆盖；删除对应 LAMPGO_* 后才能在此修改。";
        hint.classList.remove("hidden");
      }
    } else {
      input.disabled = false;
      input.classList.remove("is-override-env");
      if (wrap) wrap.classList.remove("is-override-env");
      if (hint) {
        hint.textContent = "";
        hint.classList.add("hidden");
      }
    }
    if (dotted === "voice.tts_voice") syncTtsVoiceCustomInput();
  }

  async function loadCfgAll() {
    try {
      const res = await fetch("/api/config");
      const body = await res.json();
      if (!body.ok) throw new Error(body.error || "load failed");
      const { sections, cold_restart_fields } = body.result || {};
      cfgColdRestartFields = cold_restart_fields || [];
      const voiceCell = (sections && sections.voice && sections.voice["voice.tts_voice"]) || null;
      const cameraCell = (sections && sections.voice && sections.voice["camera.port"]) || null;
      const micCell = (sections && sections.voice && sections.voice["voice.mic_device"]) || null;
      const pbCell = (sections && sections.motion && sections.motion["motion.default_playback_mode"]) || null;
      Object.entries(sections || {}).forEach(([, fields]) => {
        Object.entries(fields).forEach(([dotted, cell]) => {
          applyCfgFieldValue(dotted, cell);
        });
      });
      // Selects that start empty need a post-load populate so the stored
      // value from disk isn't wiped. For camera/mic the option list may not
      // be filled yet (detection runs on demand), but we preserve `value` as
      // a "custom, keep" option until the next autodetect populates it.
      repopulateTtsVoiceSelect(voiceCell ? (voiceCell.value || "") : undefined);
      // tts_model input's enabled state depends on the just-applied provider
      // value. Without this call, reloading the page while tts_provider is
      // "edge-tts" would leave the model input editable until the next manual
      // provider change.
      syncTtsModelEnabled();
      repopulateCameraPortSelect(cameraCell ? (cameraCell.value || "") : undefined);
      repopulateMicDeviceSelect(micCell ? (micCell.value || "") : undefined);
      // Motion default playback mode → chip bar. If the user hasn't yet
      // clicked a chip this session (no localStorage override), adopt the
      // server-side default so the chip bar reflects reality.
      if (pbCell && pbCell.value && !localStorage.getItem(PLAYBACK_MODE_KEY)) {
        setPlaybackMode(pbCell.value, { persist: false });
      }
    } catch (err) {
      console.warn("[settings] /api/config failed", err);
    }
  }

  async function pushEsp32LiveConfigFromSavedValues(values) {
    if (!values || values["device_esp32.enabled"] === false) {
      return { ok: true, skipped: true, reason: "disabled" };
    }

    const payload = {};
    if (values["device_esp32.framesize"] !== undefined && values["device_esp32.framesize"] !== "") {
      payload.framesize = Number(values["device_esp32.framesize"]);
    }
    if (values["device_esp32.jpeg_quality"] !== undefined && values["device_esp32.jpeg_quality"] !== "") {
      payload.jpeg_quality = Number(values["device_esp32.jpeg_quality"]);
    }
    if (values["device_esp32.mic_enabled"] !== undefined) {
      payload.mic_enabled = !!values["device_esp32.mic_enabled"];
    }
    if (!Object.keys(payload).length) {
      return { ok: true, skipped: true, reason: "empty" };
    }

    const res = await fetch("/api/device/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok || !body.ok) {
      return { ok: false, error: body.error || `HTTP ${res.status}` };
    }
    return { ok: true, skipped: false };
  }

  async function pushEsp32WakeModelFromSavedValues(values) {
    if (!values || values["voice.wake_word"] === undefined) {
      return { ok: true, skipped: true, reason: "empty" };
    }
    const configuredWakeWord = String(values["voice.wake_word"] || "");
    if (!configuredWakeWord) {
      return { ok: true, skipped: true, reason: "disabled" };
    }
    const wakeModel = ESP32_WAKE_MODEL_BY_CONFIG[configuredWakeWord];
    if (!wakeModel) {
      return { ok: false, error: `未知唤醒词：${configuredWakeWord}` };
    }
    const res = await fetch("/api/device/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ wake_model: wakeModel }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok || !body.ok) {
      return { ok: false, error: body.error || `HTTP ${res.status}` };
    }
    return { ok: true, skipped: false };
  }

  async function saveCfgFromButton(btn) {
    // Collect inputs within the card that owns this save button. Group by the
    // field's API section (word before the first `.`) so a card can mix fields
    // from multiple sections (e.g. the hardware card embeds `voice.mic_device`
    // next to `device.motor_port`) and we fire one POST per section.
    const card = btn.closest(".settings-card") || document;
    const primarySection = btn.dataset.cfgSave || "";
    const statusId =
      (CFG_SECTION_DOM[primarySection] || {}).status || null;
    const statusEl = statusId ? document.getElementById(statusId) : null;

    const grouped = {};
    card.querySelectorAll("[data-cfg-input]").forEach((input) => {
      if (input.disabled) return;
      const dotted = input.dataset.cfgInput || "";
      const dot = dotted.indexOf(".");
      if (dot < 0) return;
      const section = CFG_SECTION_OVERRIDE[dotted] || dotted.slice(0, dot);
      (grouped[section] = grouped[section] || {})[dotted] = extractCfgInputValue(input);
    });
    const sections = Object.keys(grouped);
    if (sections.length === 0) {
      if (statusEl) statusEl.textContent = "没有可保存的字段（都被 .env 覆盖或未改动）";
      return;
    }

    // If camera.port is set to the magic "esp32" value, strip it from the
    // camera section (so we don't persist a bogus port) and separately enable
    // the ESP32 device config.
    let switchToEsp32 = false;
    if (grouped.camera && grouped.camera["camera.port"] === "esp32") {
      grouped.camera["camera.port"] = "";
      switchToEsp32 = true;
    }

    if (statusEl) statusEl.textContent = "保存中…";
    const needsRestart = [];
    const errors = [];
    const hotReloadNotes = [];
    let cameraPortSaved = null;
    let playbackDefaultSaved = null;
    for (const section of sections) {
      try {
        const res = await fetch(`/api/config/${section}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(grouped[section]),
        });
        const body = await res.json();
        if (!body.ok) throw new Error(body.error || "save failed");
        const { needs_restart, section: refreshed, hot_reload: hotReload } = body.result || {};
        if (refreshed) {
          Object.entries(refreshed).forEach(([dotted, cell]) => {
            applyCfgFieldValue(dotted, cell);
          });
          if (section === "voice" && refreshed["voice.tts_voice"]) {
            repopulateTtsVoiceSelect(refreshed["voice.tts_voice"].value || "");
          }
        }
        if (needs_restart && needs_restart.length) needsRestart.push(...needs_restart);
        const motorReload = hotReload && hotReload["device.motor_port"];
        if (motorReload && !motorReload.skipped) {
          if (motorReload.connected) {
            hotReloadNotes.push("电机已重连");
          } else if (motorReload.mode === "virtual" && motorReload.reason === "empty_motor_port") {
            hotReloadNotes.push("电机已关闭，进入无硬件模式");
          } else {
            hotReloadNotes.push(`电机未连接：${motorReload.error || "请检查串口"}`);
          }
        }
        if ("camera.port" in grouped[section]) {
          cameraPortSaved = String(grouped[section]["camera.port"] || "");
        }
        if ("motion.default_playback_mode" in grouped[section]) {
          playbackDefaultSaved = String(grouped[section]["motion.default_playback_mode"] || "");
        }
      } catch (err) {
        errors.push(`${section}: ${err.message}`);
      }
    }
    if (statusEl) {
      statusEl.textContent = errors.length
        ? `保存失败：${errors.join("; ")}`
        : (hotReloadNotes.length ? `已保存，${hotReloadNotes.join("；")}` : "已保存");
    }
    if (!errors.length && primarySection === "device_esp32" && grouped.device_esp32) {
      if (statusEl) statusEl.textContent = "已保存，正在同步设备…";
      try {
        const pushed = await pushEsp32LiveConfigFromSavedValues(grouped.device_esp32);
        if (statusEl) {
          if (pushed.skipped && pushed.reason === "disabled") {
            statusEl.textContent = "已保存，ESP32 已关闭";
          } else if (pushed.ok) {
            statusEl.textContent = pushed.skipped ? "已保存" : "已保存并生效";
          } else {
            statusEl.textContent = `已保存，但设备未同步：${pushed.error || "设备离线"}`;
          }
        }
      } catch (err) {
        if (statusEl) statusEl.textContent = `已保存，但设备未同步：${err.message || "设备离线"}`;
      }
    }
    if (!errors.length && primarySection === "voice" && grouped.voice && grouped.voice["voice.wake_word"] !== undefined) {
      if (statusEl) statusEl.textContent = "已保存，正在同步 ESP32 唤醒词…";
      try {
        const pushed = await pushEsp32WakeModelFromSavedValues(grouped.voice);
        if (statusEl) {
          if (pushed.skipped && pushed.reason === "disabled") {
            statusEl.textContent = "已保存，唤醒词已禁用";
          } else if (pushed.ok) {
            statusEl.textContent = pushed.skipped ? "已保存" : "已保存并同步到 ESP32";
          } else {
            statusEl.textContent = `已保存，但 ESP32 未同步：${pushed.error || "设备离线"}`;
          }
        }
      } catch (err) {
        if (statusEl) statusEl.textContent = `已保存，但 ESP32 未同步：${err.message || "设备离线"}`;
      }
    }
    if (needsRestart.length) showColdRestartBanner(needsRestart);
    // Settings save persists to disk + updates in-memory config, but the
    // topbar chip's cache and any live camera producer also need to know.
    // Broadcast via the same WS path the chip popover uses.
    if (switchToEsp32) {
      if (typeof send === "function") {
        send({ type: "set_camera", port: "esp32", request_id: `cam_set_${Date.now()}` });
      }
      try {
        await fetch("/api/config/device_esp32", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ "device_esp32.enabled": true }),
        });
      } catch (_) { /* best-effort */ }
    } else if (cameraPortSaved !== null && typeof send === "function") {
      send({ type: "set_camera", port: cameraPortSaved, request_id: `cam_set_${Date.now()}` });
    }
    // When the default playback mode changes, clear any stale session override
    // so the chip bar immediately switches to (and persists as) the new
    // default. The user can still click a chip afterwards to override.
    if (playbackDefaultSaved !== null && PLAYBACK_MODES.has(playbackDefaultSaved)) {
      localStorage.removeItem(PLAYBACK_MODE_KEY);
      setPlaybackMode(playbackDefaultSaved, { persist: false });
    }
  }

  // Map a dotted config path (e.g. "device.motor_port") to its human-readable
  // form label (e.g. "电机串口"). We reuse the labels the user actually sees
  // in the settings form so the cold-restart banner stays in sync with the UI
  // without maintaining a parallel translation table.
  function cfgFieldDisplayName(dotted) {
    const field = document.querySelector(`[data-cfg-field="${dotted}"]`);
    const label = field && field.querySelector(".settings-field-label");
    if (label) {
      // Strip any trailing "(some.dotted.path)" suffix the form uses to echo
      // the config key — the banner appends the dotted name separately.
      const text = label.textContent.replace(/\s*\([^)]*\)\s*$/, "").trim();
      if (text) return text;
    }
    return "";
  }

  function showColdRestartBanner(fields) {
    const banner = document.getElementById("cfg-restart-banner");
    if (!banner) return;
    banner.classList.remove("hidden");
    const desc = banner.querySelector(".settings-restart-banner-desc");
    if (desc && fields && fields.length) {
      const list = fields
        .map((f) => {
          const name = cfgFieldDisplayName(f);
          return name ? `${name} <code>${f}</code>` : `<code>${f}</code>`;
        })
        .join("、");
      desc.innerHTML =
        `${list} 需要重启 lampgo 才会生效。请在启动的终端按 <code>Ctrl+C</code> 后重跑 <code>uv run lampgo run --web</code>。`;
    }
  }

  function initSettingsView() {
    if (!settingsInited) {
      settingsInited = true;
      const tabs = document.querySelectorAll(".settings-tab");
      const panes = document.querySelectorAll(".settings-pane");
      tabs.forEach((tab) => {
        tab.addEventListener("click", () => {
          const target = tab.dataset.settingsTab;
          tabs.forEach((t) => t.classList.toggle("is-active", t === tab));
          panes.forEach((p) => p.classList.toggle("hidden", p.dataset.settingsPane !== target));
        });
      });
      bootSettingsPanes();
    } else {
      refreshSettingsData();
    }
  }

  if (sidebarResizer) {
    sidebarResizer.addEventListener("pointerdown", beginSidebarResize);
    sidebarResizer.addEventListener("keydown", (ev) => {
      if (!isSidebarResizeEnabled()) return;
      if (ev.key === "ArrowLeft" || ev.key === "ArrowRight") {
        ev.preventDefault();
        const step = ev.shiftKey ? 32 : 16;
        nudgeSidebarWidth(ev.key === "ArrowLeft" ? -step : step);
        return;
      }
      if (ev.key === "Home") {
        ev.preventDefault();
        applySidebarWidth(MIN_SIDEBAR_WIDTH, { persist: true });
        return;
      }
      if (ev.key === "End") {
        ev.preventDefault();
        applySidebarWidth(MAX_SIDEBAR_WIDTH, { persist: true });
      }
    });
  }
  syncSidebarResizeState();
  window.addEventListener("resize", syncSidebarResizeState);

  /* ---- Boot ---- */

  loadSessions();
  renderHistory();
  // 同步初始化路径：直接用 activeSessionId 找上次看的会话；getActiveSession 会过滤掉通话会话，
  // 但通话会话也应该在启动时回到通话视图，所以这里走原始查找。
  const bootSession = activeSessionId
    ? sessions.find((s) => s.id === activeSessionId) || null
    : null;
  if (bootSession && bootSession.messages.length) {
    loadSession(bootSession.id);
  }
  // Kick off server-side sync in the background — when it finishes it will
  // replace the on-screen sessions with the authoritative snapshot from
  // ~/.lampgo/sessions.json, so opening the page in a different browser or
  // after a process restart shows the same history.
  syncSessionsFromServer();
  refreshLlmRuntimeDefaults();
  // Replay missed events so the left-side event log looks continuous across
  // process restarts and different browsers.
  backfillEventsFromServer();
  ensureIdleTimer();
  updateRecordButtonState();
  initEsp32VolumeControl();
  startWebPageOwnerHeartbeat();
  connect();
})();
