"""Hardware parameters, environment fingerprints and the roofline bound.

The roofline combines the spec's byte and flop counts with the *measured* peak bandwidth and
peak compute for this device. Measured, not documented: the measured layer is what gives an
agent a feel for the machine it is actually running on, and it is re-measured when the
environment changes.

Nothing here knows what kind of accelerator it is. A device describes itself through
``hardware.yaml`` and through the numbers the calibration suite measured.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import paths, yamlite

DEFAULT_HARDWARE_ID = "accel-default"


class HardwareError(ValueError):
    """Raised when the hardware description is incomplete for the task at hand."""


def hardware_file(hw_id: str = "", root: Optional[Path] = None) -> Path:
    """Where the measured parameters for a device live."""
    identifier = hw_id or DEFAULT_HARDWARE_ID
    return paths.kb_dir(root) / "hardware" / "measured" / f"{identifier}.yaml"


def thresholds_file(hw_id: str = "", root: Optional[Path] = None) -> Path:
    identifier = hw_id or DEFAULT_HARDWARE_ID
    return paths.kb_dir(root) / "hardware" / "measured" / "thresholds.yaml"


def load_measured(hw_id: str = "", root: Optional[Path] = None) -> Dict[str, Any]:
    path = hardware_file(hw_id, root)
    if not path.is_file():
        raise HardwareError(
            f"no measured parameters for hardware {hw_id or DEFAULT_HARDWARE_ID!r}: run "
            f"`rsi evaluate calibrate` (or the calibrate-hardware skill) before roofline work"
        )
    return yamlite.load(path) or {}


def load_thresholds(root: Optional[Path] = None) -> Dict[str, Any]:
    path = thresholds_file(root=root)
    if not path.is_file():
        return {}
    return yamlite.load(path) or {}


def env_fingerprint(device: Optional[Dict[str, Any]] = None) -> str:
    """A stable, human-readable identity for the measurement environment.

    Two results are comparable only inside one fingerprint. It carries what actually changes
    floating-point and scheduling behaviour: the device, its driver or runtime, the compiler
    and its flags, and the host OS.
    """
    device = device or discover_device()
    parts = [
        f"hw={device.get('hw_id', 'unknown')}",
        f"device={device.get('model', 'unknown')}",
        f"runtime={device.get('runtime', 'unknown')}",
        f"driver={device.get('driver', 'unknown')}",
        f"compiler={device.get('compiler', 'unknown')}",
        f"flags={device.get('compiler_flags', 'default')}",
        f"os={platform.system()}-{platform.machine()}",
    ]
    return "|".join(str(part).replace(" ", "") for part in parts)


def discover_device(root: Optional[Path] = None) -> Dict[str, Any]:
    """Read the local device description, falling back to an unaccelerated placeholder.

    ``kb/hardware/measured/device.yaml`` is what a machine declares about itself; when it is
    absent the system runs against the reference backend and says so, rather than inventing
    peak numbers.
    """
    path = paths.kb_dir(root) / "hardware" / "measured" / "device.yaml"
    if path.is_file():
        data = yamlite.load(path) or {}
        data.setdefault("hw_id", DEFAULT_HARDWARE_ID)
        return data
    return {
        "hw_id": DEFAULT_HARDWARE_ID,
        "hw_class": "other",
        "model": "generic-cpu-reference",
        "runtime": "reference",
        "driver": "none",
        "compiler": "none",
        "backend": "cpu",
        "note": "no device description found; measurement is limited to the reference backend",
    }


def fingerprint_of(data: Dict[str, Any]) -> str:
    """Stable hash of a fingerprint string, for compact comparison and storage."""
    return hashlib.sha256(str(data).encode("utf-8")).hexdigest()[:16]


class Roofline:
    """Bound and attainment for one case.

    ``T_bound = max(bytes / peak_bandwidth, flops / peak_compute)`` and
    ``attained = T_bound / T_measured``. The attained fraction is the objective answer to
    "are we at the limit yet", which is what the campaign stop conditions consume.
    """

    def __init__(self, peak_bandwidth_gbps: float, peak_flops: Dict[str, float],
                 launch_overhead_us: float = 0.0) -> None:
        if peak_bandwidth_gbps <= 0:
            raise HardwareError("peak bandwidth must be positive: calibrate the device first")
        self.peak_bandwidth_gbps = float(peak_bandwidth_gbps)
        self.peak_flops = {name: float(value) for name, value in peak_flops.items()}
        self.launch_overhead_us = float(launch_overhead_us or 0.0)

    @classmethod
    def from_measured(cls, measured: Dict[str, Any]) -> "Roofline":
        bandwidth = measured.get("peak_bandwidth_gbps")
        flops = measured.get("peak_flops") or {}
        if bandwidth is None:
            raise HardwareError(
                "the measured layer has no peak_bandwidth_gbps: run the calibrate workflow"
            )
        if measured.get("trustworthy_for_roofline") is False:
            raise HardwareError(
                "the measured peaks describe the host, not the declared accelerator: "
                f"{measured.get('measurement_note') or 're-check device.yaml and re-measure'}. "
                "A roofline built on the wrong machine reports every kernel at a few percent "
                "attainment and sends the campaign after an unreachable bound"
            )
        launch = (measured.get("launch") or {}).get("overhead_us") or 0.0
        return cls(float(bandwidth), flops, float(launch))

    def bound_us(self, bytes_moved: float, flops: float, dtype: str = "fp32") -> Dict[str, Any]:
        memory_us = bytes_moved / (self.peak_bandwidth_gbps * 1e9) * 1e6
        peak = self.peak_flops.get(dtype)
        if peak is None:
            raise HardwareError(
                f"no measured peak compute for dtype {dtype!r}; the calibration suite must cover it"
            )
        compute_us = flops / peak * 1e6
        limiting = "device_mem" if memory_us >= compute_us else "compute"
        return {
            "bound_us": max(memory_us, compute_us),
            "memory_us": memory_us,
            "compute_us": compute_us,
            "limiting_term": limiting,
            "assumptions": [
                f"peak_bandwidth_gbps={self.peak_bandwidth_gbps:g} (measured)",
                f"peak_flops[{dtype}]={peak:g} (measured)",
                "no cache reuse assumed: bytes are the device-memory traffic in the cost model",
            ],
        }

    def attainment(self, bytes_moved: float, flops: float, measured_us: float,
                   dtype: str = "fp32") -> Dict[str, Any]:
        bound = self.bound_us(bytes_moved, flops, dtype)
        if measured_us <= 0:
            raise ValueError("measured time must be positive")
        attained = 100.0 * bound["bound_us"] / measured_us
        return {
            "bound_us": bound["bound_us"],
            "measured_us": measured_us,
            "attained_pct": attained,
            "limiting_term": bound["limiting_term"],
            "assumptions": bound["assumptions"],
        }

    def gap(self, attained_pct: float, target_pct: float = 85.0) -> Dict[str, Any]:
        """How far the kernel is from the campaign target, in attainment points."""
        return {
            "attained_pct": attained_pct,
            "target_pct": target_pct,
            "gap_points": max(0.0, target_pct - attained_pct),
            "at_target": attained_pct >= target_pct,
        }


def environment_summary(root: Optional[Path] = None) -> Dict[str, Any]:
    """Everything the ledger needs to interpret a result later."""
    device = discover_device(root)
    measured_path = hardware_file(str(device.get("hw_id", "")), root)
    measured_hash = None
    if measured_path.is_file():
        measured_hash = fingerprint_of(measured_path.read_text(encoding="utf-8"))
    return {
        "device": device,
        "hw_id": device.get("hw_id"),
        "hw_class": device.get("hw_class"),
        "fingerprint": env_fingerprint(device),
        "measured_parameters_hash": measured_hash,
        "python": platform.python_version(),
        "host": f"{platform.system()}-{platform.release()}-{platform.machine()}",
    }


def compiler_version() -> str:
    """Best-effort compiler identity for the fingerprint; empty when there is none."""
    for command in (["cc", "--version"], ["clang", "--version"], ["gcc", "--version"]):
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.splitlines()[0].strip()
    return "none"
