"""OpenClaw adapter — exposes lampgo skills and manages complex-task handoff."""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import structlog

from lampgo.core.events import (
    EventBus,
    OpenClawPromotionDecision,
    OpenClawPromotionRequested,
    OpenClawTaskUpdated,
)
from lampgo.core.types import InvokeResult
from lampgo.skills.base import Skill, SkillContext
from lampgo.skills.executor import SkillExecutor
from lampgo.skills.registry import SkillRegistry
from lampgo.bridge.openclaw_client import run_openclaw_agent

logger = structlog.get_logger(__name__)
INNOVATION_MARKERS = ("创新", "新动作", "表演", "编一个", "设计一个", "原创", "即兴", "新舞", "动作")
LOGIC_MARKERS = ("技能", "逻辑", "自动", "模式", "工作流", "代码")

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_NOISE_PREFIXES = ("[plugins]", "[debug]", "[info]", "[agent]", "[provider]")


def _clean_openclaw_output(text: str, *, tail_limit: int = 1200) -> str:
    """Strip ANSI escape codes and drop common plugin/debug noise lines."""
    if not text:
        return ""
    cleaned = _ANSI_RE.sub("", text)
    lines: list[str] = []
    for raw in cleaned.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        stripped = line.lstrip()
        if any(stripped.startswith(prefix) for prefix in _NOISE_PREFIXES):
            continue
        lines.append(line)
    if not lines:
        return cleaned.strip()
    joined = "\n".join(lines).strip()
    if len(joined) > tail_limit:
        joined = "…\n" + joined[-tail_limit:]
    return joined


@dataclass
class PromotionProposal:
    proposal_id: str
    proposal_type: str
    title: str
    summary: str
    files: list[str]
    risks: list[str]
    worker: str = "claude_code"
    promotion_target: str = ""
    status: str = "pending"

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "proposal_type": self.proposal_type,
            "title": self.title,
            "summary": self.summary,
            "files": list(self.files),
            "risks": list(self.risks),
            "worker": self.worker,
            "promotion_target": self.promotion_target,
            "status": self.status,
        }


@dataclass
class OpenClawTask:
    task_id: str
    request_id: str
    user_text: str
    reason: str
    source: str = "openclaw"
    status: str = "queued"
    detail: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    available_capability_count: int = 0
    recent_tool_calls: list[dict[str, Any]] = field(default_factory=list)
    proposals: list[PromotionProposal] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "request_id": self.request_id,
            "user_text": self.user_text,
            "reason": self.reason,
            "source": self.source,
            "status": self.status,
            "detail": self.detail,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "available_capability_count": self.available_capability_count,
            "recent_tool_calls": list(self.recent_tool_calls),
            "proposals": [proposal.to_dict() for proposal in self.proposals],
        }


class CapabilitySpec:
    """Describes one callable capability for external agents."""

    def __init__(self, skill: Skill) -> None:
        self.skill_id = skill.skill_id
        self.description = skill.description
        self.parameters = {
            name: {
                "type": spec.type,
                "description": spec.description,
                "required": spec.required,
                "default": spec.default,
            }
            for name, spec in skill.parameters.items()
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "description": self.description,
            "parameters": self.parameters,
        }


class OpenClawAdapter:
    """Bridge between OpenClaw protocol and lampgo's skill system."""

    def __init__(self, registry: SkillRegistry, executor: SkillExecutor, events: EventBus) -> None:
        self._registry = registry
        self._executor = executor
        self._events = events
        self._event_subscribers: list[Callable[..., Awaitable[None]]] = []
        self._tasks: dict[str, OpenClawTask] = {}
        self._lock = asyncio.Lock()

    def get_capabilities(self) -> list[CapabilitySpec]:
        return [CapabilitySpec(skill) for skill in self._registry.list_skills()]

    async def invoke(self, skill_id: str, params: dict, ctx: SkillContext) -> InvokeResult:
        logger.info("openclaw.invoke", skill_id=skill_id, params=params)
        return await self._executor.invoke(skill_id, ctx, **params)

    async def cancel(self, invocation_id: str) -> None:
        logger.info("openclaw.cancel", invocation_id=invocation_id)
        await self._executor.cancel_current()

    def subscribe_events(self, callback: Callable[..., Awaitable[None]]) -> None:
        self._event_subscribers.append(callback)

    def list_capabilities_dict(self) -> list[dict[str, Any]]:
        return [cap.to_dict() for cap in self.get_capabilities()]

    def list_tasks(self) -> list[dict[str, Any]]:
        return [task.to_dict() for task in sorted(self._tasks.values(), key=lambda item: item.created_at, reverse=True)]

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        task = self._tasks.get(task_id)
        return task.to_dict() if task is not None else None

    async def submit_complex_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = str(payload.get("request_id", ""))
        user_text = str(payload.get("user_text", "")).strip()
        reason = str(payload.get("reason") or payload.get("detail") or "需要 OpenClaw 慢路径处理").strip()
        task = OpenClawTask(
            task_id=f"oc_{uuid.uuid4().hex[:10]}",
            request_id=request_id,
            user_text=user_text,
            reason=reason,
            detail="已提交到 OpenClaw，准备规划复杂任务。",
            available_capability_count=len(payload.get("available_capabilities", [])),
            recent_tool_calls=list(payload.get("recent_tool_calls", [])),
        )
        async with self._lock:
            self._tasks[task.task_id] = task
        logger.info("openclaw.submit_complex_task", task_id=task.task_id, request_id=request_id, reason=reason)
        await self._publish_task_update(task)
        asyncio.create_task(self._plan_task(task.task_id, payload))
        return task.to_dict()

    async def confirm_promotion(self, task_id: str, proposal_id: str, decision: str) -> dict[str, Any]:
        normalized = "approve" if decision in {"approve", "approved", "confirm"} else "reject"
        user_text: str = ""
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise KeyError(task_id)
            proposal = next((item for item in task.proposals if item.proposal_id == proposal_id), None)
            if proposal is None:
                raise KeyError(proposal_id)
            proposal.status = "approved" if normalized == "approve" else "rejected"
            if normalized == "approve":
                task.status = "executing"
                task.detail = "已确认沉淀，正在调用 OpenClaw 执行..."
                user_text = task.user_text
            else:
                task.status = "rejected"
                task.detail = "已拒绝沉淀，保留 temporary 方案，不进入长期能力。"
            task.updated_at = time.time()
            task_snapshot = task.to_dict()
        logger.info(
            "openclaw.confirm_promotion",
            task_id=task_id,
            proposal_id=proposal_id,
            decision=normalized,
        )
        await self._events.publish(
            OpenClawPromotionDecision(
                request_id=task.request_id,
                task_id=task_id,
                proposal_id=proposal_id,
                decision=normalized,
                task=task_snapshot,
            )
        )
        await self._publish_task_update(task)

        # Spawn actual OpenClaw execution after approval.
        if normalized == "approve" and user_text:
            asyncio.create_task(self._execute_after_promotion(task_id, user_text, proposal))

        return task_snapshot

    async def _execute_after_promotion(
        self,
        task_id: str,
        user_text: str,
        proposal: PromotionProposal,
    ) -> None:
        """Run `openclaw agent` after user approves a promotion proposal."""
        message = (
            f"用户任务：{user_text}\n"
            f"沉淀类型：{proposal.promotion_target}\n"
            "指令：请使用 lampgo plugin tools（lampgo_status / lampgo_move / lampgo_expression / "
            "lampgo_save_recording 等）完成该任务，并通过 lampgo_save_recording 将最终动作保存为录制文件。\n"
            "lampgo_save_recording CSV 格式要求：\n"
            "- header: timestamp,base_yaw.pos,base_pitch.pos,elbow_pitch.pos,wrist_roll.pos,wrist_pitch.pos\n"
            "- timestamp 从 0 开始，每帧递增 1/fps（例如 30fps 则每帧 +0.033）\n"
            "- 角度单位均为度（degrees）\n"
            "- 示例行: 0.000,0,-45,65,0,5\n"
            "如需用户确认，请调用 lampgo_ask_user 工具。"
        )
        logger.info("openclaw.executing_after_promotion", task_id=task_id, message_preview=message[:120])
        result = await run_openclaw_agent(message, thinking="high")

        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            if result.ok:
                task.status = "completed"
                cleaned = _clean_openclaw_output(result.stdout)
                task.detail = f"OpenClaw 执行完成。\n{cleaned}".strip()
            else:
                task.status = "failed"
                cleaned = _clean_openclaw_output(result.stderr or result.stdout)
                task.detail = f"OpenClaw 执行失败\n{cleaned}".strip()
            task.updated_at = time.time()
        await self._publish_task_update(task)

    async def _plan_task(self, task_id: str, payload: dict[str, Any]) -> None:
        await asyncio.sleep(0)
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            task.status = "planning"
            task.detail = "OpenClaw 正在分析任务，并评估是否能复用现有能力。"
            task.updated_at = time.time()
        await self._publish_task_update(task)
        task_snapshot: dict[str, Any] | None = None
        proposal_snapshot: dict[str, Any] | None = None
        run_direct = False
        direct_user_text = ""

        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            proposal = self._build_proposal(task, payload)
            if proposal is None:
                task.status = "executing_with_existing_tools"
                task.detail = "未命中沉淀规则，直接交给 OpenClaw Agent 处理..."
                task.updated_at = time.time()
                run_direct = True
                direct_user_text = task.user_text
            else:
                task.proposals.append(proposal)
                task.status = "awaiting_promotion_confirmation"
                task.detail = "已生成 temporary 方案，等待确认是否继续执行并沉淀。"
                task.updated_at = time.time()
                task_snapshot = task.to_dict()
                proposal_snapshot = proposal.to_dict()

        await self._publish_task_update(task)

        if proposal is not None:
            await self._events.publish(
                OpenClawPromotionRequested(
                    request_id=payload.get("request_id", ""),
                    task_id=task_id,
                    proposal=proposal_snapshot or {},
                    task=task_snapshot or {},
                )
            )

        if run_direct and direct_user_text:
            asyncio.create_task(self._execute_direct(task_id, direct_user_text))
        return

    async def _execute_direct(self, task_id: str, user_text: str) -> None:
        """Run OpenClaw agent directly when no promotion proposal is required."""
        message = (
            f"用户任务：{user_text}\n"
            "指令：请直接完成该任务。如果需要操作 lampgo 机器人（动作/表情/状态/录制），"
            "请使用 lampgo plugin tools（lampgo_status / lampgo_move / lampgo_expression / "
            "lampgo_save_recording 等）。如需用户澄清，请调用 lampgo_ask_user。"
        )
        logger.info("openclaw.executing_direct", task_id=task_id, message_preview=message[:120])
        try:
            result = await run_openclaw_agent(message, thinking="high")
        except Exception as exc:  # noqa: BLE001
            logger.exception("openclaw.execute_direct_failed", task_id=task_id)
            async with self._lock:
                task = self._tasks.get(task_id)
                if task is None:
                    return
                task.status = "failed"
                task.detail = f"OpenClaw 调用异常：{exc}"
                task.updated_at = time.time()
            await self._publish_task_update(task)
            return

        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return
            if result.ok:
                task.status = "completed"
                cleaned = _clean_openclaw_output(result.stdout)
                task.detail = cleaned or "OpenClaw 已执行完成。"
            else:
                task.status = "failed"
                cleaned = _clean_openclaw_output(result.stderr or result.stdout)
                task.detail = f"OpenClaw 执行失败 (exit={result.exit_code})\n{cleaned}".strip()
            task.updated_at = time.time()
        await self._publish_task_update(task)

    async def _publish_task_update(self, task: OpenClawTask) -> None:
        snapshot = task.to_dict()
        await self._events.publish(OpenClawTaskUpdated(request_id=task.request_id, task=snapshot))

    def _build_proposal(self, task: OpenClawTask, payload: dict[str, Any]) -> PromotionProposal | None:
        text = task.user_text
        lowered = text.lower()
        slug = _slug_from_text(text, task.task_id)
        if any(marker in text for marker in INNOVATION_MARKERS):
            return PromotionProposal(
                proposal_id=f"proposal_{uuid.uuid4().hex[:8]}",
                proposal_type="recording_proposal",
                title="建议沉淀为新录制动作",
                summary=(
                    "OpenClaw 判断该请求更适合先生成一个 temporary 录制动作，"
                    "验证效果后再提升为长期 recording 能力。"
                ),
                files=[
                    f"assets/recordings/{slug}.csv",
                    "openclaw-skills/lampgo/SKILL.md",
                ],
                risks=[
                    "动作轨迹需要人工复核安全性",
                    "生成后的动作名称和语义映射可能需要调整",
                ],
                promotion_target="recording",
            )
        if any(marker in text for marker in LOGIC_MARKERS) or "code" in lowered:
            return PromotionProposal(
                proposal_id=f"proposal_{uuid.uuid4().hex[:8]}",
                proposal_type="builtin_skill_proposal",
                title="建议沉淀为 builtin skill",
                summary="OpenClaw 判断该请求需要新的逻辑型能力，建议先生成 temporary skill 草案，再确认 promoted。",
                files=[
                    f"lampgo/skills/builtin/{slug}_skill.py",
                    f"tests/test_{slug}_skill.py",
                ],
                risks=[
                    "新增 skill 需要补测试和文档",
                    "可能需要人工调整参数与安全边界",
                ],
                promotion_target="builtin_skill",
            )
        return None


def _slug_from_text(text: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    if slug:
        return slug[:32]
    return fallback
