"""Hardware sampling for the tray monitor (pure psutil + vendor APIs).

Supported vendors:
  * NVIDIA  - NVML through the ``pynvml`` / ``nvidia-ml-py`` package
              (Windows, Linux, macOS as long as a driver exposes NVML)
  * AMD     - ``/sys/class/drm`` sysfs on Linux, ``rocm-smi`` as fallback
  * Apple   - GPU name only (macOS exposes no public utilisation API)
  * Intel   - reported by name only

Every metric degrades to ``-1`` when the platform cannot provide it, so the UI
always knows the difference between "0 %" and "not available".
"""

from __future__ import annotations

import glob
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import time
import warnings
from dataclasses import dataclass, field

import psutil

# the legacy "pynvml" wrapper prints a noisy FutureWarning on import
warnings.filterwarnings("ignore", message=r".*pynvml package is deprecated.*")


IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _run(cmd: list[str], timeout: float = 1.5) -> str | None:
    """Run a small helper binary, returning stdout or None on any problem."""
    try:
        out = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if out.returncode == 0 and out.stdout:
            return out.stdout
    except Exception:
        pass
    return None


def _read(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read().strip()
    except Exception:
        return None


def default_disk_path() -> str:
    if IS_WINDOWS:
        return os.environ.get("SystemDrive", "C:") + "\\"
    return "/"


@dataclass
class GPUSample:
    index: int
    name: str
    vendor: str
    utilization: float = -1.0          # percent, -1 = unknown
    memory_total: int = -1
    memory_used: int = -1
    temperature: float = -1.0
    power: float = -1.0                 # watts

    @property
    def memory_percent(self) -> float:
        if self.memory_total and self.memory_total > 0 and self.memory_used >= 0:
            return self.memory_used / self.memory_total * 100.0
        return -1.0

    def to_dict(self) -> dict:
        data = {
            "index": self.index,
            "name": self.name,
            "vendor": self.vendor,
            "utilization": self.utilization,
            "memory_total": self.memory_total,
            "memory_used": self.memory_used,
            "memory_percent": self.memory_percent,
            "temperature": self.temperature,
            "power": self.power,
        }
        return data


# ---------------------------------------------------------------------------
# vendor backends
# ---------------------------------------------------------------------------
class _NvmlBackend:
    """NVIDIA Management Library."""

    vendor = "nvidia"

    def __init__(self) -> None:
        self.nvml = None
        self.ok = False
        try:
            import pynvml  # type: ignore

            self.nvml = pynvml
            pynvml.nvmlInit()
            self.ok = pynvml.nvmlDeviceGetCount() > 0
        except Exception:
            self.nvml = None
            self.ok = False

    def gpus(self) -> list[GPUSample]:
        if not self.ok:
            return []
        out: list[GPUSample] = []
        nvml = self.nvml
        for idx in range(nvml.nvmlDeviceGetCount()):
            try:
                handle = nvml.nvmlDeviceGetHandleByIndex(idx)
            except Exception:
                continue

            name = f"NVIDIA GPU {idx}"
            try:
                raw = nvml.nvmlDeviceGetName(handle)
                name = raw.decode("utf-8", "ignore") if isinstance(raw, bytes) else str(raw)
            except Exception:
                pass

            gpu = GPUSample(index=idx, name=name, vendor=self.vendor)

            try:
                gpu.utilization = float(nvml.nvmlDeviceGetUtilizationRates(handle).gpu)
            except Exception:
                pass

            try:
                mem = nvml.nvmlDeviceGetMemoryInfo(handle)
                gpu.memory_total = int(mem.total)
                gpu.memory_used = int(mem.used)
            except Exception:
                pass

            try:
                gpu.temperature = float(
                    nvml.nvmlDeviceGetTemperature(handle, nvml.NVML_TEMPERATURE_GPU)
                )
            except Exception:
                pass

            try:
                gpu.power = float(nvml.nvmlDeviceGetPowerUsage(handle)) / 1000.0
            except Exception:
                pass

            out.append(gpu)
        return out

    def close(self) -> None:
        if self.nvml is not None and self.ok:
            try:
                self.nvml.nvmlShutdown()
            except Exception:
                pass


class _AmdSysfsBackend:
    """AMD GPUs on Linux expose everything through /sys/class/drm."""

    vendor = "amd"

    def __init__(self) -> None:
        self.cards: list[str] = []
        if not IS_LINUX:
            return
        for path in sorted(glob.glob("/sys/class/drm/card[0-9]*/device")):
            if os.path.exists(os.path.join(path, "gpu_busy_percent")):
                self.cards.append(path)

    def gpus(self) -> list[GPUSample]:
        out: list[GPUSample] = []
        for idx, dev in enumerate(self.cards):
            name = _read(os.path.join(dev, "product_name")) or ""
            if not name:
                uevent = _read(os.path.join(dev, "uevent")) or ""
                match = re.search(r"PCI_ID=(\w+):(\w+)", uevent)
                name = f"AMD GPU {idx}"
                if match:
                    name = f"{name} [{match.group(1)}:{match.group(2)}]"

            gpu = GPUSample(index=idx, name=name, vendor=self.vendor)

            busy = _read(os.path.join(dev, "gpu_busy_percent"))
            if busy:
                try:
                    gpu.utilization = float(busy)
                except ValueError:
                    pass

            used = _read(os.path.join(dev, "mem_info_vram_used"))
            total = _read(os.path.join(dev, "mem_info_vram_total"))
            if used and total:
                try:
                    gpu.memory_used = int(used)
                    gpu.memory_total = int(total)
                except ValueError:
                    pass

            for hwmon in glob.glob(os.path.join(dev, "hwmon", "hwmon*")):
                raw = _read(os.path.join(hwmon, "temp1_input"))
                if raw:
                    try:
                        gpu.temperature = float(raw) / 1000.0
                        break
                    except ValueError:
                        pass

            out.append(gpu)
        return out


class _RocmSmiBackend:
    """Fallback for AMD on Linux when sysfs is not readable."""

    vendor = "amd"

    def __init__(self) -> None:
        self.binary = shutil.which("rocm-smi") if IS_LINUX else None

    def gpus(self) -> list[GPUSample]:
        if not self.binary:
            return []
        raw = _run([self.binary, "--showuse", "--showmeminfo", "vram", "--showtemp", "--json"], 2.0)
        if not raw:
            return []
        try:
            payload = json.loads(raw)
        except Exception:
            return []
        out: list[GPUSample] = []
        for idx, (key, data) in enumerate(payload.items()):
            if not isinstance(data, dict):
                continue
            gpu = GPUSample(index=idx, name=f"AMD GPU {key}", vendor=self.vendor)
            try:
                gpu.utilization = float(data.get("GPU use (%)", -1))
            except (TypeError, ValueError):
                pass
            try:
                gpu.memory_total = int(float(data.get("VRAM Total Memory (B)", -1)))
                gpu.memory_used = int(float(data.get("VRAM Total Used Memory (B)", -1)))
            except (TypeError, ValueError):
                pass
            try:
                gpu.temperature = float(data.get("Temperature (Sensor edge) (C)", -1))
            except (TypeError, ValueError):
                pass
            out.append(gpu)
        return out


class _AppleBackend:
    """macOS: only the GPU name is publicly available."""

    vendor = "apple"

    def __init__(self) -> None:
        self._name: str | None = None

    def gpus(self) -> list[GPUSample]:
        if not IS_MACOS:
            return []
        if self._name is None:
            name = ""
            raw = _run(["system_profiler", "SPDisplaysDataType"], 3.0)
            if raw:
                match = re.search(r"Chipset Model:\s*(.+)", raw)
                if match:
                    name = match.group(1).strip()
            self._name = name
        total = psutil.virtual_memory().total
        return [
            GPUSample(
                index=0,
                name=self._name or "Apple GPU",
                vendor=self.vendor,
                memory_total=total,  # unified memory
                memory_used=psutil.virtual_memory().used,
            )
        ]


class _CpuName:
    """Resolve a friendly CPU model string once, per platform."""

    def __init__(self) -> None:
        self.value = self._detect()

    @staticmethod
    def _detect() -> str:
        if IS_LINUX:
            raw = _read("/proc/cpuinfo") or ""
            match = re.search(r"model name\s*:\s*(.+)", raw)
            if match:
                return match.group(1).strip()
        if IS_MACOS:
            raw = _run(["sysctl", "-n", "machdep.cpu.brand_string"], 1.0)
            if raw:
                return raw.strip()
        if IS_WINDOWS:
            return platform.processor() or "Unknown CPU"
        return platform.processor() or "Unknown CPU"


# ---------------------------------------------------------------------------
# public sampler
# ---------------------------------------------------------------------------
class HardwareSampler:
    """Collects one snapshot of CPU / RAM / disk / GPU state."""

    def __init__(self, disk_path: str | None = None) -> None:
        self.disk_path = disk_path or default_disk_path()
        self.cpu_name = _CpuName().value
        self.host = socket.gethostname()
        self.os_name = f"{platform.system()} {platform.release()}"
        self.arch = platform.machine()

        self.backends = []
        self._nvml = None
        self._amd = None
        self._rocm = None
        self._apple = None

        try:
            nvml = _NvmlBackend()
            if nvml.ok:
                self._nvml = nvml
                self.backends.append(nvml)
        except Exception:
            pass

        try:
            amd = _AmdSysfsBackend()
            if amd.cards:
                self._amd = amd
                self.backends.append(amd)
        except Exception:
            pass

        if not self._amd:
            try:
                rocm = _RocmSmiBackend()
                if rocm.binary:
                    self._rocm = rocm
                    self.backends.append(rocm)
            except Exception:
                pass

        if IS_MACOS:
            try:
                apple = _AppleBackend()
                self._apple = apple
                self.backends.append(apple)
            except Exception:
                pass

        # prime psutil so the first cpu_percent() is not always 0
        try:
            psutil.cpu_percent(interval=None)
            psutil.cpu_percent(interval=None, percpu=True)
        except Exception:
            pass

    # -- discovery -------------------------------------------------------
    @property
    def has_gpu(self) -> bool:
        return bool(self.backends)

    def gpu_identity(self) -> list[dict]:
        """Static GPU list, used once at start-up for the header text."""
        out: list[dict] = []
        for backend in self.backends:
            try:
                for gpu in backend.gpus():
                    out.append({"index": gpu.index, "name": gpu.name, "vendor": gpu.vendor})
            except Exception:
                continue
        return out

    # -- sampling --------------------------------------------------------
    def _cpu_temperature(self) -> float:
        try:
            sensors = psutil.sensors_temperatures()  # type: ignore[attr-defined]
        except Exception:
            return -1.0
        if not sensors:
            return -1.0
        preferred = ("coretemp", "k10temp", "zenpower", "cpu_thermal", "cpu-thermal", "acpitz")
        for key in preferred:
            for entry in sensors.get(key, []):
                if entry.current:
                    return float(entry.current)
        for entries in sensors.values():
            for entry in entries:
                if entry.current:
                    return float(entry.current)
        return -1.0

    def sample(self) -> dict:
        cpu_percent = -1.0
        per_core: list[float] = []
        freq = -1.0
        try:
            cpu_percent = float(psutil.cpu_percent(interval=None))
            per_core = [float(v) for v in psutil.cpu_percent(interval=None, percpu=True)]
        except Exception:
            pass
        try:
            freq_obj = psutil.cpu_freq()
            if freq_obj and freq_obj.current:
                freq = float(freq_obj.current)
        except Exception:
            pass

        ram_total = ram_used = -1
        ram_percent = -1.0
        swap_total = swap_used = -1
        try:
            ram = psutil.virtual_memory()
            ram_total, ram_used, ram_percent = ram.total, ram.used, float(ram.percent)
        except Exception:
            pass
        try:
            swap = psutil.swap_memory()
            swap_total, swap_used = swap.total, swap.used
        except Exception:
            pass

        disk_total = disk_used = -1
        disk_percent = -1.0
        try:
            disk = psutil.disk_usage(self.disk_path)
            disk_total, disk_used, disk_percent = disk.total, disk.used, float(disk.percent)
        except Exception:
            pass

        gpus: list[dict] = []
        for backend in self.backends:
            try:
                for gpu in backend.gpus():
                    gpus.append(gpu.to_dict())
            except Exception:
                continue

        boot = -1.0
        try:
            boot = time.time() - psutil.boot_time()
        except Exception:
            pass

        return {
            "ts": time.time(),
            "host": self.host,
            "os": self.os_name,
            "arch": self.arch,
            "uptime": boot,
            "cpu": {
                "name": self.cpu_name,
                "percent": cpu_percent,
                "per_core": per_core,
                "freq": freq,
                "temp": self._cpu_temperature(),
                "cores": psutil.cpu_count(logical=False) or 0,
                "threads": psutil.cpu_count(logical=True) or 0,
            },
            "ram": {
                "total": ram_total,
                "used": ram_used,
                "percent": ram_percent,
                "swap_total": swap_total,
                "swap_used": swap_used,
            },
            "disk": {
                "path": self.disk_path,
                "total": disk_total,
                "used": disk_used,
                "percent": disk_percent,
            },
            "gpus": gpus,
        }

    def close(self) -> None:
        if self._nvml is not None:
            self._nvml.close()


# ---------------------------------------------------------------------------
# formatting helpers shared by the UI
# ---------------------------------------------------------------------------
def format_bytes(value: float | int | None, suffix: str = "") -> str:
    if value is None or value < 0:
        return "—"
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < step or unit == "TB":
            if unit == "B":
                return f"{int(value)} B{suffix}"
            return f"{value:.1f} {unit}{suffix}"
        value /= step
    return str(value)


def format_percent(value: float | int | None, digits: int = 0) -> str:
    if value is None or value < 0:
        return "—"
    return f"{value:.{digits}f}%"


def format_temp(value: float | int | None) -> str:
    if value is None or value < 0:
        return "—"
    return f"{value:.0f}°C"


def primary_usage(sample: dict) -> float:
    """The number rendered on the tray icon."""
    gpus = sample.get("gpus") or []
    if gpus:
        best = max((g.get("utilization", -1) or -1) for g in gpus)
        if best >= 0:
            return float(best)
    cpu = sample.get("cpu", {}).get("percent", -1)
    return float(cpu if cpu is not None else -1)
