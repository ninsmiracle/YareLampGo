"""Wireless teach-calibration; P4 remains the sole servo-bus owner."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import shutil
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

from lampgo.core.hal import MotorStartupState
from lampgo.core.p4_hal import P4ControlError, P4HardwareAbstraction
from lampgo.personastore import lampgo_home

logger = structlog.get_logger(__name__)


def calibration_preview(
    neutral: dict[str, int], offsets: dict[str, int], samples: list[dict[str, int]], motors
) -> dict[str, Any]:
    if len(samples) < 10:
        raise ValueError("采样不足，请缓慢移动每个关节后再结束")
    result: dict[str, Any] = {
        "_meta": {"schema_version": 2, "sts3215_single_turn_verified": True, "source": "p4-wireless-maintenance"}
    }
    for name, motor in motors.items():
        key = str(motor.id)
        center = neutral[key]
        values = [2047 + ((row[key] - center + 2048) % 4096 - 2048) for row in samples]
        low, high = min(values), max(values)
        span = high - low
        if not 64 <= low < high <= 4031 or not 128 <= span <= 3072:
            raise ValueError(f"{name}: 记录范围 {low}～{high} 不完整或接近编码器边界，请重新采集")
        if min(2047 - low, high - 2047) < max(32, math.ceil(span * 0.05)):
            raise ValueError(f"{name}: 中立位太接近范围边缘，请重新选择中立位并采集")
        offset = (offsets[key] + center - 2047 + 2048) % 4096 - 2048
        if offset == -2048:
            raise ValueError(f"{name}: 零点偏移位于不可编码边界，请微调中立位")
        result[name] = {
            "id": motor.id,
            "drive_mode": 0,
            "homing_offset": offset,
            "range_min": low,
            "range_max": high,
            "neutral_raw": 2047,
            "neutral_degrees": round((2047 - (low + high) / 2) * 360 / 4095, 1),
        }
    return result


class P4Maintenance:
    def __init__(self, server):
        self.server = server
        self.active = False
        self.token = ""
        self.phase = "idle"
        self.error = ""
        self.samples: list[dict[str, int]] = []
        self.neutral: dict[str, int] = {}
        self.offsets: dict[str, int] = {}
        self.preview: dict[str, Any] | None = None
        self.task: asyncio.Task | None = None
        self.lock = asyncio.Lock()
        self.backup: str | None = None
        self.pending: str | None = None
        self.saved_sample_count = 0

    @property
    def draft_path(self):
        return lampgo_home() / "maintenance" / f"{self.server.config.device.lamp_id}.json"

    def _calibration_hash(self):
        config = self.server.config.device
        path = config.calibration_dir / f"{config.lamp_id}.json"
        return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None

    def _save_draft(self):
        if not self.preview:
            return
        path = self.draft_path
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "schema": 1,
            "base_sha256": self._calibration_hash(),
            "preview": self.preview,
            "neutral": self.neutral,
            "offsets": self.offsets,
            "samples": len(self.samples) or self.saved_sample_count,
        }
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    def _load_draft(self):
        try:
            data = json.loads(self.draft_path.read_text(encoding="utf-8"))
            if data.get("schema") != 1 or data.get("base_sha256") != self._calibration_hash():
                return None
            expected = {str(m.id) for m in self.server.config.device.motors.values()}
            if set(data["neutral"]) != expected or set(data["offsets"]) != expected:
                return None
            if not isinstance(data["samples"], int) or data["samples"] < 10:
                return None
            for name, motor in self.server.config.device.motors.items():
                joint = data["preview"][name]
                if (
                    joint["id"] != motor.id
                    or not 64 <= joint["range_min"] < joint["neutral_raw"] < joint["range_max"] <= 4031
                ):
                    return None
            return data
        except (OSError, ValueError, KeyError, TypeError):
            return None

    @property
    def hal(self) -> P4HardwareAbstraction:
        hal = self.server.hal
        if not isinstance(hal, P4HardwareAbstraction) or not hal.is_connected:
            raise RuntimeError("请连接配套 P4 固件；USB 校准不能用于当前维护流程")
        return hal

    def status(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "phase": self.phase,
            "error": self.error,
            "samples": len(self.samples) or self.saved_sample_count,
            "preview": self.preview,
            "neutral": self.neutral,
            "offsets": self.offsets,
            "draft_available": not self.active and self._load_draft() is not None,
            "backup": self.backup,
            "pending": self.pending,
        }

    async def command(self, operation: str, body: dict[str, Any]) -> dict[str, Any]:
        async with self.lock:
            if operation == "begin":
                if body.get("confirmed") is not True:
                    raise ValueError("请先扶稳灯臂并确认释放扭矩")
                async with self.server._record_lock:
                    if self.active or self.server._record_recorder is not None:
                        raise RuntimeError("已有校准或录制会话，请先结束")
                    if self.server.lighting_mode.is_engaged:
                        raise RuntimeError("请先退出照明模式再进入维护")
                    hal = self.hal
                    self.active = True
                    self.token = uuid.uuid4().hex
                    self.phase, self.error = "starting", ""
                    self.samples, self.preview = [], None
                    self.saved_sample_count = 0
                    self.neutral, self.offsets = {}, {}
                    self.backup = self.pending = None
                    self.server.executor.set_motion_block_reason("P4 校准维护中，自动动作已暂停")
                    await self.server.executor.cancel_current()
                    await asyncio.to_thread(self.server.motion.stop)
                    try:
                        await asyncio.to_thread(hal.enter_maintenance)
                    except Exception:
                        self.phase = "error"
                        self.active = False
                        self.token = ""
                        raise
                    self.phase = "neutral"
                    draft = self._load_draft()
                    if draft:
                        self.neutral, self.offsets = draft["neutral"], draft["offsets"]
                        self.preview, self.saved_sample_count = draft["preview"], draft["samples"]
                        self.phase = "review"
                    logger.info("maintenance.started", transport="p4")
                    return {**self.status(), "session": self.token}
            if not self.active or body.get("session") != self.token:
                raise RuntimeError("维护会话已失效或由其他页面持有，请重新开始")
            self.error = ""
            if operation == "neutral":
                if self.phase not in {"neutral", "review"}:
                    raise ValueError("请先结束当前采集")
                rows = []
                for _ in range(3):
                    response = await asyncio.to_thread(self.hal.maintenance_snapshot)
                    rows.append(self.hal._require_raw_positions(response))
                    await asyncio.sleep(0.05)
                if any(max(row[k] for row in rows) - min(row[k] for row in rows) > 8 for k in rows[0]):
                    raise ValueError("灯臂还在移动，请扶稳中立位后重试")
                self.neutral = rows[-1]
                self.offsets = {key: int(response["metrics"][key]["homing_offset"]) for key in self.neutral}
                self.samples = [dict(self.neutral)]
                self.saved_sample_count = 0
                self.preview = None
                self.draft_path.unlink(missing_ok=True)
                self.phase = "capturing"
                self.task = asyncio.create_task(self._capture())
            elif operation == "preview":
                if self.phase != "capturing":
                    raise ValueError("当前没有正在进行的范围采集")
                await self._stop_capture()
                self.phase = "review"
                self.preview = calibration_preview(
                    self.neutral, self.offsets, self.samples, self.server.config.device.motors
                )
                self._save_draft()
            elif operation == "commit":
                if body.get("confirmed") is not True or self.phase != "review" or not self.preview:
                    raise ValueError("请预览有效校准并确认保存")
                if self.hal.transport_status().get("device", {}).get("maintenance_version", 0) < 2:
                    raise RuntimeError("请先升级 P4 至 0.3.2 或更新版本，以支持不回中立位保存；本次预览已保留")
                current = await asyncio.to_thread(self.hal.maintenance_snapshot)
                self.hal._require_raw_positions(current)
                # Neutral is a recorded reference, not a pose the user must
                # reproduce. Firmware validates the offset change against the
                # fresh save-time pose while all servos remain torque-off.
                await asyncio.to_thread(self._commit)
                self.phase = "saved"
                from lampgo.skills.builtin.motion_skills import set_calibration_home

                set_calibration_home(self.hal.get_calibration_home())
                logger.info(
                    "maintenance.calibration_saved", backup=self.backup, lamp_id=self.server.config.device.lamp_id
                )
            elif operation == "finish":
                if body.get("confirmed") is not True:
                    raise ValueError("请确认退出维护；范围内将保持当前姿态，范围外需点击回位")
                if self.phase == "commit_uncertain":
                    raise RuntimeError("校准提交结果不确定，请保留诊断包并重新连接核对，不可直接启用动作")
                await self._stop_capture()
                hal = self.hal
                await asyncio.to_thread(hal.exit_maintenance)
                self.active = False
                self.phase = "idle"
                await self._restore_motion()
            else:
                raise ValueError("未知维护操作")
            return self.status()

    async def _restore_motion(self):
        hal = self.hal
        if hal.startup_state is MotorStartupState.READY:
            await asyncio.to_thread(hal.enable_torque)
            self.server.executor.set_motion_block_reason(None)
            self.server.motion.start()
        else:
            self.server.executor.set_motion_block_reason(
                hal.recovery_reason or "P4 需要回位或重新校准",
                allow_return_safe_recovery=hal.recovery_required and hal.supports_remote_recovery,
            )

    async def _capture(self):
        deadline = time.monotonic() + 120
        try:
            while self.phase == "capturing" and time.monotonic() < deadline:
                response = await asyncio.to_thread(self.hal.maintenance_snapshot)
                self.samples.append(self.hal._require_raw_positions(response))
                await asyncio.sleep(0.05)
            if self.phase == "capturing":
                self.phase = "review"
                self.preview = calibration_preview(
                    self.neutral, self.offsets, self.samples, self.server.config.device.motors
                )
                self._save_draft()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.phase, self.error = "error", str(exc)
            logger.exception("maintenance.capture_failed")

    async def _stop_capture(self):
        task, self.task = self.task, None
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def _commit(self):
        config = self.server.config.device
        target = config.calibration_dir / f"{config.lamp_id}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        if target.exists():
            backup = lampgo_home() / "backups" / "calibration" / stamp / target.name
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup)
            self.backup = str(backup)
        pending = target.with_name(f"{target.stem}.pending-{stamp}.json")
        self.pending = str(pending)
        with pending.open("x", encoding="utf-8") as stream:
            json.dump(self.preview, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        self.phase = "commit_uncertain"
        try:
            self.hal.apply_calibration_file(pending)
        except P4ControlError as exc:
            outcome = exc.response.get("calibration_result")
            detail = exc.response.get("calibration_error") or str(exc)
            rollback = exc.response.get("rollback_error")
            if outcome in {"not_written", "rolled_back"}:
                self.phase = "review"
                prefix = "未改写舵机" if outcome == "not_written" else "旧校准寄存器已读回确认恢复"
                self.error = f"保存失败；{prefix}。原因：{detail}"
            else:
                self.error = f"校准结果待核对，请勿开启扭矩。写入错误：{detail}"
                if rollback:
                    self.error += f"；回滚核对：{rollback}"
            raise RuntimeError(self.error) from exc
        except Exception as exc:
            self.error = f"校准结果待核对，预览和待保存文件已保留：{exc}"
            raise RuntimeError(self.error) from exc
        os.replace(pending, target)
        self.pending = None
        self.draft_path.unlink(missing_ok=True)
