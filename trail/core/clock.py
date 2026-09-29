"""Virtual time. The kit scores in virtual ms and may replay faster than real time.

The agent is never told the time scale, so it estimates it from event
timestamps versus arrival times. Deadlines are then set in virtual ms, so a
local `--time-scale 8` run behaves like the official scale-1 run.
"""

from __future__ import annotations

import statistics
from time import monotonic


class VirtualClock:
    def __init__(self) -> None:
        self._anchor: tuple[float, float] | None = None   # (real_s, virtual_ms)
        self._samples: list[float] = []
        self.scale = 1.0

    def observe(self, virtual_ms: float | None) -> None:
        if virtual_ms is None:
            return
        now = monotonic()
        if self._anchor is None:
            self._anchor = (now, float(virtual_ms))
            return
        real0, virt0 = self._anchor
        d_real = now - real0
        d_virt = float(virtual_ms) - virt0
        # Only well-separated samples say anything about the scale.
        if d_real > 0.02 and d_virt > 20:
            self._samples.append(d_virt / 1000.0 / d_real)
            self._samples = self._samples[-16:]
            est = statistics.median(self._samples)
            # The official scale is 1; local runs use 1-10. Clamp outliers.
            self.scale = min(max(est, 0.5), 20.0)

    def now_ms(self) -> float:
        if self._anchor is None:
            return 0.0
        real0, virt0 = self._anchor
        return virt0 + (monotonic() - real0) * 1000.0 * self.scale

    def real_seconds(self, virtual_ms: float) -> float:
        return max(virtual_ms, 0.0) / 1000.0 / self.scale
