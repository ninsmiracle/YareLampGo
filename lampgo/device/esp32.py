"""ESP32 camera/mic device discovery + HTTP proxy.

Discovers lampgo-cam firmware (running on XIAO ESP32S3 Sense) on the LAN via
mDNS (``_lampgo-cam._tcp.local.``) and proxies management / snapshot requests
through the lampgo backend so the browser UI never has to talk directly to the
device during normal operation.

Fallback behavior (see lampgo.perception.camera.CameraCapture):
  * ``is_online() == False`` and ``session_used() == False`` → cold-fallback to
    local cv2 camera (banner in UI).
  * ``is_online() == False`` and ``session_used() == True``  → hot-fallback is
    disabled on purpose; capture returns None so the LLM can say "I can't see".
"""

from __future__ import annotations

import asyncio
import ipaddress
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

from lampgo.core.config import DeviceEsp32Config

logger = structlog.get_logger(__name__)

MDNS_SERVICE_TYPE = "_lampgo-cam._tcp.local."
HEALTH_TTL_S = 5.0


@dataclass
class Esp32Device:
    """Snapshot of one discovered device."""

    device_id: str
    host: str
    ip: str = ""
    port: int = 80
    firmware: str = ""
    hostname: str = ""
    last_seen: float = 0.0
    last_health_ok_at: float = 0.0
    last_health_ok: bool = False
    extras: dict[str, Any] = field(default_factory=dict)

    @property
    def base_url(self) -> str:
        if self.ip:
            return f"http://{self.ip}:{self.port}"
        return f"http://{self.host}:{self.port}"

    def to_status_dict(self) -> dict[str, Any]:
        return {
            "device_id": self.device_id,
            "host": self.host,
            "ip": self.ip,
            "port": self.port,
            "firmware": self.firmware,
            "hostname": self.hostname,
            "last_seen": self.last_seen,
            "last_health_ok_at": self.last_health_ok_at,
            **self.extras,
        }


class Esp32DeviceManager:
    """Background mDNS browser + HTTP proxy for one ESP32 camera device.

    One lampgo ↔ one ESP32 (by design). If multiple devices announce themselves
    the manager keeps the most recently-seen one as "active". User can pin a
    specific hostname via ``device_esp32.preferred_host``.
    """

    def __init__(self, config: DeviceEsp32Config) -> None:
        self._config = config
        self._devices: dict[str, Esp32Device] = {}
        self._lock = asyncio.Lock()
        self._zeroconf = None
        self._browser = None
        self._health_task: asyncio.Task | None = None
        self._session_used = False
        self._http: httpx.AsyncClient | None = None
        self._started = False
        self._preferred_health_ok = False
        self._preferred_health_ok_at = 0.0

    def update_config(self, config: DeviceEsp32Config) -> None:
        """Called when the user edits device_esp32.* at runtime."""
        self._config = config

    @property
    def enabled(self) -> bool:
        return bool(self._config.enabled)

    async def start(self) -> None:
        if self._started or not self._config.enabled:
            return
        try:
            from zeroconf import IPVersion
            from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf
        except ImportError:
            logger.warning("esp32.zeroconf_missing", msg="pip install zeroconf")
            return

        try:
            self._zeroconf = AsyncZeroconf(ip_version=IPVersion.V4Only)
            self._browser = AsyncServiceBrowser(
                self._zeroconf.zeroconf,
                [MDNS_SERVICE_TYPE],
                handlers=[self._on_service_state_change],
            )
            self._http = httpx.AsyncClient(timeout=self._config.http_timeout_s)
            self._health_task = asyncio.create_task(self._health_loop())
            self._started = True
            logger.info("esp32.discovery_started", service=MDNS_SERVICE_TYPE)
        except Exception:
            logger.exception("esp32.start_failed")

    async def shutdown(self) -> None:
        if self._health_task is not None:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
            self._health_task = None
        if self._browser is not None:
            try:
                await self._browser.async_cancel()
            except Exception:
                pass
            self._browser = None
        if self._zeroconf is not None:
            try:
                await self._zeroconf.async_close()
            except Exception:
                pass
            self._zeroconf = None
        if self._http is not None:
            try:
                await self._http.aclose()
            except Exception:
                pass
            self._http = None
        self._started = False
        logger.info("esp32.discovery_stopped")

    def _on_service_state_change(self, zeroconf, service_type, name, state_change) -> None:
        from zeroconf import ServiceStateChange

        asyncio.create_task(
            self._async_handle_state_change(zeroconf, service_type, name, state_change)
        )

    async def _async_handle_state_change(self, zeroconf, service_type, name, state_change) -> None:
        from zeroconf import ServiceStateChange

        if state_change == ServiceStateChange.Removed:
            async with self._lock:
                dev = self._devices.pop(name, None)
            if dev:
                logger.info("esp32.device_removed", name=name, host=dev.host)
            return

        try:
            info = await self._zeroconf.async_get_service_info(service_type, name, timeout=2000)
        except Exception:
            logger.exception("esp32.service_info_failed", name=name)
            return
        if info is None:
            return

        ip = ""
        try:
            addrs = info.parsed_addresses()
            if addrs:
                ip = addrs[0]
        except Exception:
            pass

        hostname = (info.server or "").rstrip(".")
        device_id = hostname or name

        extras: dict[str, Any] = {}
        try:
            if info.properties:
                for k, v in info.properties.items():
                    key = k.decode() if isinstance(k, (bytes, bytearray)) else str(k)
                    if isinstance(v, (bytes, bytearray)):
                        try:
                            extras[key] = v.decode()
                        except UnicodeDecodeError:
                            extras[key] = v.hex()
                    else:
                        extras[key] = str(v)
        except Exception:
            pass

        now = time.monotonic()
        async with self._lock:
            prev = self._devices.get(name)
            dev = Esp32Device(
                device_id=device_id,
                host=hostname,
                ip=ip,
                port=info.port or 80,
                firmware=extras.get("version", ""),
                hostname=hostname,
                last_seen=now,
                last_health_ok_at=(prev.last_health_ok_at if prev else 0.0),
                last_health_ok=(prev.last_health_ok if prev else False),
                extras=extras,
            )
            self._devices[name] = dev
        logger.info("esp32.device_discovered", name=name, host=hostname, ip=ip, port=dev.port)

    async def _health_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(3.0)
                await self._probe_all()
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("esp32.health_loop_failed")

    async def _probe_all(self) -> None:
        async with self._lock:
            devices = list(self._devices.values())
        for dev in devices:
            ok = await self._probe(dev)
            async with self._lock:
                if dev.device_id in [d.device_id for d in self._devices.values()]:
                    for stored in self._devices.values():
                        if stored.device_id == dev.device_id:
                            stored.last_health_ok = ok
                            if ok:
                                stored.last_health_ok_at = time.monotonic()
                            break
        # Some networks block mDNS multicast/broadcast across clients. In that
        # case we still allow an explicit preferred_host (IP/hostname) to work
        # as a direct fallback path.
        fallback = self._preferred_fallback_device()
        if fallback is not None:
            ok = await self._probe(fallback)
            self._preferred_health_ok = ok
            if ok:
                self._preferred_health_ok_at = time.monotonic()

    async def _probe(self, dev: Esp32Device) -> bool:
        if self._http is None:
            return False
        try:
            resp = await self._http.get(f"{dev.base_url}/device/status", timeout=self._config.http_timeout_s)
            return resp.status_code == 200
        except Exception:
            return False

    def _pick_active(self) -> Esp32Device | None:
        preferred = self._config.preferred_host.strip()
        if preferred:
            for dev in self._devices.values():
                if preferred in (dev.host, dev.hostname, dev.ip):
                    return dev
            return self._preferred_fallback_device()
        if not self._devices:
            return None
        return max(self._devices.values(), key=lambda d: d.last_seen)

    def _preferred_fallback_device(self) -> Esp32Device | None:
        preferred = self._config.preferred_host.strip()
        if not preferred:
            return None
        host = preferred
        ip = ""
        try:
            ip = str(ipaddress.ip_address(preferred))
        except ValueError:
            pass
        return Esp32Device(
            device_id=f"preferred:{preferred}",
            host=host,
            ip=ip,
            port=80,
            hostname=host,
            last_seen=time.monotonic(),
            last_health_ok=self._preferred_health_ok,
            last_health_ok_at=self._preferred_health_ok_at,
            extras={"source": "preferred_host"},
        )

    def is_online(self) -> bool:
        if not self._config.enabled:
            return False
        dev = self._pick_active()
        if dev is None:
            return False
        if not dev.last_health_ok:
            return False
        return (time.monotonic() - dev.last_health_ok_at) < HEALTH_TTL_S + 10.0

    def get_active_host(self) -> str | None:
        dev = self._pick_active()
        return dev.host if dev else None

    def get_active_base_url(self) -> str | None:
        dev = self._pick_active()
        return dev.base_url if dev else None

    def session_used(self) -> bool:
        return self._session_used

    def mark_session_used(self) -> None:
        self._session_used = True

    def reset_session(self) -> None:
        self._session_used = False

    def get_status(self) -> dict[str, Any]:
        dev = self._pick_active()
        return {
            "enabled": self._config.enabled,
            "configured": dev is not None,
            "online": self.is_online(),
            "session_used": self._session_used,
            "preferred_host": self._config.preferred_host,
            "device": dev.to_status_dict() if dev else None,
            "all_devices": [d.to_status_dict() for d in self._devices.values()],
        }

    async def proxy_get(self, path: str) -> tuple[int, dict[str, Any] | bytes, str]:
        dev = self._pick_active()
        if dev is None or self._http is None:
            return 503, {"ok": False, "error": "no_device"}, "application/json"
        url = f"{dev.base_url}{path}"
        try:
            resp = await self._http.get(url)
            content_type = resp.headers.get("content-type", "")
            if "application/json" in content_type:
                try:
                    return resp.status_code, resp.json(), content_type
                except Exception:
                    pass
            return resp.status_code, resp.content, content_type
        except httpx.HTTPError as exc:
            return 502, {"ok": False, "error": f"proxy_failed: {exc}"}, "application/json"

    async def proxy_post(self, path: str, json_body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any], str]:
        dev = self._pick_active()
        if dev is None or self._http is None:
            return 503, {"ok": False, "error": "no_device"}, "application/json"
        url = f"{dev.base_url}{path}"
        try:
            resp = await self._http.post(url, json=json_body or {})
            content_type = resp.headers.get("content-type", "application/json")
            try:
                body = resp.json()
            except Exception:
                body = {"ok": resp.status_code < 400, "raw": resp.text}
            return resp.status_code, body, content_type
        except httpx.HTTPError as exc:
            return 502, {"ok": False, "error": f"proxy_failed: {exc}"}, "application/json"

    async def snapshot_jpeg(self) -> bytes | None:
        dev = self._pick_active()
        if dev is None or self._http is None:
            return None
        try:
            resp = await self._http.get(f"{dev.base_url}/capture")
            if resp.status_code != 200:
                return None
            return resp.content
        except httpx.HTTPError:
            return None
