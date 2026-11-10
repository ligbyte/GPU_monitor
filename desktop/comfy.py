"""Optional ComfyUI link.

Keeps the "plugin" feeling of the original extension: queue counters are always
available over plain HTTP, and live step progress is streamed over the ComfyUI
websocket when ``websocket-client`` is installed.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request
import uuid

try:  # optional dependency
    import websocket  # type: ignore

    HAS_WS = True
except Exception:  # pragma: no cover - depends on environment
    websocket = None
    HAS_WS = False


class ComfyClient:
    def __init__(self, url: str = "http://127.0.0.1:8188") -> None:
        self.base_url = (url or "").strip().rstrip("/")
        self._lock = threading.Lock()
        self._state: dict = {
            "ok": False,
            "error": "",
            "running": 0,
            "pending": 0,
            "device": "",
            "vram_total": -1,
            "vram_free": -1,
            "version": "",
        }
        self._progress: dict = {
            "active": False,
            "value": 0,
            "max": 0,
            "node": "",
            "updated": 0.0,
        }
        self._ws_thread: threading.Thread | None = None
        self._ws_stop = threading.Event()
        self._client_id = uuid.uuid4().hex
        self._ws_url = ""

    # -- configuration ---------------------------------------------------
    def configure(self, url: str) -> None:
        url = (url or "").strip().rstrip("/")
        if url == self.base_url:
            return
        self.base_url = url
        self.stop_ws()
        self._ws_url = ""

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    def _http_json(self, path: str, timeout: float = 0.8):
        request = urllib.request.Request(
            self.base_url + path,
            headers={"Accept": "application/json", "User-Agent": "gpu-monitor-tray"},
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", "ignore"))

    # -- polling ---------------------------------------------------------
    def poll(self) -> dict:
        if not self.enabled:
            with self._lock:
                self._state.update({"ok": False, "error": "未配置"})
                return dict(self._state)

        state = {"ok": False, "error": "", "running": 0, "pending": 0}
        try:
            queue = self._http_json("/queue")
            running = queue.get("queue_running") or []
            pending = queue.get("queue_pending") or []
            state["ok"] = True
            state["running"] = len(running)
            state["pending"] = len(pending)
        except Exception as exc:
            state["error"] = self._short_error(exc)

        try:
            stats = self._http_json("/system_stats", timeout=0.8)
            system = stats.get("system") or {}
            devices = stats.get("devices") or []
            if devices:
                dev = devices[0]
                state["device"] = str(dev.get("name", ""))
                state["vram_total"] = int(dev.get("vram_total", -1))
                state["vram_free"] = int(dev.get("vram_free", -1))
            state["version"] = str(system.get("comfyui_version", "") or "")
            state["ok"] = state.get("ok") or bool(devices)
        except Exception:
            pass

        with self._lock:
            self._state.update(state)
            snapshot = dict(self._state)

        if snapshot.get("ok"):
            self._ensure_ws()
        return snapshot

    @staticmethod
    def _short_error(exc: Exception) -> str:
        text = str(exc)
        if isinstance(exc, urllib.error.URLError):  # type: ignore[attr-defined]
            reason = getattr(exc, "reason", "")
            text = str(reason) or text
        if len(text) > 60:
            text = text[:57] + "…"
        return text.replace("\n", " ")

    # -- websocket progress ---------------------------------------------
    def _ws_endpoint(self) -> str:
        parsed = urllib.parse.urlparse(self.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        host = parsed.netloc or "127.0.0.1:8188"
        prefix = parsed.path.rstrip("/")
        return f"{scheme}://{host}{prefix}/ws?clientId={self._client_id}"

    def _ensure_ws(self) -> None:
        if not HAS_WS or not self.base_url:
            return
        if self._ws_thread and self._ws_thread.is_alive():
            return
        self._ws_stop.clear()
        self._ws_thread = threading.Thread(target=self._ws_loop, name="comfy-ws", daemon=True)
        self._ws_thread.start()

    def _ws_loop(self) -> None:
        while not self._ws_stop.is_set():
            try:
                url = self._ws_endpoint()
                ws = websocket.WebSocketApp(  # type: ignore[union-attr]
                    url,
                    on_message=self._on_ws_message,
                    on_open=lambda *_: self._mark_idle(0.0),
                )
                ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception:
                pass
            self._ws_stop.wait(4.0)

    def _mark_idle(self, delay: float = 3.0) -> None:
        with self._lock:
            self._progress.update(
                {"active": False, "value": 0, "max": 0, "node": "", "updated": time.time()}
            )

    def _on_ws_message(self, _ws, message) -> None:
        if isinstance(message, bytes):
            return  # binary preview frames are not needed here
        try:
            payload = json.loads(message)
        except Exception:
            return
        kind = payload.get("type")
        data = payload.get("data") or {}
        now = time.time()

        with self._lock:
            if kind == "progress":
                maximum = int(data.get("max") or 0)
                value = int(data.get("value") or 0)
                self._progress.update(
                    {
                        "active": True,
                        "value": value,
                        "max": maximum,
                        "node": str(data.get("node") or ""),
                        "updated": now,
                    }
                )
            elif kind in ("execution_start", "execution_cached", "executing"):
                node = data.get("node")
                self._progress.update({"active": True, "node": str(node or ""), "updated": now})
                if kind == "executing" and node is None:
                    self._mark_idle()
            elif kind in ("execution_success", "execution_error", "execution_interrupted"):
                self._progress.update(
                    {"active": False, "value": 0, "max": 0, "node": "", "updated": now}
                )

    # -- readers ---------------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._state)

    def progress(self) -> dict:
        with self._lock:
            data = dict(self._progress)
        if data["active"] and time.time() - data.get("updated", 0) > 20:
            data["active"] = False
        return data

    def stop_ws(self) -> None:
        self._ws_stop.set()
