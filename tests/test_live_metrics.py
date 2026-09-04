"""Tests for the opt-in llama.cpp live-metrics monitor."""

from __future__ import annotations

import threading

import httpx
import pytest

from llama_orchestrator.gui.app import ensure_live_metrics_argument
from llama_orchestrator.gui.live_metrics_window import detail_rate_rows
from llama_orchestrator.live_metrics import (
    LiveMetricsMonitor,
    LiveMetricsOptions,
    LiveMetricTracker,
    MetricSample,
    MetricTarget,
    _safe_error_message,
    calculate_live_snapshot,
    parse_metrics_response,
)

METRICS = """# HELP llamacpp:prompt_tokens_total Number of prompt tokens processed.
llamacpp:prompt_tokens_total 100
llamacpp:prompt_seconds_total 2
llamacpp:tokens_predicted_total 50
llamacpp:tokens_predicted_seconds_total 5
llamacpp:requests_processing 1
llamacpp:requests_deferred 0
other_metric 42
"""


def sample(**changes: object) -> MetricSample:
    values: dict[str, object] = {
        "prompt_tokens_total": 100.0,
        "prompt_seconds_total": 2.0,
        "predicted_tokens_total": 50.0,
        "predicted_seconds_total": 5.0,
        "requests_processing": 1,
        "requests_deferred": 0,
        "collected_monotonic": 10.0,
        "process_start_time_unix": "1000",
    }
    values.update(changes)
    return MetricSample(**values)  # type: ignore[arg-type]


def test_parse_metrics_response_reads_required_metrics_and_ignores_unknown_values() -> None:
    parsed = parse_metrics_response(METRICS, process_start_time_unix="1000", collected_monotonic=1.0)

    assert parsed == sample(collected_monotonic=1.0)


def test_parse_metrics_response_rejects_missing_or_duplicate_required_metric() -> None:
    with pytest.raises(ValueError, match="missing required metrics"):
        parse_metrics_response("llamacpp:prompt_tokens_total 1")
    with pytest.raises(ValueError, match="duplicate required metric"):
        parse_metrics_response(METRICS + "llamacpp:prompt_tokens_total 101")


def test_calculate_live_snapshot_uses_server_counter_deltas_and_marks_shared_scope() -> None:
    target = MetricTarget("model", "http://127.0.0.1:8193/metrics", configured_parallelism=2)
    previous = sample()
    current = sample(
        prompt_tokens_total=160.0,
        prompt_seconds_total=4.0,
        predicted_tokens_total=90.0,
        predicted_seconds_total=7.0,
        requests_processing=2,
    )

    result = calculate_live_snapshot(target, previous, current)

    assert result.status == "active"
    assert result.prefill_tokens_per_second == 30.0
    assert result.decode_tokens_per_second == 20.0
    assert result.scope == "shared server-wide"


def test_calculate_live_snapshot_handles_baseline_reset_and_idle_without_false_zero_rate() -> None:
    target = MetricTarget("model", "http://127.0.0.1:8193/metrics")

    baseline = calculate_live_snapshot(target, None, sample())
    reset = calculate_live_snapshot(target, sample(), sample(prompt_tokens_total=1.0))
    idle = calculate_live_snapshot(target, sample(), sample(requests_processing=0))

    assert baseline.status == "warming_up"
    assert reset.status == "warming_up"
    assert idle.status == "idle"
    assert idle.prefill_tokens_per_second is None
    assert idle.decode_tokens_per_second is None


def test_tracker_reports_now_rolling_windows_and_persistent_whole_prompt() -> None:
    tracker = LiveMetricTracker(MetricTarget("model", "http://unused/metrics"))

    tracker.add_sample(sample(collected_monotonic=0.0))
    active = tracker.add_sample(
        sample(
            prompt_tokens_total=200.0,
            prompt_seconds_total=4.0,
            predicted_tokens_total=80.0,
            predicted_seconds_total=6.0,
            collected_monotonic=10.0,
        )
    )

    assert active.prefill_tokens_per_second == 50.0
    assert active.decode_tokens_per_second == 30.0
    assert active.prefill_1m_tokens_per_second == 50.0
    assert active.decode_10m_tokens_per_second == 30.0
    assert active.prompt_in_progress is False
    assert active.last_prompt_tokens_per_second == 50.0
    assert active.last_decode_tokens_per_second == 30.0

    completed = tracker.add_sample(
        sample(
            prompt_tokens_total=200.0,
            prompt_seconds_total=4.0,
            predicted_tokens_total=80.0,
            predicted_seconds_total=6.0,
            requests_processing=0,
            collected_monotonic=11.0,
        )
    )
    after_one_minute = tracker.add_sample(
        sample(
            prompt_tokens_total=200.0,
            prompt_seconds_total=4.0,
            predicted_tokens_total=80.0,
            predicted_seconds_total=6.0,
            requests_processing=0,
            collected_monotonic=70.0,
        )
    )

    assert completed.last_prompt_tokens_per_second == 50.0
    assert completed.last_decode_tokens_per_second == 30.0
    assert completed.prompt_in_progress is False
    assert after_one_minute.prefill_1m_tokens_per_second is None
    assert after_one_minute.prefill_10m_tokens_per_second == 50.0
    assert after_one_minute.last_prompt_tokens_per_second == 50.0

    after_ten_minutes = tracker.add_sample(
        sample(
            prompt_tokens_total=200.0,
            prompt_seconds_total=4.0,
            predicted_tokens_total=80.0,
            predicted_seconds_total=6.0,
            requests_processing=0,
            collected_monotonic=611.0,
        )
    )
    assert after_ten_minutes.prefill_10m_tokens_per_second is None
    assert after_ten_minutes.last_prompt_tokens_per_second == 50.0


def test_tracker_clears_last_prompt_while_next_prompt_is_analyzed() -> None:
    tracker = LiveMetricTracker(MetricTarget("model", "http://unused/metrics"))
    tracker.add_sample(sample(collected_monotonic=0.0))
    tracker.add_sample(
        sample(prompt_tokens_total=200.0, prompt_seconds_total=4.0, collected_monotonic=1.0)
    )
    completed = tracker.add_sample(
        sample(
            prompt_tokens_total=200.0,
            prompt_seconds_total=4.0,
            requests_processing=0,
            collected_monotonic=2.0,
        )
    )
    next_prompt_started = tracker.add_sample(
        sample(
            prompt_tokens_total=200.0,
            prompt_seconds_total=4.0,
            requests_processing=1,
            collected_monotonic=3.0,
        )
    )
    next_completed = tracker.add_sample(
        sample(prompt_tokens_total=240.0, prompt_seconds_total=5.0, collected_monotonic=4.0)
    )

    assert completed.last_prompt_tokens_per_second == 50.0
    assert next_prompt_started.prompt_in_progress is True
    assert next_prompt_started.last_prompt_tokens_per_second is None
    assert detail_rate_rows(next_prompt_started)[0][4] == "analyzing…"
    assert next_completed.last_prompt_tokens_per_second == 40.0


def test_detail_rows_show_precise_rates_and_phase_progress() -> None:
    tracker = LiveMetricTracker(MetricTarget("model", "http://unused/metrics"))
    tracker.add_sample(sample(collected_monotonic=0.0))
    snapshot = tracker.add_sample(
        sample(prompt_tokens_total=101.0, prompt_seconds_total=2.003, collected_monotonic=1.0)
    )

    rows = detail_rate_rows(snapshot)

    assert rows[0][0] == "Prefill"
    assert rows[0][1] == "333.33 tok/s"
    assert rows[0][4] == "333.33 tok/s"
    assert rows[1][0] == "Decode"


def test_disabled_monitor_creates_no_worker_and_performs_no_fetch() -> None:
    calls: list[MetricTarget] = []
    monitor = LiveMetricsMonitor(
        LiveMetricsOptions(),
        lambda _snapshots: None,
        fetcher=lambda target, _timeout: calls.append(target) or sample(),
    )
    monitor.update_targets((MetricTarget("model", "http://unused/metrics"),))
    monitor.start()

    assert monitor.running is False
    assert calls == []


def test_metrics_disabled_by_server_returns_an_actionable_message() -> None:
    request = httpx.Request("GET", "http://127.0.0.1:8193/metrics")
    response = httpx.Response(501, request=request)
    error = httpx.HTTPStatusError("not implemented", request=request, response=response)

    assert _safe_error_message(error) == "Metrics endpoint unavailable; start this server with --metrics."


def test_live_metrics_argument_is_opt_in_and_idempotent() -> None:
    args = ["--ctx-size", "8192"]

    assert ensure_live_metrics_argument(args, enabled=False) == args
    assert ensure_live_metrics_argument(args, enabled=True) == [*args, "--metrics"]
    assert ensure_live_metrics_argument([*args, "--metrics"], enabled=True) == [*args, "--metrics"]


def test_enabled_monitor_polls_each_target_once_without_retaining_unbounded_history() -> None:
    published: list[tuple] = []
    complete = threading.Event()
    samples = iter((sample(), sample(prompt_tokens_total=110.0, prompt_seconds_total=3.0)))

    def fetcher(_target: MetricTarget, _timeout: float) -> MetricSample:
        return next(samples)

    def on_snapshots(snapshots: tuple) -> None:
        published.append(snapshots)
        if len(published) >= 2:
            complete.set()

    monitor = LiveMetricsMonitor(
        LiveMetricsOptions(enabled=True, poll_interval_seconds=0.5, history_capacity=10),
        on_snapshots,
        fetcher=fetcher,
    )
    monitor.update_targets((MetricTarget("model", "http://unused/metrics"),))
    monitor.start()
    try:
        assert complete.wait(2.0)
    finally:
        monitor.stop()

    assert published[0][0].status == "warming_up"
    assert published[1][0].prefill_tokens_per_second == 10.0
    assert len(monitor.history_for("model")) == 2
