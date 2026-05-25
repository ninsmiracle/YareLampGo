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
import secrets
import socket
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import structlog

from lampgo.core.config import DeviceEsp32Config
from lampgo.personastore import lampgo_home

logger = structlog.get_logger(__name__)

MDNS_SERVICE_TYPE = "_lampgo-cam._tcp.local."
HEALTH_TTL_S = 5.0
OWNER_LEASE_TTL_MS = 120_000


def _lampgo_home() -> Path:
    return lampgo_home()


def _load_or_create_secret_file(filename: str, *, prefix: str = "", length: int = 32) -> str:
    path = _lampgo_home() / filename
    try:
        if path.exists():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
        path.parent.mkdir(parents=True, exist_ok=True)
        value = f"{prefix}{secrets.token_urlsafe(length)}"
        path.write_text(value + "\n", encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return value
    except Exception:
        return f"{prefix}{uuid.uuid4().hex}"


def _load_or_create_owner_id() -> str:
    path = _lampgo_home() / "esp32_owner_id"
    try:
        if path.exists():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
        path.parent.mkdir(parents=True, exist_ok=True)
        value = f"lampgo-{uuid.uuid4().hex[:12]}"
        path.write_text(value + "\n", encoding="utf-8")
        return value
    except Exception:
        return f"lampgo-{uuid.uuid4().hex[:12]}"


def _load_or_create_pairing_secret() -> str:
    return _load_or_create_secret_file("esp32_pairing_secret", length=32)


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
        self._preferred_fallback_snapshot: Esp32Device | None = None
        self._owner_id = _load_or_create_owner_id()
        self._owner_label = f"{socket.gethostname()}:{config.preferred_host or 'lampgo'}"
        self._pairing_secret = _load_or_create_pairing_secret()

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
            self._http = httpx.AsyncClient(timeout=self._config.http_timeout_s, trust_env=False)
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

    async def restart_discovery(self, *, clear_devices: bool = False, reason: str = "") -> None:
        if not self._config.enabled:
            return
        if clear_devices:
            async with self._lock:
                self._devices.clear()
            self._preferred_health_ok = False
            self._preferred_health_ok_at = 0.0
            self._preferred_fallback_snapshot = None
        await self.shutdown()
        await self.start()
        logger.info("esp32.discovery_restarted", clear_devices=clear_devices, reason=reason)

    def _on_service_state_change(self, zeroconf, service_type, name, state_change) -> None:
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
            fallback.last_health_ok = ok
            if ok:
                fallback.last_health_ok_at = time.monotonic()
            self._preferred_health_ok = ok
            if ok:
                self._preferred_health_ok_at = time.monotonic()
            self._preferred_fallback_snapshot = fallback

    async def _probe(self, dev: Esp32Device) -> bool:
        if self._http is None:
            return False
        try:
            resp = await self._http.get(
                f"{dev.base_url}/device/status",
                timeout=min(self._config.http_timeout_s, 2.0),
            )
            if resp.status_code < 400:
                try:
                    body = resp.json()
                except Exception:
                    body = None
                if isinstance(body, dict):
                    self._merge_device_status(dev, body)
                return True
            # Older / wedged firmware can occasionally answer OPTIONS while
            # the JSON status route is slow. Keep the device visible, but it
            # will not be treated as pairing-capable until status says so.
            resp = await self._http.options(
                f"{dev.base_url}/device/status",
                timeout=min(self._config.http_timeout_s, 2.0),
            )
            return resp.status_code < 400
        except Exception:
            return False

    def _merge_device_status(self, dev: Esp32Device, body: dict[str, Any]) -> None:
        for key in (
            "firmware",
            "hostname",
            "paired",
            "paired_owner_id",
            "paired_owner_label",
            "pairing_state",
            "pairing_supported",
            "ip",
            "led_ready",
            "led_mode",
            "led_mode_name",
            "led_brightness",
            "led_last_command",
            "led_last_write_ms",
            "led_driver",
            "led_pixel_pin",
            "led_pixel_count",
            "led_panel_count",
            "led_output_ok",
        ):
            if key in body:
                dev.extras[key] = body.get(key)
        if "firmware" in body and body.get("firmware"):
            dev.firmware = str(body.get("firmware") or "")
        if "hostname" in body and body.get("hostname"):
            dev.hostname = str(body.get("hostname") or dev.hostname)
        if "ip" in body and body.get("ip"):
            dev.ip = str(body.get("ip") or dev.ip)

    def _pairing_supported(self, dev: Esp32Device | None) -> bool:
        if dev is None:
            return False
        if "pairing_supported" in dev.extras:
            return bool(dev.extras.get("pairing_supported"))
        return "paired" in dev.extras or "pairing_state" in dev.extras

    def _paired_owner_id(self, dev: Esp32Device | None) -> str:
        if dev is None:
            return ""
        return str(dev.extras.get("paired_owner_id") or "")

    def _is_paired(self, dev: Esp32Device | None) -> bool:
        if dev is None:
            return False
        if "paired" in dev.extras:
            return bool(dev.extras.get("paired"))
        return bool(self._paired_owner_id(dev))

    def _is_paired_to_self(self, dev: Esp32Device | None) -> bool:
        return bool(self._paired_owner_id(dev) and self._paired_owner_id(dev) == self._owner_id)

    def _is_paired_to_other(self, dev: Esp32Device | None) -> bool:
        return bool(self._is_paired(dev) and not self._is_paired_to_self(dev))

    def _is_selectable(self, dev: Esp32Device | None) -> bool:
        return dev is not None and not self._is_paired_to_other(dev)

    def _pick_candidate(self) -> Esp32Device | None:
        preferred = self._config.preferred_host.strip()
        if preferred:
            for dev in self._devices.values():
                if preferred in (dev.host, dev.hostname, dev.ip):
                    return dev
            return self._preferred_fallback_device()
        if not self._devices:
            return None
        return max(self._devices.values(), key=lambda d: d.last_seen)

    def _pick_active(self) -> Esp32Device | None:
        preferred = self._config.preferred_host.strip()
        if preferred:
            candidate = self._pick_candidate()
            return candidate if self._is_selectable(candidate) else None
        candidates = [d for d in self._devices.values() if self._is_selectable(d)]
        if not candidates:
            return None
        return max(candidates, key=lambda d: d.last_seen)

    def _device_for_host(self, host: str | None) -> Esp32Device | None:
        wanted = (host or "").strip()
        if not wanted:
            return self._pick_candidate()
        for dev in self._devices.values():
            if wanted in (dev.host, dev.hostname, dev.ip):
                return dev
        ip = ""
        try:
            ip = str(ipaddress.ip_address(wanted))
        except ValueError:
            pass
        return Esp32Device(
            device_id=f"manual:{wanted}",
            host=wanted,
            ip=ip,
            port=80,
            hostname=wanted,
            last_seen=time.monotonic(),
            extras={"source": "manual_host"},
        )

    def _preferred_fallback_device(self) -> Esp32Device | None:
        preferred = self._config.preferred_host.strip()
        if not preferred:
            return None
        device_id = f"preferred:{preferred}"
        if self._preferred_fallback_snapshot and self._preferred_fallback_snapshot.device_id == device_id:
            return self._preferred_fallback_snapshot
        host = preferred
        ip = ""
        try:
            ip = str(ipaddress.ip_address(preferred))
        except ValueError:
            pass
        return Esp32Device(
            device_id=device_id,
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

    def has_active_device(self) -> bool:
        """Return True when discovery/preferred-host has an address to try.

        Media WebSockets live on the ESP32 stream server and can remain usable
        even when the main HTTP health endpoint is slow or wedged.
        """
        return self._config.enabled and self._pick_active() is not None

    def mark_active_healthy(self) -> None:
        """Mark the active device healthy after a successful non-HTTP channel.

        Some firmware paths keep WebSocket streaming responsive while HTTP
        health probes are slow. Treat a successful WS connection as liveness so
        the UI and stream URL builders don't falsely flip the ESP32 offline.
        """
        dev = self._pick_active()
        if dev is None:
            return
        now = time.monotonic()
        preferred = self._config.preferred_host.strip()
        if preferred and dev.device_id.startswith("preferred:"):
            self._preferred_health_ok = True
            self._preferred_health_ok_at = now
        for stored in self._devices.values():
            if stored.device_id == dev.device_id:
                stored.last_health_ok = True
                stored.last_health_ok_at = now
                stored.last_seen = now
                break

    def get_active_host(self) -> str | None:
        dev = self._pick_active()
        return dev.host if dev else None

    def get_active_base_url(self) -> str | None:
        dev = self._pick_active()
        return dev.base_url if dev else None

    @property
    def owner_id(self) -> str:
        return self._owner_id

    @property
    def owner_label(self) -> str:
        return self._owner_label

    @property
    def pairing_secret(self) -> str:
        return self._pairing_secret

    def ws_owner_query(self) -> str:
        return f"owner={quote(self._owner_id)}&token={quote(self._pairing_secret)}"

    def pairing_payload(self) -> dict[str, Any]:
        return {
            "owner_id": self._owner_id,
            "owner_label": self._owner_label,
            "pairing_secret": self._pairing_secret,
        }

    def owner_auth_payload(self, *, reason: str = "") -> dict[str, Any]:
        payload = self.pairing_payload()
        payload["ttl_ms"] = OWNER_LEASE_TTL_MS
        if reason:
            payload["reason"] = reason
        return payload

    def with_owner_auth(self, payload: dict[str, Any] | None = None, *, reason: str = "") -> dict[str, Any]:
        merged = dict(payload or {})
        merged.update(self.owner_auth_payload(reason=reason))
        return merged

    async def claim_owner(self, *, force: bool = False, reason: str = "") -> bool:
        """Claim exclusive ESP32 wake/audio ownership when firmware supports it.

        Formal pairing never steals a device from another computer. Unsupported
        firmware is treated as unavailable so it cannot broadcast audio to
        multiple backends again.
        """
        body = self.owner_auth_payload(reason=reason)
        if force:
            logger.warning("esp32.owner_claim_force_ignored", reason=reason)
        status, resp, _ = await self.proxy_post("/device/claim", body)
        if status == 404:
            logger.warning("esp32.owner_claim_unsupported", reason=reason)
            return False
        if 200 <= status < 300:
            logger.info("esp32.owner_claimed", owner_id=self._owner_id, reason=reason)
            return True
        logger.warning(
            "esp32.owner_claim_failed",
            status=status,
            body=str(resp)[:200],
            owner_id=self._owner_id,
            reason=reason,
        )
        return False

    async def pair_device(self, *, host: str | None = None, reason: str = "") -> tuple[bool, dict[str, Any], int]:
        dev = self._device_for_host(host)
        if dev is None or self._http is None:
            return False, {"ok": False, "error": "no_device"}, 503
        body = self.owner_auth_payload(reason=reason or "pair")
        status, resp, _ = await self._post_to_device(dev, "/device/pair", body)
        if status == 404:
            resp = {"ok": False, "error": "pairing_unsupported", "message": "ESP32 firmware must be updated"}
        if 200 <= status < 300:
            logger.info("esp32.device_paired", host=dev.host, owner_id=self._owner_id)
            if isinstance(resp, dict):
                self._merge_device_status(
                    dev,
                    {**resp, "paired": True, "paired_owner_id": self._owner_id, "pairing_supported": True},
                )
            return True, resp, status
        logger.warning("esp32.device_pair_failed", host=dev.host, status=status, body=str(resp)[:200])
        return False, resp, status

    async def unpair_device(self, *, reason: str = "") -> tuple[int, dict[str, Any]]:
        status, body, _ = await self.proxy_post("/device/unpair", self.owner_auth_payload(reason=reason or "unpair"))
        if 200 <= status < 300:
            logger.info("esp32.device_unpaired", owner_id=self._owner_id)
        return status, body

    async def release_owner(self, *, reason: str = "") -> tuple[int, dict[str, Any]]:
        status, body, _ = await self.proxy_post("/device/release", self.owner_auth_payload(reason=reason or "release"))
        return status, body

    def session_used(self) -> bool:
        return self._session_used

    def mark_session_used(self) -> None:
        self._session_used = True

    def reset_session(self) -> None:
        self._session_used = False

    def get_status(self) -> dict[str, Any]:
        dev = self._pick_active()
        raw_devices = [self._device_status_dict(d) for d in self._devices.values()]
        if dev is not None and not any(d.get("device_id") == dev.device_id for d in raw_devices):
            raw_devices.append(self._device_status_dict(dev))
        visible_devices = [d for d in raw_devices if not d.get("is_paired_to_other")]
        blocked_count = sum(1 for d in raw_devices if d.get("is_paired_to_other"))
        return {
            "enabled": self._config.enabled,
            "configured": dev is not None,
            "online": self.is_online(),
            "session_used": self._session_used,
            "preferred_host": self._config.preferred_host,
            "owner_id": self._owner_id,
            "owner_label": self._owner_label,
            "device": self._device_status_dict(dev) if dev else None,
            "all_devices": visible_devices,
            "blocked_devices_count": blocked_count,
        }

    def _device_status_dict(self, dev: Esp32Device | None) -> dict[str, Any]:
        if dev is None:
            return {}
        data = dev.to_status_dict()
        pairing_supported = self._pairing_supported(dev)
        paired = self._is_paired(dev)
        paired_owner_id = self._paired_owner_id(dev)
        paired_owner_label = str(dev.extras.get("paired_owner_label") or "")
        data.update(
            {
                "pairing_supported": pairing_supported,
                "paired": paired,
                "paired_owner_id": paired_owner_id,
                "paired_owner_label": paired_owner_label,
                "pairing_state": str(dev.extras.get("pairing_state") or ("paired" if paired else "unpaired")),
                "is_paired_to_self": self._is_paired_to_self(dev),
                "is_paired_to_other": self._is_paired_to_other(dev),
                "needs_firmware_update": not pairing_supported,
            }
        )
        return data

    async def _post_to_device(
        self,
        dev: Esp32Device,
        path: str,
        json_body: dict[str, Any] | None = None,
    ) -> tuple[int, dict[str, Any], str]:
        if self._http is None:
            return 503, {"ok": False, "error": "no_http_client"}, "application/json"
        try:
            resp = await self._http.post(f"{dev.base_url}{path}", json=json_body or {})
            if resp.status_code < 400:
                dev.last_health_ok = True
                dev.last_health_ok_at = time.monotonic()
                self.mark_active_healthy()
            content_type = resp.headers.get("content-type", "application/json")
            try:
                body = resp.json()
            except Exception:
                body = {"ok": resp.status_code < 400, "raw": resp.text}
            return resp.status_code, body, content_type
        except httpx.HTTPError as exc:
            return 502, {"ok": False, "error": f"proxy_failed: {exc}"}, "application/json"

    async def proxy_get(self, path: str) -> tuple[int, dict[str, Any] | bytes, str]:
        dev = self._pick_active()
        if dev is None or self._http is None:
            return 503, {"ok": False, "error": "no_device"}, "application/json"
        url = f"{dev.base_url}{path}"
        try:
            resp = await self._http.get(url)
            if resp.status_code < 400:
                dev.last_health_ok = True
                dev.last_health_ok_at = time.monotonic()
                self.mark_active_healthy()
            content_type = resp.headers.get("content-type", "")
            if "application/json" in content_type:
                try:
                    body = resp.json()
                    if path == "/device/status" and isinstance(body, dict):
                        self._merge_device_status(dev, body)
                    return resp.status_code, body, content_type
                except Exception:
                    pass
            return resp.status_code, resp.content, content_type
        except httpx.HTTPError as exc:
            return 502, {"ok": False, "error": f"proxy_failed: {exc}"}, "application/json"

    async def proxy_post(self, path: str, json_body: dict[str, Any] | None = None) -> tuple[int, dict[str, Any], str]:
        dev = self._pick_active()
        if dev is None or self._http is None:
            return 503, {"ok": False, "error": "no_device"}, "application/json"
        return await self._post_to_device(dev, path, json_body)

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
