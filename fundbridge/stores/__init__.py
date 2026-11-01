"""Store wiring with visible fallbacks.

The CSR store always reads the active versioned dataset (DatasetCSRStore). MEMORY_BACKEND=meko|local:
if Meko is missing credentials or fails its health check, we fall back to the local memory store
and record a warning that the UI shows in its header.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..config import settings
from .csr_dataset import DatasetCSRStore
from .memory_base import MemoryStore

log = logging.getLogger("fundbridge.stores")


@dataclass
class Backends:
    csr: DatasetCSRStore
    memory: MemoryStore
    warnings: list[str] = field(default_factory=list)

    def status(self) -> dict:
        return {
            "memory_backend": self.memory.backend_name,
            "requested_memory_backend": settings.memory_backend,
            "data_source": self.csr.data_source(),
            # Shown in the UI header: dataset name, version, REAL/SAMPLE, FY range, row count.
            "dataset": self._dataset(),
            "warnings": self.warnings,
        }

    def _dataset(self) -> dict:
        m = self.csr.active_manifest()
        keys = ["dataset_id", "name", "version", "sha256", "data_source", "fy_range", "rows_loaded", "created_at"]
        return {k: m.get(k) for k in keys}


def build_backends() -> Backends:
    from .memory_local import LocalMemoryStore

    warnings: list[str] = []
    csr = DatasetCSRStore()

    memory: MemoryStore
    if settings.memory_backend == "meko":
        try:
            from .memory_meko import MekoMemoryStore

            memory = MekoMemoryStore.from_settings(settings)
            memory.health_check()
        except Exception as e:  # noqa: BLE001
            msg = f"Meko unavailable ({e.__class__.__name__}: {e}); using local memory store."
            log.warning(msg)
            warnings.append(msg)
            memory = LocalMemoryStore()
    else:
        memory = LocalMemoryStore()

    return Backends(csr=csr, memory=memory, warnings=warnings)
