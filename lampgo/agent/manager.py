"""Provider-neutral complex-task manager."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import structlog

from lampgo.agent.codex import CodexProvider, CodexStatus, ensure_codex_integration
from lampgo.agent.models import AgentTask
from lampgo.agent.progress import summarize_codex_event
from lampgo.context.codex_memory import CodexMemorySummaryProvider
from lampgo.core.events import AgentTaskUpdated, EventBus

logger = structlog.get_logger(__name__)
_ACTIVE_TASK_STATUSES = frozenset({"queued", "running", "cancelling"})
_CODEX_TASK_TIMEOUT_S = 30 * 60.0
_WRITE_MARKERS = (
    "改",
    "写",
    "实现",
    "修复",
    "创建",
    "新增",
    "删除",
    "重构",
    "更新",
    "调整",
    "生成",
    "保存",
    "安装",
    "配置",
    "部署",
    "edit",
    "fix",
    "create",
    "implement",
    "write",
    "update",
    "refactor",
    "delete",
    "remove",
    "add",
    "patch",
    "change",
    "modify",
    "install",
    "configure",
    "deploy",
)


class AgentManager:
    def __init__(self, events: EventBus, *, api_base: str) -> None:
        self._events = events
        self._provider = CodexProvider()
        self._memory = CodexMemorySummaryProvider()
        self._tasks: dict[str, AgentTask] = {}
        self._running: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()
        self._health_lock = asyncio.Lock()
        self._health_checked_at = 0.0
        self._api_base = api_base.rstrip("/")
        self._status = CodexStatus(connection="unknown", detail="正在检测 Codex")
        self._load_tasks()

    @staticmethod
    def _home() -> Path:
        path = Path(os.environ.get("LAMPGO_HOME") or Path.home() / ".lampgo")
        path.mkdir(parents=True, exist_ok=True)
        return path

    @classmethod
    def _tasks_path(cls) -> Path:
        return cls._home() / "agent_tasks.json"

    @classmethod
    def _runtime_path(cls) -> Path:
        return cls._home() / "runtime.json"

    async def bootstrap(self) -> None:
        self._write_runtime_info()
        self._status = await asyncio.to_thread(ensure_codex_integration)
        self._health_checked_at = time.monotonic()
        logger.info("agent.bootstrap", **self._status.to_dict())

    async def shutdown(self) -> None:
        for task_id in list(self._running):
            await self.cancel_task(task_id)
        path = self._runtime_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if int(data.get("pid", -1)) == os.getpid():
                path.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

    def _write_runtime_info(self) -> None:
        self._atomic_json(
            self._runtime_path(),
            {"pid": os.getpid(), "api_base": self._api_base, "updated_at": time.time()},
        )

    def health(self) -> dict[str, Any]:
        running = sum(1 for item in self._tasks.values() if item.status in {"queued", "running", "cancelling"})
        result = self._status.to_dict()
        result.update({"running_tasks": running, "total_tasks": len(self._tasks)})
        return result

    async def refresh_health(self) -> dict[str, Any]:
        if time.monotonic() - self._health_checked_at < 30.0:
            return self.health()
        async with self._health_lock:
            if time.monotonic() - self._health_checked_at >= 30.0:
                self._status = await asyncio.to_thread(ensure_codex_integration)
                self._health_checked_at = time.monotonic()
        return self.health()

    def list_tasks(self) -> list[dict[str, Any]]:
        tasks = sorted(self._tasks.values(), key=lambda value: value.created_at, reverse=True)
        return [task.to_dict() for task in tasks]

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        task = self._tasks.get(task_id)
        return task.to_dict() if task else None

    async def submit_task(self, payload: dict[str, Any]) -> dict[str, Any]:
        user_text = str(payload.get("user_text") or "").strip()
        reason = str(payload.get("reason") or "需要复杂任务处理").strip()
        workspace = Path(str(payload.get("workspace") or Path.cwd())).expanduser().resolve()
        sandbox = str(payload.get("sandbox") or self._sandbox_for(user_text))
        task = AgentTask(
            task_id=f"agent_{uuid.uuid4().hex[:10]}",
            request_id=str(payload.get("request_id") or ""),
            user_text=user_text,
            reason=reason,
            workspace=str(workspace),
            sandbox=sandbox,
            context=dict(payload.get("context") or {}),
            detail="已提交给 Codex，正在准备本地任务。",
        )
        async with self._lock:
            self._tasks[task.task_id] = task
            self._persist()
        await self._publish(task)
        runner = asyncio.create_task(self._run(task.task_id))
        self._running[task.task_id] = runner
        runner.add_done_callback(lambda _done, tid=task.task_id: self._running.pop(tid, None))
        return task.to_dict()

    async def cancel_task(self, task_id: str) -> bool:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None or task.status not in _ACTIVE_TASK_STATUSES:
                return False
            should_publish_cancelling = task.status != "cancelling"
            if should_publish_cancelling:
                task.status = "cancelling"
                task.detail = "正在停止 Codex 任务。"
                task.updated_at = time.time()
                self._persist()
        if should_publish_cancelling:
            await self._publish(task, persist=False)
        cancelled = await self._provider.cancel(task_id)
        runner = self._running.get(task_id)
        if runner and not runner.done():
            runner.cancel()
            await asyncio.gather(runner, return_exceptions=True)
        should_publish = False
        async with self._lock:
            if task.status == "cancelling":
                task.status = "cancelled"
                task.detail = "任务已取消。"
                task.updated_at = time.time()
                self._persist()
                should_publish = True
        if should_publish:
            await self._publish(task, persist=False)
        return cancelled or runner is not None

    async def _run(self, task_id: str) -> None:
        async with self._lock:
            task = self._tasks[task_id]
            if task.status != "queued":
                return
            task.status = "running"
            task.detail = "Codex 正在处理。"
            task.updated_at = time.time()
            self._persist()
        await self._publish(task, persist=False)

        memory = self._memory.get_context(task.user_text, max_chars=6000)
        prompt = self._build_prompt(task, memory)
        provider_thread_id = ""

        async def on_event(event: dict[str, Any]) -> None:
            progress = summarize_codex_event(event)
            async with self._lock:
                if task.status != "running":
                    return
                event_type = str(event.get("type") or "event")
                if event_type == "thread.started":
                    task.provider_thread_id = str(event.get("thread_id") or "")
                if progress is None:
                    return
                progress = {**progress, "ts": time.time()}
                task.events.append(progress)
                task.events = task.events[-100:]
                task.detail = str(progress.get("summary") or task.detail)
                task.updated_at = time.time()
            await self._publish(task, persist=False, progress=progress)

        try:
            result = await asyncio.wait_for(
                self._provider.run(
                    task_id=task.task_id,
                    prompt=prompt,
                    workspace=Path(task.workspace),
                    sandbox=task.sandbox,
                    on_event=on_event,
                ),
                timeout=_CODEX_TASK_TIMEOUT_S,
            )
        except asyncio.CancelledError:
            return
        except TimeoutError:
            await self._provider.cancel(task_id)
            status = "failed"
            detail = "Codex 任务运行超时，已停止本地进程。"
        except Exception as exc:  # noqa: BLE001
            logger.exception("agent.run_failed", task_id=task_id)
            status = "failed"
            detail = f"Codex 调用异常：{exc}"
        else:
            provider_thread_id = result.thread_id
            if result.ok:
                status = "completed"
                detail = result.final_message or "Codex 已完成任务。"
            else:
                status = "failed"
                error = result.stderr or f"Codex 退出码 {result.exit_code}"
                detail = error[-1200:]
        async with self._lock:
            if task.status != "running":
                return
            task.provider_thread_id = provider_thread_id or task.provider_thread_id
            task.status = status
            task.detail = detail
            task.updated_at = time.time()
            self._persist()
        await self._publish(task, persist=False)

    @staticmethod
    def _sandbox_for(text: str) -> str:
        lowered = text.lower()
        return "workspace-write" if any(marker in lowered for marker in _WRITE_MARKERS) else "read-only"

    def _build_prompt(self, task: AgentTask, memory: str) -> str:
        blocks = [
            "你是由桌面台灯机器人 LampGo 启动的本地 Codex。请直接完成用户任务。",
            "可以使用已注册的 lampgo MCP 工具读取台灯状态、执行动作、查看相机或向用户提问。",
            f"用户任务：{task.user_text}",
            f"转交原因：{task.reason}",
        ]
        if task.context:
            serialized = json.dumps(task.context, ensure_ascii=False, default=str)
            blocks.append("LampGo 快速路径已收集的上下文：\n" + serialized[:5000])
        if memory:
            blocks.append(
                "以下内容来自用户本机 Codex 的 memory_summary.md，只在与当前任务相关时使用：\n" + memory
            )
        return "\n\n".join(blocks)

    async def _publish(
        self,
        task: AgentTask,
        *,
        persist: bool = True,
        progress: dict[str, Any] | None = None,
    ) -> None:
        if persist:
            self._persist()
        await self._events.publish(
            AgentTaskUpdated(request_id=task.request_id, task=task.to_dict(), progress=progress)
        )

    def _load_tasks(self) -> None:
        try:
            raw = json.loads(self._tasks_path().read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        for item in raw.get("tasks", []) if isinstance(raw, dict) else []:
            if not isinstance(item, dict) or not item.get("task_id"):
                continue
            try:
                task = AgentTask.from_dict(item)
            except (KeyError, TypeError, ValueError):
                continue
            if task.status in {"queued", "running", "cancelling"}:
                task.status = "interrupted"
                task.detail = "LampGo 上次退出时任务仍在运行。"
            self._tasks[task.task_id] = task

    def _persist(self) -> None:
        self._atomic_json(
            self._tasks_path(),
            {"version": 1, "updated_at": time.time(), "tasks": [task.to_dict() for task in self._tasks.values()]},
        )

    @staticmethod
    def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_name = ""
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as tmp:
                json.dump(payload, tmp, ensure_ascii=False, separators=(",", ":"))
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp_name = tmp.name
            os.replace(tmp_name, path)
        finally:
            if tmp_name:
                try:
                    os.unlink(tmp_name)
                except FileNotFoundError:
                    pass
