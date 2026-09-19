"""In-memory provider latency metrics for lightweight monitoring."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict, deque

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_METRICS: dict[str, deque] = defaultdict(lambda: deque(maxlen=200))


def _percentile(values, percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * percentile))
    return ordered[index]


def _threshold_for(metric_name: str) -> float | None:
    env_map = {
        "stt_latency": "STT_LATENCY_WARN_MS",
        "tts_ttfb": "TTS_TTFB_WARN_MS",
        "tts_total": "TTS_TOTAL_WARN_MS",
    }
    default_map = {
        "stt_latency": 2500.0,
        "tts_ttfb": 1500.0,
        "tts_total": 8000.0,
    }
    env_name = env_map.get(metric_name)
    if not env_name:
        return None
    try:
        return float(os.getenv(env_name, str(default_map.get(metric_name, 0.0))) or "0") or None
    except ValueError:
        return None


def record_provider_metric(metric_name: str, provider: str, value_ms: float) -> None:
    key = f"{metric_name}:{provider}"
    sample = {"timestamp": time.time(), "value_ms": float(value_ms)}
    with _LOCK:
        _METRICS[key].append(sample)

    threshold = _threshold_for(metric_name)
    if threshold is not None and value_ms > threshold:
        logger.warning(
            "[PROVIDER ALERT] metric=%s provider=%s value_ms=%.1f threshold_ms=%.1f",
            metric_name,
            provider,
            value_ms,
            threshold,
        )


def snapshot_provider_metrics() -> dict:
    with _LOCK:
        items = {key: list(samples) for key, samples in _METRICS.items()}

    summary = {}
    for key, samples in items.items():
        values = [item["value_ms"] for item in samples]
        summary[key] = {
            "count": len(values),
            "latest_ms": values[-1] if values else 0.0,
            "p50_ms": _percentile(values, 0.50),
            "p95_ms": _percentile(values, 0.95),
            "samples": samples[-20:],
        }
    return {"metrics": summary}
