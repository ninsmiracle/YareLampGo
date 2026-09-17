/* P4 maintenance stays alongside the existing on-site debugging controls. */
(() => {
  const dialog = document.getElementById("p4-maintenance-dialog");
  const status = document.getElementById("maintenance-status");
  const message = document.getElementById("maintenance-message");
  const preview = document.getElementById("maintenance-preview");
  const phases = {idle: "未进入维护", starting: "正在确认扭矩关闭", neutral: "请扶到中立位",
    capturing: "正在采集活动范围", review: "检查结果后可直接保存，无需回到中立位", saved: "校准已保存并同步到 P4",
    error: "采集已停止，请查看错误", commit_uncertain: "提交结果待核对，请下载诊断包"};
  let session = sessionStorage.getItem("lampgo-maintenance-session") || "";
  const returnButton = document.getElementById("btn-p4-return-safe");
  returnButton.addEventListener("click", async () => {
    returnButton.disabled = true;
    try {
      const response = await fetch("/api/invoke", {method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({skill_id: "return_safe", params: {}, wait: true})});
      const data = await response.json();
      if (!response.ok || !data.ok || data.result?.status === "error") throw new Error(data.error || data.result?.message || "回位失败，请查看诊断包");
    } catch (error) { window.alert(error.message); }
    finally { returnButton.disabled = false; }
  });
  window.addEventListener("lampgo:status", event => {
    const data = event.detail;
    const notice = document.getElementById("p4-motion-notice");
    notice.hidden = data.motor_transport !== "p4" || data.motor_startup_state === "ready";
    notice.textContent = data.motor_startup_state === "recovery_required" ?
      `舵机已连接，当前位置需要受控回位。请清空运动范围后点击“回到安全位”。${data.hardware_error || ""}` :
      (data.hardware_error || `P4 状态：${data.motor_startup_state || "等待连接"}。可在无线维护中查看状态并下载诊断包。`);
  });
  let busy = false;
  let state = {};
  const names = {base_yaw: "底座旋转", base_pitch: "底座俯仰", elbow_pitch: "肘部俯仰", wrist_roll: "手腕旋转", wrist_pitch: "顶部俯仰"};
  function render(next) {
    state = {...state, ...next};
    status.textContent = `${phases[state.phase] || state.phase} · 已采集 ${state.samples || 0} 帧`;
    const hardware = state.hardware || {};
    const diagnostic = document.getElementById("maintenance-hardware");
    const mode = hardware.maintenance ? "无线维护中，自动动作已暂停" :
      (hardware.recovery_reason || hardware.startup_state || "等待状态");
    diagnostic.textContent = state.transport !== "p4" ? "当前为 USB 路线，请在设置中选择 P4 无线控制。" :
      `P4 ${hardware.connected ? "已连接" : "未连接"} · 扭矩${hardware.torque_enabled ? "开启" : "关闭"} · ${mode}`;
    if (state.log_path) document.getElementById("runtime-log-location").textContent = state.log_path;
    if (state.error) message.textContent = `设备返回：${state.error}`;
    const canSaveWithoutNeutral = (hardware.device?.maintenance_version || 0) >= 2;
    const upgradeHint = "保存需要配套 P4 0.3.2 或更新固件；当前预览已保留，升级后可继续。";
    if (state.phase === "review" && !canSaveWithoutNeutral) message.textContent = upgradeHint;
    else if (message.textContent === upgradeHint) message.textContent = "";
    preview.replaceChildren();
    if (state.draft_available) {
      const hint = document.createElement("p"); hint.textContent = "已保留上次有效采样。点击“继续上次校准”即可恢复预览，无需重录。"; preview.append(hint);
    }
    if (state.preview) {
      const hint = document.createElement("p");
      hint.textContent = "以下使用记录时的同一套刻度。保存会沿用已记录的中立位，不会把当前姿态当成新的中立位。";
      preview.append(hint);
      const table = document.createElement("table");
      const header = table.insertRow();
      for (const label of ["关节", "编号", "当前位置", "采样范围", "已记中立位"]) { const th = document.createElement("th"); th.textContent = label; header.append(th); }
      const wrap = value => ((value % 4096) + 4096) % 4096;
      for (const [key, joint] of Object.entries(state.preview)) {
        if (key === "_meta") continue;
        const id = String(joint.id);
        const oldOffset = state.offsets?.[id];
        const shift = Number.isInteger(oldOffset) ? joint.homing_offset - oldOffset : 0;
        const low = wrap(joint.range_min + shift), high = wrap(joint.range_max + shift);
        const raw = hardware.device?.positions?.[id];
        const currentOffset = hardware.device?.metrics?.[id]?.homing_offset;
        const current = Number.isInteger(raw) && Number.isInteger(currentOffset) && Number.isInteger(oldOffset) ?
          wrap(raw + currentOffset - oldOffset) : null;
        const outside = current !== null && wrap(current - low) > joint.range_max - joint.range_min;
        const row = table.insertRow();
        for (const text of [names[key] || key, joint.id, current === null ? "—" : `${current}${outside ? "（范围外）" : ""}`,
          `${low}～${high}${low > high ? "（跨零）" : ""}`, state.neutral?.[id] ?? "—"]) row.insertCell().textContent = text;
      }
      preview.append(table);
      const details = document.createElement("details");
      const summary = document.createElement("summary"); summary.textContent = "校准后的内部刻度（调试）"; details.append(summary);
      for (const [key, joint] of Object.entries(state.preview)) {
        if (key === "_meta") continue;
        const line = document.createElement("p"); line.textContent = `${joint.id} 号：${joint.range_min}～${joint.range_max}，中立位 ${joint.neutral_raw}`;
        details.append(line);
      }
      preview.append(details);
    }
    if (state.backup) { const line = document.createElement("p"); line.textContent = `旧校准备份：${state.backup}`; preview.append(line); }
    dialog.querySelector('[data-maintenance-op="begin"]').textContent = state.draft_available ? "1. 继续上次校准" : "1. 进入维护";
    const allowed = {begin: !state.active && state.transport === "p4" && hardware.connected, neutral: ["neutral", "review"].includes(state.phase),
      preview: state.phase === "capturing", commit: state.phase === "review" && !!state.preview && canSaveWithoutNeutral,
      finish: state.active && state.phase !== "commit_uncertain"};
    dialog.querySelectorAll("[data-maintenance-op]").forEach(button => { button.disabled = busy || !allowed[button.dataset.maintenanceOp]; });
  }
  async function refresh() {
    if (busy) return;
    try {
      const response = await fetch("/api/maintenance"); const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "无法读取设备状态");
      render(data.result);
    } catch (error) {
      message.textContent = error instanceof TypeError ?
        "后端连接中断，暂时无法核对保存结果；请恢复连接后查看，不要重复保存。" : error.message;
    }
  }
  document.getElementById("btn-p4-maintenance").addEventListener("click", () => { dialog.showModal(); refresh(); });
  document.getElementById("maintenance-close").addEventListener("click", () => dialog.close());
  dialog.querySelectorAll("[data-maintenance-op]").forEach(button => button.addEventListener("click", async () => {
    const operation = button.dataset.maintenanceOp;
    const questions = {
      begin: "请扶稳灯臂、清空周围空间，并确认 USB 舵机驱动板已退出总线。进入维护后会释放扭矩。",
      commit: "确认保存已记录的中立位和采样范围？无需摆回原位置，只需短暂扶稳灯臂。将先备份旧校准；保存不会开启扭矩。",
      finish: "结束维护？若当前位置在工作范围内，将开启扭矩保持姿态；否则保持扭矩关闭，请随后点击回位。"
    };
    if (questions[operation] && !window.confirm(questions[operation])) return;
    busy = true; message.textContent = ""; render({});
    try {
      const response = await fetch("/api/maintenance", {method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({operation, session, confirmed: true})});
      const data = await response.json();
      if (data.result) render(data.result);
      if (!response.ok || !data.ok) throw new Error(data.error || "操作失败，请下载诊断包");
      if (data.result.session) { session = data.result.session; sessionStorage.setItem("lampgo-maintenance-session", session); }
      render(data.result);
    } catch (error) {
      message.textContent = error instanceof TypeError ?
        "请求连接中断，正在核对设备结果；请勿重复保存。" : error.message;
    }
    finally { busy = false; await refresh(); render({}); }
  }));
  setInterval(() => { if (dialog.open) refresh(); }, 1500);
})();
