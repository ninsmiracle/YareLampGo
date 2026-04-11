/* lampgo Web UI — WebSocket chat client */

(function () {
  "use strict";

  const chatMessages = document.getElementById("chat-messages");
  const chatForm = document.getElementById("chat-form");
  const chatInput = document.getElementById("chat-input");
  const appShell = document.querySelector(".app-shell");
  const paneResizer = document.getElementById("pane-resizer");
  const connDot = document.getElementById("conn-dot");
  const connText = document.getElementById("conn-text");
  const btnEstop = document.getElementById("btn-estop");
  const btnRecordMotion = document.getElementById("btn-record-motion");
  const btnRecordMotionPanel = document.getElementById("btn-record-motion-panel");
  const btnOpenEvents = document.getElementById("btn-open-events");
  const btnCloseDrawer = document.getElementById("btn-close-drawer");
  const eventDrawer = document.getElementById("event-drawer");
  const emptyState = document.getElementById("empty-state");
  const skillGrid = document.getElementById("skill-grid");
  const recordingGrid = document.getElementById("recording-grid");
  const expressionGrid = document.getElementById("expression-grid");
  const stHealth = document.getElementById("st-health");
  const stBusy = document.getElementById("st-busy");
  const stSkill = document.getElementById("st-skill");
  const stEstop = document.getElementById("st-estop");
  const jointPanel = document.getElementById("joint-panel");
  const openclawTaskList = document.getElementById("openclaw-task-list");
  const eventLog = document.getElementById("event-log");
  const btnMic = document.getElementById("btn-mic");
  const btnVoiceCancel = document.getElementById("btn-voice-cancel");
  const btnStop = document.getElementById("btn-stop");
  const voiceWave = document.getElementById("voice-wave");
  const voiceCanvas = document.getElementById("voice-canvas");
  const groupToggles = document.querySelectorAll("[data-toggle-group]");
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

  let ws = null;
  let reqCounter = 0;
  const pendingMessages = new Map();
  const pendingUserMessages = new Map();
  const openclawTasks = new Map();
  const streamingState = new Map();
  let isResizing = false;
  let activeAgentRequestId = null;
  let isMotionRecording = false;
  let hasPendingMotionRecording = false;
  let pendingOverwriteSave = false;
  let recordingStartTs = 0;
  let recordTimerTask = null;
  let recordingFps = 30;
  let recordingFrames = 0;

  function eachRecordButton(fn) {
    [btnRecordMotion, btnRecordMotionPanel].forEach((btn) => {
      if (btn) fn(btn);
    });
  }

  function resetRecordStartDialogUI() {
    if (btnRecordStartConfirm) {
      btnRecordStartConfirm.textContent = "开始录制";
    }
    if (btnRecordStartCancel) {
      btnRecordStartCancel.classList.remove("hidden");
    }
    if (recordStartDesc) {
      recordStartDesc.textContent = "点击“开始录制”后将自动关闭电机力矩，你可以手动掰动关节进行录制。";
    }
    if (recordTimer) {
      recordTimer.textContent = "已录制 0.0s";
    }
    if (recordMetrics) {
      recordMetrics.textContent = "采样：-- FPS · 0 帧";
    }
  }

  function startRecordTimer() {
    stopRecordTimer();
    if (!recordTimer) {
      return;
    }
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

  const SIDEBAR_WIDTH_KEY = "lampgo.sidebarWidth";
  const SIDEBAR_MIN = 220;
  const SIDEBAR_DEFAULT = 284;

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
  }

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
      handleEvent(msg);
      logEvent(msg);
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
      if (btnRecordStartConfirm) {
        btnRecordStartConfirm.textContent = "结束录制";
      }
      if (btnRecordStartCancel) {
        btnRecordStartCancel.classList.add("hidden");
      }
      if (recordStartDesc) {
        recordStartDesc.textContent = "录制进行中。按“结束录制”完成采集。";
      }
      if (recordMetrics) {
        recordMetrics.textContent = `采样：${recordingFps} FPS · 0 帧`;
      }
      startRecordTimer();
      if (recordStartDialog && !recordStartDialog.open) {
        recordStartDialog.showModal();
      }
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
      if (recordNameError) {
        recordNameError.textContent = "同名动作已存在，再次点击保存将覆盖";
      }
      if (btnRecordSave) {
        btnRecordSave.textContent = "确认覆盖";
      }
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

    if (evt === "OpenClawTaskUpdated" && data.task) {
      upsertOpenClawTask(data.task);
    } else if (evt === "OpenClawPromotionRequested" && data.task) {
      upsertOpenClawTask(data.task);
    } else if (evt === "OpenClawPromotionDecision" && data.task) {
      upsertOpenClawTask(data.task);
    }

    if (evt === "TtsAudio" && data.audio) {
      handleTtsAudio(data.audio, data.format || "mp3");
    }

    const requestId = data.request_id || "";
    const bubble = requestId ? pendingMessages.get(requestId) : null;

    if (!bubble) {
      return;
    }

    const stepsEl = bubble.querySelector(".steps");
    if (!stepsEl) {
      return;
    }

    switch (evt) {
      case "IntentRouting":
        addStep(stepsEl, "正在理解意图...", "active");
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
          if (data.stage === "llm_request") {
            finalizeStreamingThinking(requestId);
            markAllActiveDone(stepsEl);
            addStep(stepsEl, data.message, "active");
          } else if (data.stage === "llm_thinking_delta") {
            appendThinkingDelta(bubble, requestId, data.message);
          } else if (data.stage === "llm_response_delta") {
            appendResponseDelta(bubble, requestId, data.message);
          } else if (data.stage === "llm_narration") {
            finalizeNarration(bubble, requestId, data.message);
          } else if (data.stage === "llm_thinking") {
            appendThinkingToBubble(bubble, data.message);
          } else if (stepsEl.querySelector(".step-row.active:last-child")) {
            updateActiveStep(stepsEl, data.message);
          } else {
            addStep(stepsEl, data.message, "active");
          }
        }
        break;
      case "IntentResolved":
        markLastDone(stepsEl);
        addStep(stepsEl, formatIntentResolved(data), "done");
        if (requestId && requestId === activeAgentRequestId) {
          activeAgentRequestId = null;
          btnStop.classList.add("hidden");
        }
        break;
      case "OpenClawTaskUpdated":
        markLastDone(stepsEl);
        addStep(stepsEl, `OpenClaw 状态：${formatOpenClawStatus(data.task && data.task.status)}`, "done");
        break;
      case "OpenClawPromotionRequested":
        markLastDone(stepsEl);
        addStep(stepsEl, "OpenClaw 生成了 promoted 待确认方案", "active");
        break;
      case "OpenClawPromotionDecision":
        markLastDone(stepsEl);
        addStep(
          stepsEl,
          data.decision === "approve" ? "已确认 promoted" : "已拒绝 promoted",
          data.decision === "approve" ? "done" : "error"
        );
        break;
      case "ToolCallPlanned":
        markLastDone(stepsEl);
        addStep(
          stepsEl,
          `LLM 第 ${data.turn_index} 轮：调用 ${data.tool_name}${formatToolArguments(data.arguments)}`,
          "active"
        );
        break;
      case "ToolCallFinished":
        markLastDone(stepsEl);
        addStep(
          stepsEl,
          data.summary || `工具完成：${data.tool_name} -> ${data.status}`,
          data.status === "ok" ? "done" : "error"
        );
        break;
      case "AgentFinished":
        markLastDone(stepsEl);
        if (data.stop_reason === "finish_response") {
          addStep(stepsEl, "任务完成", "done");
        } else if (data.stop_reason === "user_cancelled") {
          markAllActiveDone(stepsEl);
          addStep(stepsEl, "已停止", "error");
        } else {
          addStep(stepsEl, `流程结束：${data.stop_reason || "unknown"}`, "error");
        }
        if (requestId && requestId === activeAgentRequestId) {
          activeAgentRequestId = null;
          btnStop.classList.add("hidden");
        }
        break;
      case "SkillStarted":
        addStep(stepsEl, `执行技能：${data.skill_id}`, "active");
        break;
      case "SkillProgress":
        updateActiveStep(stepsEl, `执行中 ${Math.round(data.progress * 100)}% ${data.message || ""}`.trim());
        break;
      case "SkillFinished":
        markLastDone(stepsEl);
        addStep(stepsEl, data.status === "ok" ? "执行完成" : `结束：${data.status}`, data.status === "ok" ? "done" : "error");
        break;
      case "SkillCancelled":
        markLastDone(stepsEl);
        addStep(stepsEl, "已取消", "error");
        break;
      case "ChatMessage":
        if (data.content) {
          appendTextToBubble(bubble, data.content);
        }
        break;
    }
  }

  function logEvent(msg) {
    const item = document.createElement("div");
    let cls = "";
    if (msg.event.startsWith("Skill")) cls = "evt-skill";
    else if (msg.event.startsWith("Safety") || msg.event.startsWith("EStop")) cls = "evt-safety";
    else if (msg.event.startsWith("Intent")) cls = "evt-intent";
    else if (msg.event.startsWith("ToolCall") || msg.event === "AgentFinished") cls = "evt-intent";
    else if (msg.event.startsWith("OpenClaw")) cls = "evt-intent";
    else if (msg.event === "ChatMessage") cls = "evt-chat";

    item.className = `event-item ${cls}`;
    const t = new Date(msg.ts * 1000).toLocaleTimeString();
    let displayData = msg.data;
    if (msg.event === "TtsAudio" && displayData && displayData.audio && displayData.audio.length > 100) {
      displayData = { ...displayData, audio: displayData.audio.slice(0, 100) + "…" };
    }
    item.textContent = `[${t}] ${msg.event}: ${JSON.stringify(displayData)}`;
    eventLog.appendChild(item);
    eventLog.scrollTop = eventLog.scrollHeight;
  }

  function updateStatus(data) {
    if (!data) {
      return;
    }

    setStatusValue(stHealth, data.device_health || "--", data.device_health === "ok");
    setStatusValue(stBusy, data.is_busy ? "是" : "否", !data.is_busy);
    setStatusValue(stSkill, data.running_skill || "无", true, true);
    setStatusValue(stEstop, data.estopped ? `是 (${data.estop_reason || ""})` : "否", !data.estopped);

    const joints = data.joint_positions || {};
    jointPanel.innerHTML = Object.entries(joints)
      .map(([k, v]) => {
        const value = typeof v === "number" ? `${v.toFixed(1)}°` : `${v}`;
        return `<div class="joint-row"><span>${esc(k)}</span><span class="joint-value">${esc(value)}</span></div>`;
      })
      .join("");

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
    if (data.intent_type === "openclaw") {
      return `${source}已接管复杂任务`;
    }
    if (data.intent_type === "agent") {
      return `${source}完成多步工具编排`;
    }
    if (data.intent_type === "skill" && data.skill_id) {
      if (data.source === "keyword" && data.matched_keyword) {
        return `${source}命中“${data.matched_keyword}” -> 技能：${data.skill_id}`;
      }
      return `${source}识别为技能：${data.skill_id}`;
    }
    if (data.intent_type === "chat") {
      return `${source}识别为聊天回复`;
    }
    return `${source}判定为复杂请求`;
  }

  function formatIntentSource(source) {
    if (source === "keyword") {
      return "关键词";
    }
    if (source === "llm") {
      return "LLM";
    }
    if (source === "llm_web_search") {
      return "LLM 网页搜索";
    }
    if (source === "openclaw") {
      return "OpenClaw";
    }
    return "意图路由";
  }

  function formatToolArguments(args) {
    const entries = Object.entries(args || {});
    if (!entries.length) {
      return "()";
    }
    return `(${entries.map(([key, value]) => `${key}=${JSON.stringify(value)}`).join(", ")})`;
  }

  function setStatusValue(el, text, good, alwaysNeutral) {
    el.textContent = text;
    el.className = "status-value";
    if (alwaysNeutral) {
      el.classList.add("status-neutral");
    } else {
      el.classList.add(good ? "status-good" : "status-bad");
    }
  }

  function renderSkills(skills) {
    skillGrid.innerHTML = "";
    skills
      .filter((skill) => !["estop", "play_recording"].includes(skill.skill_id))
      .forEach((skill) => {
        const btn = document.createElement("button");
        btn.className = "skill-btn";
        btn.type = "button";
        btn.textContent = skill.skill_id;
        btn.title = skill.description;
        btn.addEventListener("click", () => invokeSkill(skill.skill_id));
        skillGrid.appendChild(btn);
      });
  }

  function renderRecordings(recordings) {
    recordingGrid.innerHTML = "";
    recordings.forEach((name) => {
      const expression = getRecordingExpression(name);
      const btn = document.createElement("button");
      btn.className = "skill-btn";
      btn.type = "button";
      btn.textContent = name;
      btn.title = `播放录制动作：${name} · 推荐表情：${expression}`;
      btn.addEventListener("click", () => invokeRecording(name));
      recordingGrid.appendChild(btn);
    });
  }

  function renderExpressions(expressions) {
    expressionGrid.innerHTML = "";
    expressions.forEach((name) => {
      const btn = document.createElement("button");
      btn.className = "skill-btn";
      btn.type = "button";
      btn.textContent = name;
      btn.title = `切换灯光表情：${name}`;
      btn.addEventListener("click", () => invokeExpression(name));
      expressionGrid.appendChild(btn);
    });
  }

  function renderOpenClawTasks(tasks) {
    openclawTasks.clear();
    (tasks || []).forEach((task) => {
      if (task && task.task_id) {
        openclawTasks.set(task.task_id, task);
      }
    });
    paintOpenClawTasks();
  }

  function upsertOpenClawTask(task) {
    if (!task || !task.task_id) {
      return;
    }
    openclawTasks.set(task.task_id, task);
    paintOpenClawTasks();
  }

  function paintOpenClawTasks() {
    if (!openclawTaskList) {
      return;
    }
    const tasks = Array.from(openclawTasks.values()).sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
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
    return `
      <div class="openclaw-task-card">
        <div class="openclaw-task-head">
          <div class="openclaw-task-title">${esc(task.user_text || task.task_id)}</div>
          <div class="openclaw-task-status">${esc(formatOpenClawStatus(task.status))}</div>
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
      case "queued":
        return "排队中";
      case "planning":
        return "规划中";
      case "executing_with_existing_tools":
        return "复用现有工具";
      case "generating_temporary_asset":
        return "生成 temporary";
      case "awaiting_promotion_confirmation":
        return "等待确认";
      case "promoted":
        return "已 promoted";
      case "rejected":
        return "已拒绝";
      case "failed":
        return "失败";
      default:
        return status || "--";
    }
  }

  function formatProposalStatus(status) {
    if (status === "approved") {
      return "已确认";
    }
    if (status === "rejected") {
      return "已拒绝";
    }
    return "待确认";
  }

  function invokeSkill(skillId) {
    clearEmptyState();
    const requestId = nextId();
    const bubble = addAssistantBubble(requestId);
    addStep(bubble.querySelector(".steps"), `调用 ${skillId}`, "active");
    send({ type: "invoke", skill_id: skillId, params: {}, wait: true, request_id: requestId });
  }

  function invokeRecording(name) {
    clearEmptyState();
    const requestId = nextId();
    const bubble = addAssistantBubble(requestId);
    const expression = getRecordingExpression(name);
    addStep(bubble.querySelector(".steps"), `播放录制动作 ${name} · 表情 ${expression}`, "active");
    send({
      type: "invoke",
      skill_id: "play_recording",
      params: { name, expression },
      wait: true,
      request_id: requestId,
    });
  }

  function invokeExpression(name) {
    clearEmptyState();
    const requestId = nextId();
    const bubble = addAssistantBubble(requestId);
    addStep(bubble.querySelector(".steps"), `切换灯光表情 ${name}`, "active");
    send({
      type: "invoke",
      skill_id: "set_expression",
      params: { expression: name },
      wait: true,
      request_id: requestId,
    });
  }

  function updateRecordButtonState() {
    if (!btnRecordMotion && !btnRecordMotionPanel) {
      return;
    }
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
    send({
      type: "recording_start",
      fps: 30,
      request_id: nextId(),
    });
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
    if (!recordStartDialog || !recordStartDialog.open) {
      return;
    }
    recordStartDialog.close();
  }

  function stopMotionRecording() {
    send({
      type: "recording_stop",
      request_id: nextId(),
    });
  }

  function openRecordNameDialog() {
    if (!recordNameDialog) {
      return;
    }
    pendingOverwriteSave = false;
    recordNameError.textContent = "";
    recordNameInput.value = "";
    if (btnRecordSave) {
      btnRecordSave.textContent = "保存";
    }
    recordNameDialog.showModal();
    recordNameInput.focus();
  }

  function closeRecordNameDialog() {
    if (!recordNameDialog || !recordNameDialog.open) {
      return;
    }
    pendingOverwriteSave = false;
    if (btnRecordSave) {
      btnRecordSave.textContent = "保存";
    }
    recordNameDialog.close();
  }

  function saveMotionRecording(name, overwrite = false) {
    send({
      type: "recording_save",
      name,
      overwrite,
      request_id: nextId(),
    });
  }

  function discardMotionRecording() {
    send({
      type: "recording_discard",
      request_id: nextId(),
    });
  }

  chatForm.addEventListener("submit", (e) => {
    e.preventDefault();
    void unlockTtsPlayback();

    if (isVoiceMode) {
      if (mediaRecorder && mediaRecorder.state !== "inactive") {
        mediaRecorder.stop();
      }
      stopVoiceMode();
      return;
    }

    const text = chatInput.value.trim();
    if (!text) {
      return;
    }

    clearEmptyState();
    chatInput.value = "";
    addUserBubble(text);

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
    if (requestId && bubble) {
      pendingUserMessages.set(requestId, bubble);
    }
    scrollChat();
    return bubble;
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
    scrollChat();
    return bubble;
  }

  function finishPending(msg) {
    const bubble = pendingMessages.get(msg.request_id);
    if (!bubble) {
      return;
    }

    finalizeStreamingThinking(msg.request_id);
    streamingState.delete(msg.request_id);

    const stepsEl = bubble.querySelector(".steps");
    markLastDone(stepsEl);

    const result = msg.result || {};
    const text = result.response || result.chat_response;
    if (text) {
      appendTextToBubble(bubble, text);
    }

    if (!msg.ok && msg.error) {
      addStep(stepsEl, `错误：${msg.error}`, "error");
    }

    if (msg.request_id === activeAgentRequestId) {
      activeAgentRequestId = null;
      btnStop.classList.add("hidden");
    }

    pendingMessages.delete(msg.request_id);
    pendingUserMessages.delete(msg.request_id);
    scrollChat();
  }

  function updateUserBubbleText(requestId, text) {
    const bubble = pendingUserMessages.get(requestId);
    if (!bubble || !text) {
      return;
    }
    bubble.textContent = text;
  }

  function appendThinkingToBubble(bubble, text) {
    if (!bubble || !text) {
      return;
    }
    const stepsEl = bubble.querySelector(".steps");
    if (!stepsEl) {
      return;
    }
    const details = document.createElement("details");
    details.className = "thinking-block";
    const summary = document.createElement("summary");
    summary.textContent = "思考过程";
    details.appendChild(summary);
    const body = document.createElement("div");
    body.className = "thinking-body";
    body.innerHTML = formatAssistantText(text);
    details.appendChild(body);
    stepsEl.appendChild(details);
    scrollChat();
  }

  function appendThinkingDelta(bubble, requestId, chunk) {
    if (!bubble) {
      return;
    }
    const stepsEl = bubble.querySelector(".steps");
    if (!stepsEl) {
      return;
    }
    let state = streamingState.get(requestId);
    if (!state) {
      state = { thinkingEl: null, thinkingText: "" };
      streamingState.set(requestId, state);
    }
    if (!state.thinkingEl) {
      const details = document.createElement("details");
      details.className = "thinking-block";
      details.open = true;
      const summary = document.createElement("summary");
      summary.textContent = "思考中…";
      details.appendChild(summary);
      const body = document.createElement("div");
      body.className = "thinking-body";
      details.appendChild(body);
      stepsEl.appendChild(details);
      state.thinkingEl = details;
      state.thinkingText = "";
    }
    state.thinkingText += chunk;
    const body = state.thinkingEl.querySelector(".thinking-body");
    body.textContent = state.thinkingText;
    scrollChat();
  }

  function appendResponseDelta(bubble, requestId, chunk) {
    if (!bubble) {
      return;
    }
    const el = bubble.querySelector(".response-text");
    if (!el) {
      return;
    }
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
      if (body) {
        body.innerHTML = formatAssistantText(state.thinkingText);
      }
      state.thinkingEl.open = false;
      const summary = state.thinkingEl.querySelector("summary");
      if (summary) {
        summary.textContent = "思考过程";
      }
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
      if (finalEl) {
        finalEl.textContent = "";
      }
    }
    const stepsEl = bubble.querySelector(".steps");
    if (stepsEl) {
      const row = document.createElement("div");
      row.className = "step-row narration";
      row.innerHTML = `<span class="step-icon">💬</span><span>${esc(text)}</span>`;
      stepsEl.appendChild(row);
      scrollChat();
    }
  }

  function appendTextToBubble(bubble, text) {
    const el = bubble.querySelector(".response-text");
    if (!el) {
      return;
    }
    let finalEl = el.querySelector(".final-response");
    if (!finalEl) {
      finalEl = document.createElement("div");
      finalEl.className = "final-response";
      el.appendChild(finalEl);
    }
    finalEl.innerHTML = formatAssistantText(text);
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
    if (!active) {
      return;
    }
    active.classList.remove("active");
    active.classList.add("done");
    const icon = active.querySelector(".step-icon");
    if (icon) {
      icon.textContent = "✓";
    }
  }

  function updateActiveStep(container, text) {
    const actives = container.querySelectorAll(".step-row.active");
    const active = actives.length ? actives[actives.length - 1] : null;
    if (!active) {
      return;
    }
    const spans = active.querySelectorAll("span");
    if (spans[1]) {
      spans[1].textContent = text;
    }
  }

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
      if (isMotionRecording) {
        stopMotionRecording();
      } else if (hasPendingMotionRecording) {
        openRecordNameDialog();
      } else {
        openRecordStartDialog();
      }
    });
  });

  if (recordStartForm) {
    recordStartForm.addEventListener("submit", (e) => {
      e.preventDefault();
      if (isMotionRecording) {
        stopMotionRecording();
      } else {
        startMotionRecording();
      }
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
    btnRecordDiscard.addEventListener("click", () => {
      discardMotionRecording();
    });
  }

  if (btnRecordRerecord) {
    btnRecordRerecord.addEventListener("click", () => {
      discardMotionRecording();
      setTimeout(() => {
        openRecordStartDialog();
      }, 80);
    });
  }

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") {
      return;
    }
    if (recordStartDialog && recordStartDialog.open) {
      e.preventDefault();
      if (btnRecordStartConfirm) {
        btnRecordStartConfirm.click();
      }
      return;
    }
    if (recordNameDialog && recordNameDialog.open) {
      return;
    }
    const tag = (e.target && e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select" || e.isComposing) {
      return;
    }
    if (!btnRecordMotion && !btnRecordMotionPanel) {
      return;
    }
    e.preventDefault();
    const primary = btnRecordMotionPanel || btnRecordMotion;
    primary.click();
  });

  [btnOpenEvents].forEach((btn) => {
    if (!btn) {
      return;
    }
    btn.addEventListener("click", () => {
      eventDrawer.classList.remove("hidden");
    });
  });

  if (btnCloseDrawer) {
    btnCloseDrawer.addEventListener("click", () => {
      eventDrawer.classList.add("hidden");
    });
  }

  if (paneResizer && appShell) {
    const savedWidth = Number.parseInt(localStorage.getItem(SIDEBAR_WIDTH_KEY) || "", 10);
    setSidebarWidth(Number.isFinite(savedWidth) ? savedWidth : SIDEBAR_DEFAULT);

    paneResizer.addEventListener("pointerdown", (event) => {
      if (window.innerWidth <= 1100) {
        return;
      }
      isResizing = true;
      appShell.classList.add("is-resizing");
      paneResizer.setPointerCapture(event.pointerId);
      event.preventDefault();
    });

    paneResizer.addEventListener("pointermove", (event) => {
      if (!isResizing) {
        return;
      }
      setSidebarWidth(event.clientX - appShell.getBoundingClientRect().left);
    });

    paneResizer.addEventListener("pointerup", (event) => {
      if (!isResizing) {
        return;
      }
      isResizing = false;
      appShell.classList.remove("is-resizing");
      paneResizer.releasePointerCapture(event.pointerId);
      localStorage.setItem(SIDEBAR_WIDTH_KEY, getSidebarWidth());
    });

    paneResizer.addEventListener("pointercancel", () => {
      isResizing = false;
      appShell.classList.remove("is-resizing");
    });
  }

  groupToggles.forEach((btn) => {
    btn.addEventListener("click", () => {
      const group = btn.dataset.toggleGroup;
      const body = document.querySelector(`[data-group-body="${group}"]`);
      if (!body) {
        return;
      }
      const collapsed = body.classList.toggle("group-collapsed");
      btn.setAttribute("aria-expanded", collapsed ? "false" : "true");
    });
  });

  function addSystemMessage(text) {
    clearEmptyState();
    const note = document.createElement("div");
    note.className = "system-note";
    note.textContent = text;
    chatMessages.appendChild(note);
    scrollChat();
  }

  function clearEmptyState() {
    if (emptyState && emptyState.parentNode) {
      emptyState.parentNode.removeChild(emptyState);
    }
  }

  function nextId() {
    reqCounter += 1;
    return `r${reqCounter}_${Date.now().toString(36)}`;
  }

  function setSidebarWidth(width) {
    if (!appShell) {
      return;
    }
    const maxWidth = Math.max(SIDEBAR_MIN, Math.min(560, Math.floor(window.innerWidth * 0.55)));
    const clamped = Math.max(SIDEBAR_MIN, Math.min(maxWidth, Math.round(width)));
    appShell.style.setProperty("--left-rail-width", `${clamped}px`);
  }

  function getSidebarWidth() {
    if (!appShell) {
      return `${SIDEBAR_DEFAULT}`;
    }
    const value = appShell.style.getPropertyValue("--left-rail-width").trim();
    return value.replace("px", "") || `${SIDEBAR_DEFAULT}`;
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
    div.textContent = s;
    return div.innerHTML;
  }

  function formatAssistantText(text) {
    const lines = String(text || "").replace(/\r\n/g, "\n").split("\n");
    const blocks = [];
    let paragraph = [];
    let listItems = [];

    function flushParagraph() {
      if (!paragraph.length) {
        return;
      }
      blocks.push(`<p>${formatInlineMarkdown(paragraph.join("<br>"))}</p>`);
      paragraph = [];
    }

    function flushList() {
      if (!listItems.length) {
        return;
      }
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
  let selectedMicId = "";
  const micSelect = document.getElementById("mic-select");

  async function enumerateMics() {
    try {
      await navigator.mediaDevices.getUserMedia({ audio: true }).then((s) => s.getTracks().forEach((t) => t.stop()));
      const devices = await navigator.mediaDevices.enumerateDevices();
      const mics = devices.filter((d) => d.kind === "audioinput");
      micSelect.innerHTML = '<option value="">默认麦克风</option>';
      mics.forEach((d) => {
        const opt = document.createElement("option");
        opt.value = d.deviceId;
        opt.textContent = d.label || `麦克风 ${d.deviceId.slice(0, 8)}`;
        micSelect.appendChild(opt);
      });
      console.log("[voice] found", mics.length, "mic devices:", mics.map((d) => d.label));
    } catch (err) {
      console.warn("[voice] cannot enumerate mics:", err);
    }
  }

  micSelect.addEventListener("change", () => {
    selectedMicId = micSelect.value;
    console.log("[voice] selected mic:", selectedMicId || "(default)");
  });

  enumerateMics();

  const micGroup = document.querySelector(".mic-group");

  btnMic.addEventListener("click", () => {
    stopAllTts();
    void unlockTtsPlayback();
    if (!isVoiceMode) {
      startVoiceMode();
    }
  });

  btnVoiceCancel.addEventListener("click", () => {
    if (!isVoiceMode) return;
    voiceCancelled = true;
    if (mediaRecorder && mediaRecorder.state !== "inactive") {
      mediaRecorder.stop();
    }
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
    if (selectedMicId) {
      constraints.audio.deviceId = { exact: selectedMicId };
    }

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
      for (let i = 0; i < bufLen; i++) {
        ctx.lineTo(i * sliceWidth, ysBuf[i]);
      }
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
    if (!AudioContextCtor) {
      return null;
    }
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
    if (!ttsPlaying) {
      void playNextTts();
    }
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
    if (ttsPlaying) {
      return;
    }
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
      try {
        ttsCurrentSource.stop();
      } catch (_) {}
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

  updateRecordButtonState();
  connect();
})();
