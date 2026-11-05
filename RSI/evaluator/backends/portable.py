"""Portable backend: any array runtime that exposes the NumPy array API.

One code path serves every device that has an array library — NumPy on the host, CuPy or
PyTorch on a GPU, torch_npu or the Ascend equivalent on an NPU. The backend is picked by the
name the device description declares, so the framework carries no vendor branch:

``device.yaml``

.. code-block:: yaml

    hw_id: accel-a
    hw_class: npu
    backend: portable
    array_module: torch_npu        # or cupy, numpy, torch
    device: npu:0                  # passed to the array module unchanged

Guards here are adjacent allocations, because an array runtime gives no contiguous allocation
to place a canary inside. Correctness still detects out-of-bounds writes into *adjacent*
guards and, more importantly, detects a candidate that never wrote its output: the filling
sentinel survives. The mode is reported so nobody reads more into the guard result than it
supports.
"""

from __future__ import annotations

import importlib
import time
from typing import Any, Dict, List, Optional, Tuple

from . import BackendBase, GuardSpec, register

#: Array modules this backend can drive, by import name.
KNOWN_MODULES = ("numpy", "cupy", "torch", "torch_npu")

#: Sentinel written into an output buffer before a candidate runs.
SENTINEL = 1.0e38


class PortableBackend(BackendBase):
    """Drive whichever array runtime the device description names."""

    name = "portable"
    capabilities = ("timing", "array_runtime", "integer_bit_exact")

    def __init__(self, module_name: str = "numpy", device: Optional[str] = None,
                 guard_elements: int = 8) -> None:
        self.module_name = module_name
        self.device = device
        self._guard_elements = guard_elements
        self._module: Any = None
        self._array_module: Any = None

    # -- lifecycle --------------------------------------------------------
    def _import(self) -> Any:
        if self._module is not None:
            return self._module
        try:
            self._module = importlib.import_module(self.module_name)
        except ImportError as error:
            raise RuntimeError(f"array module {self.module_name!r} is not importable: {error}") from error
        self._array_module = self._module
        if self.module_name.startswith("torch"):
            self._init_torch()
        return self._module

    def _init_torch(self) -> None:
        torch = self._module
        if self.device:
            kind = str(self.device).split(":")[0]
            if kind in {"npu", "cuda", "xpu", "mps"}:
                setter = getattr(torch, kind, None)
                if setter is not None and hasattr(setter, "set_device"):
                    try:
                        setter.set_device(int(str(self.device).split(":")[1]) if ":" in str(self.device) else 0)
                    except Exception:  # pragma: no cover - device stack specific
                        pass

    def available(self) -> Tuple[bool, str]:
        try:
            module = self._import()
        except RuntimeError as error:
            return False, str(error)
        if self.module_name == "numpy":
            return True, "numpy available"
        if self.module_name.startswith("torch"):
            if self.device:
                kind = str(self.device).split(":")[0]
                if kind in {"npu", "cuda", "xpu", "mps"}:
                    runtime = getattr(module, kind, None)
                    if runtime is None:
                        return False, f"torch is installed but torch.{kind} is unavailable"
                    if hasattr(runtime, "is_available") and not runtime.is_available():
                        return False, f"torch.{kind}.is_available() is false"
            return True, "torch available"
        if self.module_name == "cupy":
            try:
                if not module.cuda.is_available():
                    return False, "cupy reports no runtime device"
            except AttributeError:
                return False, "cupy is installed without its runtime bindings"
        return True, f"{self.module_name} available"

    # -- buffers ----------------------------------------------------------
    def zeros(self, n: int) -> Any:
        module = self._import()
        array = module.zeros(int(n), dtype=self._float_dtype())
        return self._to_device(array)

    def from_list(self, values: List[float]) -> Any:
        module = self._import()
        if self.module_name.startswith("torch"):
            array = module.tensor(list(values), dtype=self._float_dtype())
        else:
            array = module.asarray(list(values), dtype=self._float_dtype())
        return self._to_device(array)

    def tolist(self, array: Any) -> List[float]:
        if self.module_name.startswith("torch"):
            return [float(value) for value in array.detach().to("cpu").tolist()]
        return [float(value) for value in array.tolist()]

    def fill(self, array: Any, value: float) -> None:
        if self.module_name.startswith("torch"):
            array.fill_(float(value))
            return
        array.fill(float(value))

    def sync(self) -> None:
        module = self._module or self._import()
        if self.module_name.startswith("torch"):
            kind = str(self.device or "").split(":")[0]
            runtime = getattr(module, kind, None) if kind else None
            if runtime is not None and hasattr(runtime, "synchronize"):
                runtime.synchronize()
            elif hasattr(module, "cuda") and hasattr(module.cuda, "synchronize"):
                module.cuda.synchronize()
            return
        if self.module_name == "cupy":
            module.cuda.Stream.null.synchronize()

    def now(self) -> float:
        return time.perf_counter()

    # -- description ------------------------------------------------------
    @property
    def guards(self) -> GuardSpec:
        return GuardSpec("adjacent", self._guard_elements,
                         "guards are separate allocations; an out-of-bounds write is detected "
                         "only when it lands on one of them")

    def device_description(self) -> Dict[str, Any]:
        module = self._module or self._import()
        version = getattr(module, "__version__", "unknown")
        return {
            "backend": self.name,
            "array_module": self.module_name,
            "array_module_version": str(version),
            "device": self.device,
            "capabilities": list(self.capabilities),
            "guards": self.guards.to_dict(),
        }

    def _float_dtype(self) -> Any:
        module = self._module or self._import()
        return module.float32

    def _to_device(self, array: Any) -> Any:
        if self.module_name.startswith("torch") and self.device:
            return array.to(self.device)
        if self.module_name == "cupy":
            return array
        return array


def _factory(config: Dict[str, Any]) -> PortableBackend:
    module_name, device = _configured(config)
    guard = int(config.get("guard_elements", 8))
    return PortableBackend(module_name, device, guard)


def _configured(config: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    """Read the array module from the device description, defaulting to NumPy."""
    import os

    module_name = config.get("array_module") or os.environ.get("RSI_ARRAY_MODULE") or "numpy"
    device = config.get("device") or os.environ.get("RSI_DEVICE") or None
    return str(module_name), device


register("portable", _factory)
