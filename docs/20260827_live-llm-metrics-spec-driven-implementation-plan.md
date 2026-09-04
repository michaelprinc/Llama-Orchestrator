# Live LLM Metrics: Spec-Driven Implementation Plan

**Status:** Aggregate-monitor MVP implemented on 2026-08-27. The request-observing proxy remains deferred.

**Decision scope:** Implement the aggregate live-monitor MVP first. Defer the opt-in request-observing proxy to a follow-on specification after the MVP proves accurate and low overhead.

**Implemented:** global disabled-by-default control; automatic coverage of every currently running managed model; global bounded polling settings; no persistence of samples or request content; reset/unavailable/shared-scope handling; worker shutdown on disable and GUI close; pure telemetry tests and full-suite validation. The remaining operational gate is a matched enabled-versus-disabled RX 6800 benchmark against an instance launched with `--metrics`.

## 1. Product specification

### 1.1 Goal

Allow an operator to opt in to live, local observation of prefill and decode throughput for selected running `llama-server` instances, while imposing no ongoing monitoring work when disabled.

### 1.2 Non-goals for MVP

- Do not change llama.cpp, HIP kernels, server packages, model settings, or inference scheduling.
- Do not automatically add `--metrics`, edit an instance, or restart a server.
- Do not claim server-wide measurements are per-request when the server has multiple active slots.
- Do not persist high-frequency samples, request prompts, response text, or secrets.
- Do not add a Prometheus server, external database, or new runtime dependency.

### 1.3 User stories

1. As an operator, I can turn **Live metrics** on for selected running instances and see current server prefill/decode TPS within the configured sampling interval.
2. As an operator, I can keep it off and know it consumes no background polling resources.
3. As an operator, I can tell whether a value is idle, unavailable, shared across requests, or based on a fresh sample.
4. As an operator, I receive a helpful next action when the server lacks `--metrics` without the application changing the instance for me.
5. As an operator, I can turn it off or close the GUI and be confident that monitoring stops promptly.

### 1.4 Acceptance criteria

| ID | Criterion |
|---|---|
| AC-01 | With Live metrics disabled, no `/metrics` request is issued and no monitor worker/client is alive. |
| AC-02 | With it enabled for a healthy `--metrics` instance, the panel shows sample age, active/deferred request counts, and live prefill/decode values from counter deltas. |
| AC-03 | With no counter movement, throughput is shown as `idle`/`—`, never as a misleading `0.0 TPS`. |
| AC-04 | When `requests_processing > 1` or configured parallelism is greater than 1, displayed throughput has a `shared` scope label. |
| AC-05 | A 404, 400/unsupported payload, timeout, malformed metric, or connection failure shows `unavailable` and does not break normal GUI refresh. |
| AC-06 | A server start-time change or declining cumulative counter resets the baseline and suppresses a rate until the next usable interval. |
| AC-07 | Sampling never overlaps for one instance, honors the configured lower interval limit, and stops on disable/window close/instance stop. |
| AC-08 | In the fixed RX 6800 validation contract, enabled 1-second monitoring does not show a material regression in median prefill or decode throughput relative to disabled monitoring; any observed difference is reported with variation, not asserted causal from one run. |

## 2. Metrics contract

### 2.1 Capability probe

For each selected running instance, call `GET http://{host}:{port}/metrics` with a short connect/read timeout. Parse the Prometheus text format for these names:

| Metric | Type | Required for | Notes |
|---|---|---|---|
| `llamacpp:prompt_tokens_total` | counter | live prefill TPS | cumulative server-wide prompt tokens |
| `llamacpp:prompt_seconds_total` | counter | live prefill TPS | cumulative server-side prompt duration |
| `llamacpp:tokens_predicted_total` | counter | live decode TPS | cumulative server-wide generation tokens |
| `llamacpp:tokens_predicted_seconds_total` | counter | live decode TPS | cumulative server-side generation duration |
| `llamacpp:requests_processing` | gauge | activity state | active processing slots |
| `llamacpp:requests_deferred` | gauge | activity state | queued/deferred work |
| `Process-Start-Time-Unix` | response header | reset detection | optional but preferred baseline identity |

Unknown fields are ignored. Missing required fields means `unavailable`, with the exact missing field recorded in diagnostics. Endpoint access is only a capability check; it does not prove that a value is request-specific.

### 2.2 Rate calculation

For two valid adjacent samples `A` and `B`, where `delta_seconds > 0` and the server identity has not changed:

```
live_prefill_tps = (B.prompt_tokens_total - A.prompt_tokens_total)
                   / (B.prompt_seconds_total - A.prompt_seconds_total)

live_decode_tps  = (B.predicted_tokens_total - A.predicted_tokens_total)
                   / (B.predicted_seconds_total - A.predicted_seconds_total)
```

Use a phase rate only when both numerator and denominator deltas are positive. A zero token delta means `idle` for that phase. A counter regression, zero/negative time delta, or changed start time invalidates the baseline. Do not fall back to wall-clock TPS: server processing duration is the more relevant denominator for this telemetry.

Rounding and presentation are GUI concerns; retain raw floats in the in-memory snapshot. The status must say `server-wide` and add `shared` when more than one request is active or parallelism is configured above one.

### 2.3 Data types and ownership

Introduce a narrow new module, for example `live_metrics.py`, containing immutable dataclasses:

- `MetricCapability` — endpoint state, missing fields, server identity.
- `MetricSample` — sampled counters/gauges, monotonic collection time, server start time.
- `LiveMetricSnapshot` — calculated rates, phase/activity state, scope, sample age, and diagnostic message.
- `LiveMetricsSettings` — enabled flag, selected instance identifiers, interval seconds, and history capacity.

Keep transport/parsing/calculation independent of Tkinter. GUI code should only start/stop a coordinator and render immutable snapshots on the main thread. Store the user preferences in existing GUI settings; retain only a fixed-length in-memory history (`60` samples by default).

## 3. Delivery slices

### Slice 0 — Specification and fixtures

1. Create `docs/20260827_live-llm-metrics-specification.md` from Sections 1–2, including the metric examples collected from a real local endpoint.
2. Capture sanitized `/metrics` fixtures for: idle server, active prefill, active decode, multi-slot activity, malformed response, missing `--metrics`, and a process restart.
3. Agree that MVP labels are **Server prefill TPS (live)** and **Server decode TPS (live)**, not generic “TPS”.
4. Record the exact Orchestrator commit, server package identity, model/runtime configuration, and capture timestamp in the fixture manifest.

**Exit gate:** a review confirms the scope and naming; no implementation begins until the aggregate-versus-request distinction is accepted.

### Slice 1 — Pure telemetry core

1. Implement a strict, dependency-free Prometheus-text parser for the required names and response header.
2. Implement baseline, reset, delta, and activity-state calculation as pure functions.
3. Add unit tests for each acceptance criterion through AC-06, using fixtures only.
4. Add a bounded per-instance state store that does not retain text or request payloads.

**Exit gate:** full unit suite passes; parser ignores unknown metrics and exposes failure reasons without throwing into callers.

### Slice 2 — Optional monitor lifecycle

1. Add a monitor coordinator that uses one bounded background worker/client and a per-instance no-overlap guard.
2. Start it only when `LiveMetricsSettings.enabled` is true and there is at least one selected, running instance.
3. Stop and dispose it on disable, GUI close, no eligible instances, and bounded repeated failure.
4. Use the existing thread-to-Tk message pattern; never make network calls from the Tk main thread.
5. Enforce a 0.5-second minimum interval, 5-second default maximum/configurable interval, short metric request timeout, and exponential retry only for endpoint failures.

**Exit gate:** lifecycle tests prove zero calls while disabled, no duplicate worker, no overlap, and no remaining worker after stop.

### Slice 3 — GUI and settings

1. Add a disabled-by-default **Live metrics** control to the existing toolbar or details panel.
2. Add an instance selector and interval control with safe bounds and plain language explaining `server-wide` scope.
3. Render a compact panel or expandable section with status, prefill/decode live TPS, activity/deferred count, scope, last sample age, and endpoint diagnostics.
4. Add a 60-sample sparkline/history only if it can be rendered without degrading the current Treeview refresh; otherwise defer the graph until the snapshot path is proven stable.
5. Persist only settings via the existing GUI-settings migration approach. Preserve older settings defaults.

**Exit gate:** GUI tests cover enabled/disabled/unavailable/idle/shared/reset states; manual smoke test verifies the GUI remains responsive during 1-second sampling.

### Slice 4 — Integration and measured validation

1. Start an existing `--metrics` test instance and verify sample values against direct `/metrics` captures.
2. Confirm the inspected port-8193 profile reports `unavailable` until it is deliberately configured with `--metrics`; do not alter it during this task.
3. Validate restart handling, process stop, endpoint timeout, and two selected instances.
4. Measure monitoring overhead under the canonical RX 6800 contract: fixed package, runtime, model, context, batch/ubatch, cache types, Flash Attention, prompt, seed, and output length. Run two warm-ups and at least five measured requests for both disabled and enabled states, alternating order where practical.
5. Record prefill/decode medians and p10/p90 separately. Reject runs with changed generation length, fallback, restart, allocation failure, or mismatched package/runtime.

**Exit gate:** acceptance criteria AC-01 through AC-08 pass, raw captures/receipts are retained, and the release note states the server-wide scope.

## 4. Follow-on: request-observing mode

Specify and approve this only after the MVP.

### Proposed contract

An opt-in, loopback-only Orchestrator reverse proxy forwards the client’s streaming request to the configured llama.cpp server unchanged. It observes SSE arrival times and fields but does not persist prompt or generated text. For traffic routed through it, it displays:

- time from forwarding request to first content token (TTFT);
- `waiting for first token` as the prefill state;
- final prefill TPS from llama.cpp `timings.prompt_per_second` at/after first token;
- rolling observed decode TPS over a labelled short time window;
- final decode TPS from `timings.predicted_per_second`; and
- a `client-observed` versus `server-reported final` provenance label.

The proxy may request `timings_per_token` only when the feature is enabled and the downstream endpoint accepts it. The design must test OpenAI-compatible chat and completion streaming, cancellation/backpressure, upstream errors, API keys, loopback binding, large outputs, multi-client isolation, and transparent forwarding. It must have a one-click off/revert path that restores the original direct endpoint.

## 5. Test matrix

| Level | Cases |
|---|---|
| Parser/unit | valid values; comments/whitespace; unknown metrics; missing metrics; malformed numbers; duplicate metric; reset header; counter decrease; zero delta; no active requests |
| Coordinator | disabled no-op; start/stop; selected-instance changes; timeout; retries; no concurrent same-instance poll; worker shutdown |
| GUI | settings migration; disabled; available idle; active prefill; active decode; shared; unavailable; stale; reset; keyboard/window close |
| Server integration | `--metrics` enabled; missing flag; server restart; multi-slot request; port unreachable |
| Performance | monitor disabled/enabled at 0.5, 1, and 5 seconds; fixed-shape prefill/decode benchmark; CPU/UI responsiveness |
| Regression | full existing Orchestrator tests; type checks/lint available in the project environment; no changes to normal start/stop/benchmark behavior |

## 6. Documentation and rollout

1. Update README telemetry documentation with the feature’s optionality, `--metrics` prerequisite, server-wide scope, labels, interval guidance, and troubleshooting.
2. Add a short in-app help message: “Live values are aggregate for the selected server. With parallel requests they are shared, not per-client.”
3. Publish the feature initially as experimental/opt-in with no automatic configuration mutation.
4. Record validation receipts and package identity. Do not generalize results from one RX 6800 run to other backends or workloads.

## 7. Implementation decision

Proceed with Slices 0–4. The feature is feasible without a llama.cpp rebuild or a new dependency. Treat the reverse proxy as a separate implementation decision, because it changes the endpoint topology and introduces request-level privacy and compatibility obligations.
