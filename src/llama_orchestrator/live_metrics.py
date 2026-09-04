"""Bounded, opt-in sampling of llama.cpp Prometheus metrics.

This module deliberately has no Tkinter dependency.  The GUI creates a monitor
only after the global live-metrics setting is enabled, so the disabled path has
no worker, HTTP client, timer, or retained samples.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace

import httpx

REQUIRED_METRICS = frozenset(
    {
        "llamacpp:prompt_tokens_total",
        "llamacpp:prompt_seconds_total",
        "llamacpp:tokens_predicted_total",
        "llamacpp:tokens_predicted_seconds_total",
        "llamacpp:requests_processing",
        "llamacpp:requests_deferred",
    }
)
MIN_POLL_INTERVAL_SECONDS = 0.5
MAX_POLL_INTERVAL_SECONDS = 60.0
MIN_REQUEST_TIMEOUT_SECONDS = 0.1
MAX_REQUEST_TIMEOUT_SECONDS = 10.0
MIN_HISTORY_CAPACITY = 10
MAX_HISTORY_CAPACITY = 600
MIN_PARALLEL_POLLS = 1
MAX_PARALLEL_POLLS = 8
ONE_MINUTE_SECONDS = 60.0
TEN_MINUTES_SECONDS = 600.0


@dataclass(frozen=True)
class LiveMetricsOptions:
    """Global live-monitor settings, all intentionally bounded."""

    enabled: bool = False
    poll_interval_seconds: float = 1.0
    request_timeout_seconds: float = 1.0
    history_capacity: int = 60
    max_parallel_polls: int = 4

    def normalized(self) -> LiveMetricsOptions:
        """Return safe settings even if a persisted value is malformed."""

        return LiveMetricsOptions(
            enabled=bool(self.enabled),
            poll_interval_seconds=_clamp_float(
                self.poll_interval_seconds,
                default=1.0,
                minimum=MIN_POLL_INTERVAL_SECONDS,
                maximum=MAX_POLL_INTERVAL_SECONDS,
            ),
            request_timeout_seconds=_clamp_float(
                self.request_timeout_seconds,
                default=1.0,
                minimum=MIN_REQUEST_TIMEOUT_SECONDS,
                maximum=MAX_REQUEST_TIMEOUT_SECONDS,
            ),
            history_capacity=_clamp_int(
                self.history_capacity,
                default=60,
                minimum=MIN_HISTORY_CAPACITY,
                maximum=MAX_HISTORY_CAPACITY,
            ),
            max_parallel_polls=_clamp_int(
                self.max_parallel_polls,
                default=4,
                minimum=MIN_PARALLEL_POLLS,
                maximum=MAX_PARALLEL_POLLS,
            ),
        )


@dataclass(frozen=True)
class MetricTarget:
    """One managed server eligible for global live monitoring."""

    name: str
    url: str
    configured_parallelism: int = 1


@dataclass(frozen=True)
class MetricSample:
    """One validated snapshot of llama.cpp's cumulative server metrics."""

    prompt_tokens_total: float
    prompt_seconds_total: float
    predicted_tokens_total: float
    predicted_seconds_total: float
    requests_processing: int
    requests_deferred: int
    collected_monotonic: float
    process_start_time_unix: str | None = None


@dataclass(frozen=True)
class LiveMetricSnapshot:
    """Presentation-neutral result for one monitored instance."""

    name: str
    status: str
    message: str
    scope: str
    prefill_tokens_per_second: float | None
    decode_tokens_per_second: float | None
    prefill_1m_tokens_per_second: float | None
    decode_1m_tokens_per_second: float | None
    prefill_10m_tokens_per_second: float | None
    decode_10m_tokens_per_second: float | None
    last_prompt_tokens_per_second: float | None
    last_decode_tokens_per_second: float | None
    prompt_in_progress: bool
    decode_in_progress: bool
    requests_processing: int | None
    requests_deferred: int | None
    sampled_monotonic: float


def parse_metrics_response(
    text: str,
    *,
    process_start_time_unix: str | None = None,
    collected_monotonic: float | None = None,
) -> MetricSample:
    """Parse only the fixed llama.cpp metrics contract needed by the GUI.

    Unknown metrics and Prometheus comments are ignored.  A duplicate required
    metric is rejected because this monitor has no label selector and a silent
    choice would make a server-wide rate ambiguous.
    """

    values: dict[str, float] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 2:
            continue
        metric_name = fields[0].split("{", 1)[0]
        if metric_name not in REQUIRED_METRICS:
            continue
        if metric_name in values:
            raise ValueError(f"duplicate required metric: {metric_name}")
        try:
            value = float(fields[1])
        except ValueError as exc:
            raise ValueError(f"invalid value for {metric_name}") from exc
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"invalid value for {metric_name}")
        values[metric_name] = value

    missing = sorted(REQUIRED_METRICS.difference(values))
    if missing:
        raise ValueError(f"missing required metrics: {', '.join(missing)}")

    return MetricSample(
        prompt_tokens_total=values["llamacpp:prompt_tokens_total"],
        prompt_seconds_total=values["llamacpp:prompt_seconds_total"],
        predicted_tokens_total=values["llamacpp:tokens_predicted_total"],
        predicted_seconds_total=values["llamacpp:tokens_predicted_seconds_total"],
        requests_processing=_as_nonnegative_int(values["llamacpp:requests_processing"], "requests_processing"),
        requests_deferred=_as_nonnegative_int(values["llamacpp:requests_deferred"], "requests_deferred"),
        collected_monotonic=collected_monotonic if collected_monotonic is not None else time.monotonic(),
        process_start_time_unix=process_start_time_unix or None,
    )


def calculate_live_snapshot(
    target: MetricTarget,
    previous: MetricSample | None,
    current: MetricSample,
) -> LiveMetricSnapshot:
    """Calculate server-wide phase rates from adjacent monotonic counters."""

    scope = (
        "shared server-wide"
        if current.requests_processing > 1 or target.configured_parallelism > 1
        else "server-wide"
    )
    if previous is None:
        return _snapshot(
            target.name,
            status="warming_up",
            message="Baseline captured; waiting for the next sample.",
            scope=scope,
            current=current,
        )

    if _server_restarted(previous, current) or _counter_regressed(previous, current):
        return _snapshot(
            target.name,
            status="warming_up",
            message="Server counters reset; baseline captured again.",
            scope=scope,
            current=current,
        )

    prefill_rate = _counter_rate(
        current.prompt_tokens_total - previous.prompt_tokens_total,
        current.prompt_seconds_total - previous.prompt_seconds_total,
    )
    decode_rate = _counter_rate(
        current.predicted_tokens_total - previous.predicted_tokens_total,
        current.predicted_seconds_total - previous.predicted_seconds_total,
    )
    if prefill_rate is None and decode_rate is None:
        message = "No new server work in this sample interval."
        status = "idle" if current.requests_processing == 0 and current.requests_deferred == 0 else "waiting"
    else:
        message = "Rates are aggregate across this server's active request slots."
        status = "active"
    return _snapshot(
        target.name,
        status=status,
        message=message,
        scope=scope,
        current=current,
        prefill_tokens_per_second=prefill_rate,
        decode_tokens_per_second=decode_rate,
    )


@dataclass(frozen=True)
class _RateDelta:
    """One counter delta, timestamped at the end of its polling interval."""

    sampled_monotonic: float
    tokens: float
    processing_seconds: float


class _RollingRateWindow:
    """Amortized O(1) rolling token rate with a bounded in-memory deque."""

    def __init__(self, duration_seconds: float) -> None:
        self.duration_seconds = duration_seconds
        self._deltas: deque[_RateDelta] = deque()
        self._tokens = 0.0
        self._processing_seconds = 0.0

    def reset(self) -> None:
        self._deltas.clear()
        self._tokens = 0.0
        self._processing_seconds = 0.0

    def update(self, sampled_monotonic: float, tokens: float, processing_seconds: float) -> None:
        if tokens > 0 and processing_seconds > 0:
            delta = _RateDelta(sampled_monotonic, tokens, processing_seconds)
            self._deltas.append(delta)
            self._tokens += tokens
            self._processing_seconds += processing_seconds
        cutoff = sampled_monotonic - self.duration_seconds
        while self._deltas and self._deltas[0].sampled_monotonic <= cutoff:
            expired = self._deltas.popleft()
            self._tokens -= expired.tokens
            self._processing_seconds -= expired.processing_seconds

    @property
    def rate(self) -> float | None:
        return _counter_rate(self._tokens, self._processing_seconds)


class _LastPhaseRate:
    """Retain the latest completed phase until its successor begins."""

    def __init__(self) -> None:
        self.active = False
        self.last_completed_rate: float | None = None

    def reset(self) -> None:
        self.active = False
        self.last_completed_rate = None

    def start(self) -> None:
        self.active = True
        self.last_completed_rate = None

    def complete(self, tokens: float, processing_seconds: float) -> None:
        self.last_completed_rate = _counter_rate(tokens, processing_seconds)
        self.active = False

    def finish_without_result(self) -> None:
        self.active = False


class LiveMetricTracker:
    """Stateful low-cost rate tracker for one llama.cpp server.

    Four rolling accumulators make each update constant-time regardless of the
    ten-minute window length.  llama.cpp publishes prompt and decode totals when
    a phase completes, so whole-phase values are retained from the latest
    positive delta. Parallel slots remain an explicitly server-wide approximation.
    """

    def __init__(self, target: MetricTarget) -> None:
        self.target = target
        self._previous: MetricSample | None = None
        self._prefill_1m = _RollingRateWindow(ONE_MINUTE_SECONDS)
        self._decode_1m = _RollingRateWindow(ONE_MINUTE_SECONDS)
        self._prefill_10m = _RollingRateWindow(TEN_MINUTES_SECONDS)
        self._decode_10m = _RollingRateWindow(TEN_MINUTES_SECONDS)
        self._prompt_phase = _LastPhaseRate()
        self._decode_phase = _LastPhaseRate()
        self._last_snapshot: LiveMetricSnapshot | None = None

    def update_target(self, target: MetricTarget) -> None:
        """Refresh display metadata without discarding the counter baseline."""

        self.target = target

    def add_sample(self, current: MetricSample) -> LiveMetricSnapshot:
        """Consume one cumulative sample and return all presentation rates."""

        previous = self._previous
        self._previous = current
        scope = _metric_scope(self.target, current)
        if previous is None:
            snapshot = _snapshot(
                self.target.name,
                status="warming_up",
                message="Baseline captured; waiting for the next sample.",
                scope=scope,
                current=current,
            )
            self._last_snapshot = snapshot
            return snapshot

        if _server_restarted(previous, current) or _counter_regressed(previous, current):
            self._reset_rates()
            snapshot = _snapshot(
                self.target.name,
                status="warming_up",
                message="Server counters reset; baseline captured again.",
                scope=scope,
                current=current,
            )
            self._last_snapshot = snapshot
            return snapshot

        prompt_tokens = current.prompt_tokens_total - previous.prompt_tokens_total
        prompt_seconds = current.prompt_seconds_total - previous.prompt_seconds_total
        decode_tokens = current.predicted_tokens_total - previous.predicted_tokens_total
        decode_seconds = current.predicted_seconds_total - previous.predicted_seconds_total
        now = current.collected_monotonic

        self._prefill_1m.update(now, prompt_tokens, prompt_seconds)
        self._decode_1m.update(now, decode_tokens, decode_seconds)
        self._prefill_10m.update(now, prompt_tokens, prompt_seconds)
        self._decode_10m.update(now, decode_tokens, decode_seconds)
        prefill_rate = _counter_rate(prompt_tokens, prompt_seconds)
        decode_rate = _counter_rate(decode_tokens, decode_seconds)
        if current.requests_processing > previous.requests_processing:
            self._prompt_phase.start()
        if prefill_rate is not None:
            self._prompt_phase.complete(prompt_tokens, prompt_seconds)
            self._decode_phase.start()
        if decode_rate is not None:
            self._decode_phase.complete(decode_tokens, decode_seconds)
        if current.requests_processing == 0:
            if self._prompt_phase.active:
                self._prompt_phase.finish_without_result()
            if self._decode_phase.active:
                self._decode_phase.finish_without_result()
        if prefill_rate is None and decode_rate is None:
            message = "No new server work in this sample interval."
            status = (
                "idle"
                if current.requests_processing == 0 and current.requests_deferred == 0
                else "waiting"
            )
        else:
            message = "Rates are aggregate across this server's active request slots."
            status = "active"
        snapshot = _snapshot(
            self.target.name,
            status=status,
            message=message,
            scope=scope,
            current=current,
            prefill_tokens_per_second=prefill_rate,
            decode_tokens_per_second=decode_rate,
            prefill_1m_tokens_per_second=self._prefill_1m.rate,
            decode_1m_tokens_per_second=self._decode_1m.rate,
            prefill_10m_tokens_per_second=self._prefill_10m.rate,
            decode_10m_tokens_per_second=self._decode_10m.rate,
            last_prompt_tokens_per_second=self._prompt_phase.last_completed_rate,
            last_decode_tokens_per_second=self._decode_phase.last_completed_rate,
            prompt_in_progress=self._prompt_phase.active,
            decode_in_progress=self._decode_phase.active,
        )
        self._last_snapshot = snapshot
        return snapshot

    def unavailable(self, message: str) -> LiveMetricSnapshot:
        """Keep completed/rolling values visible across a transient poll error."""

        if self._last_snapshot is None:
            return unavailable_snapshot(self.target.name, message)
        snapshot = replace(
            self._last_snapshot,
            status="unavailable",
            message=message,
            prefill_tokens_per_second=None,
            decode_tokens_per_second=None,
            sampled_monotonic=time.monotonic(),
        )
        self._last_snapshot = snapshot
        return snapshot

    def _reset_rates(self) -> None:
        for window in (
            self._prefill_1m,
            self._decode_1m,
            self._prefill_10m,
            self._decode_10m,
        ):
            window.reset()
        self._prompt_phase.reset()
        self._decode_phase.reset()


def unavailable_snapshot(name: str, message: str) -> LiveMetricSnapshot:
    """Create a failure snapshot without retaining a misleading old rate."""

    return LiveMetricSnapshot(
        name=name,
        status="unavailable",
        message=message,
        scope="server-wide",
        prefill_tokens_per_second=None,
        decode_tokens_per_second=None,
        prefill_1m_tokens_per_second=None,
        decode_1m_tokens_per_second=None,
        prefill_10m_tokens_per_second=None,
        decode_10m_tokens_per_second=None,
        last_prompt_tokens_per_second=None,
        last_decode_tokens_per_second=None,
        prompt_in_progress=False,
        decode_in_progress=False,
        requests_processing=None,
        requests_deferred=None,
        sampled_monotonic=time.monotonic(),
    )


def fetch_metric_sample(target: MetricTarget, request_timeout_seconds: float) -> MetricSample:
    """Fetch one local llama.cpp endpoint with a strict timeout."""

    response = httpx.get(
        target.url,
        timeout=request_timeout_seconds,
        headers={"Accept": "text/plain; version=0.0.4"},
    )
    response.raise_for_status()
    return parse_metrics_response(
        response.text,
        process_start_time_unix=response.headers.get("Process-Start-Time-Unix"),
    )


class LiveMetricsMonitor:
    """Bounded background poller started only while global monitoring is enabled."""

    def __init__(
        self,
        options: LiveMetricsOptions,
        on_snapshots: Callable[[tuple[LiveMetricSnapshot, ...]], None],
        *,
        fetcher: Callable[[MetricTarget, float], MetricSample] = fetch_metric_sample,
    ) -> None:
        self._options = options.normalized()
        self._on_snapshots = on_snapshots
        self._fetcher = fetcher
        self._targets: tuple[MetricTarget, ...] = ()
        self._trackers: dict[str, LiveMetricTracker] = {}
        self._history: dict[str, deque[LiveMetricSnapshot]] = {}
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._executor: ThreadPoolExecutor | None = None

    @property
    def running(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def start(self) -> None:
        """Start no background work unless monitoring is explicitly enabled."""

        if not self._options.enabled or self.running:
            return
        self._stop_event.clear()
        self._executor = ThreadPoolExecutor(
            max_workers=self._options.max_parallel_polls,
            thread_name_prefix="LiveMetricsPoll",
        )
        self._worker = threading.Thread(
            target=self._run,
            name="LiveMetricsCoordinator",
            daemon=True,
        )
        self._worker.start()

    def stop(self) -> None:
        """Stop polling promptly and release all resources created by ``start``."""

        self._stop_event.set()
        worker = self._worker
        if worker is not None:
            worker.join(timeout=self._options.request_timeout_seconds + 1.0)
        executor = self._executor
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)
        self._worker = None
        self._executor = None

    def update_targets(self, targets: Iterable[MetricTarget]) -> None:
        """Atomically replace eligible running instances and discard stale state."""

        normalized = tuple(sorted({target.name: target for target in targets}.values(), key=lambda item: item.name))
        names = {target.name for target in normalized}
        with self._lock:
            self._targets = normalized
            self._trackers = {name: tracker for name, tracker in self._trackers.items() if name in names}
            self._history = {name: history for name, history in self._history.items() if name in names}
            for target in normalized:
                tracker = self._trackers.get(target.name)
                if tracker is not None:
                    tracker.update_target(target)

    def history_for(self, name: str) -> tuple[LiveMetricSnapshot, ...]:
        """Return only bounded in-memory history; nothing is persisted."""

        with self._lock:
            return tuple(self._history.get(name, ()))

    def _run(self) -> None:
        while not self._stop_event.is_set():
            cycle_started = time.monotonic()
            with self._lock:
                targets = self._targets
                options = self._options
                executor = self._executor
            if targets and executor is not None:
                snapshots = self._poll_targets(targets, options, executor)
                if snapshots and not self._stop_event.is_set():
                    self._on_snapshots(snapshots)
            remaining = options.poll_interval_seconds - (time.monotonic() - cycle_started)
            self._stop_event.wait(max(0.0, remaining))

    def _poll_targets(
        self,
        targets: tuple[MetricTarget, ...],
        options: LiveMetricsOptions,
        executor: ThreadPoolExecutor,
    ) -> tuple[LiveMetricSnapshot, ...]:
        futures = {
            executor.submit(self._fetcher, target, options.request_timeout_seconds): target
            for target in targets
        }
        snapshots: list[LiveMetricSnapshot] = []
        for future in as_completed(futures):
            target = futures[future]
            if self._stop_event.is_set():
                break
            try:
                current = future.result()
            except Exception as exc:
                with self._lock:
                    tracker = self._trackers.get(target.name)
                snapshot = (
                    tracker.unavailable(_safe_error_message(exc))
                    if tracker is not None
                    else unavailable_snapshot(target.name, _safe_error_message(exc))
                )
            else:
                with self._lock:
                    tracker = self._trackers.setdefault(target.name, LiveMetricTracker(target))
                    tracker.update_target(target)
                    snapshot = tracker.add_sample(current)
            with self._lock:
                history = self._history.setdefault(target.name, deque(maxlen=options.history_capacity))
                history.append(snapshot)
            snapshots.append(snapshot)
        return tuple(sorted(snapshots, key=lambda item: item.name))


def _snapshot(
    name: str,
    *,
    status: str,
    message: str,
    scope: str,
    current: MetricSample,
    prefill_tokens_per_second: float | None = None,
    decode_tokens_per_second: float | None = None,
    prefill_1m_tokens_per_second: float | None = None,
    decode_1m_tokens_per_second: float | None = None,
    prefill_10m_tokens_per_second: float | None = None,
    decode_10m_tokens_per_second: float | None = None,
    last_prompt_tokens_per_second: float | None = None,
    last_decode_tokens_per_second: float | None = None,
    prompt_in_progress: bool = False,
    decode_in_progress: bool = False,
) -> LiveMetricSnapshot:
    return LiveMetricSnapshot(
        name=name,
        status=status,
        message=message,
        scope=scope,
        prefill_tokens_per_second=prefill_tokens_per_second,
        decode_tokens_per_second=decode_tokens_per_second,
        prefill_1m_tokens_per_second=prefill_1m_tokens_per_second,
        decode_1m_tokens_per_second=decode_1m_tokens_per_second,
        prefill_10m_tokens_per_second=prefill_10m_tokens_per_second,
        decode_10m_tokens_per_second=decode_10m_tokens_per_second,
        last_prompt_tokens_per_second=last_prompt_tokens_per_second,
        last_decode_tokens_per_second=last_decode_tokens_per_second,
        prompt_in_progress=prompt_in_progress,
        decode_in_progress=decode_in_progress,
        requests_processing=current.requests_processing,
        requests_deferred=current.requests_deferred,
        sampled_monotonic=current.collected_monotonic,
    )


def _counter_rate(token_delta: float, seconds_delta: float) -> float | None:
    if token_delta <= 0 or seconds_delta <= 0:
        return None
    rate = token_delta / seconds_delta
    return rate if math.isfinite(rate) and rate >= 0 else None


def _metric_scope(target: MetricTarget, current: MetricSample) -> str:
    return (
        "shared server-wide"
        if current.requests_processing > 1 or target.configured_parallelism > 1
        else "server-wide"
    )


def _server_restarted(previous: MetricSample, current: MetricSample) -> bool:
    return bool(
        previous.process_start_time_unix
        and current.process_start_time_unix
        and previous.process_start_time_unix != current.process_start_time_unix
    )


def _counter_regressed(previous: MetricSample, current: MetricSample) -> bool:
    return any(
        current_value < previous_value
        for previous_value, current_value in (
            (previous.prompt_tokens_total, current.prompt_tokens_total),
            (previous.prompt_seconds_total, current.prompt_seconds_total),
            (previous.predicted_tokens_total, current.predicted_tokens_total),
            (previous.predicted_seconds_total, current.predicted_seconds_total),
        )
    )


def _as_nonnegative_int(value: float, name: str) -> int:
    if not value.is_integer():
        raise ValueError(f"invalid integer value for {name}")
    return int(value)


def _safe_error_message(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "Metrics endpoint timed out."
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status in {400, 404, 501}:
            return "Metrics endpoint unavailable; start this server with --metrics."
        return f"Metrics endpoint returned HTTP {status}."
    if isinstance(exc, httpx.HTTPError):
        return "Metrics endpoint could not be reached."
    return f"Metrics response unavailable: {exc}"


def _clamp_float(value: object, *, default: float, minimum: float, maximum: float) -> float:
    try:
        numeric = float(str(value))
    except (TypeError, ValueError):
        return default
    if not math.isfinite(numeric):
        return default
    return min(max(numeric, minimum), maximum)


def _clamp_int(value: object, *, default: int, minimum: int, maximum: int) -> int:
    try:
        numeric = int(str(value))
    except (TypeError, ValueError):
        return default
    return min(max(numeric, minimum), maximum)
