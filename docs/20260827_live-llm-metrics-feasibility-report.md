# Live LLM Metrics: Feasibility Report

**Decision:** Feasible. Implement it as an optional, local-only Orchestrator monitor that reports two clearly labelled measures: live aggregate server throughput and, when the Orchestrator owns the streamed request, request-specific rolling decode throughput. Do not present a mid-request prefill TPS as final until the first generated token arrives.

**Scope:** Assessment only. No application, instance, server-package, or configuration changes were made.

## Proposal assessed

Add an opt-in “Live metrics” view to Llama Orchestrator so an operator can observe LLM prefill and decode speed while the server is in use. It should consume negligible resources while disabled.

## Evidence from the current implementation

The existing implementation already has the necessary foundations.

| Finding | Evidence | Consequence |
|---|---|---|
| Final per-request prefill and decode metrics are already captured by quick benchmarks. | `benchmark.py` extracts `timings.prompt_per_second` and `timings.predicted_per_second`; the GUI already calls them **Prefill TPS** and **Decode TPS**. | The metric names, final values, display conventions, and storage model are established. |
| llama.cpp can expose Prometheus-format `/metrics` only when launched with `--metrics`. | The bundled b10199 source gates the endpoint on `endpoint_metrics`. Its counters include prompt tokens/seconds and predicted tokens/seconds, plus gauges for current average prompt/decode TPS and requests in progress. | Sampling counter deltas can yield live, server-wide prefill/decode TPS without modifying llama.cpp. |
| The server supports request field `timings_per_token`. | The b10199 server schema documents it as including prompt-processing and generation-speed information in each response. The server attaches timings to partial results when this flag is enabled. | A request that passes through the Orchestrator can provide continuously refreshed server-reported timing snapshots. |
| The current quick benchmark is a streaming `httpx` client and records the time of its first token. | `quick_benchmark_instance()` reads streaming response lines and derives first-token latency today. | A reusable streaming observer can be built with existing dependencies and conventions. |
| The GUI already has a background refresh controller and an event queue to the Tk main thread. | `RefreshController` runs background collection; the GUI pumps queued messages on the main thread. | Live polling can stay off the UI thread and should not need new dependencies. |

The live application instance currently inspected (port 8193) does **not** include `--metrics` in its arguments. Across the current instance configurations, 17 of 84 include that flag. Therefore the feature must discover support at runtime and report an explicit “endpoint unavailable” state; it must never assume that metrics are enabled.

## What “live” can accurately mean

There are two distinct scopes. They must not be conflated.

| Measure | Availability | Meaning | Limits |
|---|---|---|---|
| **Live server prefill/decode TPS** | Any instance with `/metrics` enabled | Delta of the server’s cumulative prompt/predicted tokens divided by its corresponding cumulative processing time over a sampling interval. | Aggregate across every request/slot. It is not attributable to one user request when parallelism is greater than one. |
| **Request decode TPS (rolling)** | Only traffic streamed through an Orchestrator proxy/observer, or an Orchestrator-initiated request | Token count over a short recent window after first token. | It is an observed delivery rate, affected by network/client buffering and speculative-decoding token chunks. It is not necessarily a one-token-per-decode kernel rate. |
| **Request final prefill TPS** | At first token / completion | Server `prompt_per_second` from the request timing payload. | Prefill is a completed phase. It cannot have a stable exact rate *during* a long prefill unless llama.cpp emits explicit prompt progress. |
| **Request final decode TPS** | Completion | Server `predicted_per_second` from final timings. | The authoritative final value; do not replace it with a rolling estimate. |

`/metrics` also exposes `requests_processing` and `requests_deferred`. These are useful context for interpreting shared throughput, especially for multi-slot servers.

## Recommended product design

Use one **Live metrics** switch in the GUI, disabled by default and persisted in GUI settings. It starts a bounded monitor only for selected, running instances. The default interval should be 1 second, with a configurable safe range such as 0.5–5 seconds.

The panel should display:

- Instance, capture state, last sample age, and active/deferred request counts.
- **Server prefill TPS (live)** and **Server decode TPS (live)**, calculated from counter deltas, with a `shared` label whenever the server can process more than one slot.
- The server’s own lifetime-average TPS only as secondary diagnostic information, labelled `server average`, not `current`.
- A small rolling history (for example 60 samples in memory) and a clear `no active work` state rather than a zero rate when no counters advance.
- When request observation is available: phase (`waiting for first token`, `decode`, `complete`), TTFT, rolling decode TPS, output tokens observed, final server prefill TPS, and final server decode TPS.

The status text must distinguish:

- `disabled` — no background worker, no HTTP polling, no persistence;
- `unavailable` — server was not started with `--metrics`, returned an invalid response, or is unreachable;
- `idle` — monitor works but no active request/counter movement;
- `shared` — numbers combine multiple active requests;
- `observed` — client-side rolling estimate; and
- `server-reported final` — final request timings supplied by llama.cpp.

## Optionality and resource impact

The requested resource boundary is practical.

- **Disabled:** do not create the monitor object, network client, timer, history, database rows, or server flag. The feature has no steady-state polling cost.
- **Enabled:** issue one local GET per selected running instance per interval, parse a small Prometheus payload, retain a fixed-size in-memory ring buffer, and update only changed GUI widgets. No GPU work is introduced.
- **Stop conditions:** stop immediately when the switch is off, the window closes, the selected instance stops, or repeated endpoint failures hit a bounded threshold. Close/reuse the HTTP client deterministically.
- **No required server restart for an already-enabled endpoint:** monitoring can start and stop purely in the Orchestrator. Enabling `/metrics` on an instance that lacks `--metrics` is a separate static configuration change and requires its normal restart path.

At a 1-second local interval this should be operationally light, but it should be validated on the RX 6800 workload. `/metrics` is handled through the server task queue at high priority, so aggressive polling could perturb a saturated single-slot server. The plan therefore sets a lower interval bound, avoids overlapping requests, and includes an overhead test.

## Alternatives considered

1. **Poll `/metrics` only — recommended MVP.** Accurate server-wide rates, no new package, and works for existing `--metrics` instances. Its scope is aggregate, so labels are essential.
2. **Make every managed application request use `timings_per_token`.** Useful only when the Orchestrator is in the request path. It cannot observe calls sent directly from another client to `llama-server`.
3. **Build an opt-in reverse proxy in the Orchestrator — recommended follow-on for per-request live metrics.** The proxy can forward SSE unchanged while tracking first token, chunks, rolling throughput, and final timing payload. This gives request attribution but changes the client endpoint/topology and deserves its own security and compatibility tests.
4. **Parse server stdout/stderr.** Rejected: log format is not a stable telemetry API, may be rotated, and is hard to attribute under concurrency.
5. **Patch llama.cpp for a new streaming telemetry endpoint.** Not required for MVP. Consider only if future requirements demand exact in-progress prefill percent/rate for direct clients or per-slot attribution that `/metrics` cannot supply.

## Risks and controls

| Risk | Control |
|---|---|
| Reporting a lifetime average as a current rate | Compute rates from adjacent cumulative counter samples; label endpoint-provided gauges as lifetime/server averages. |
| Assigning aggregate throughput to one client under `parallel > 1` | Label as `shared`; omit per-request attribution unless the proxy owns that stream. |
| Counter reset after a server restart | Read the `Process-Start-Time-Unix` response header; reset the baseline and show `warming up` rather than a negative/invalid rate. |
| Requests with prompt-cache reuse | Surface cache information when available in request timings; do not call server prefill TPS a full-prompt rate without qualification. |
| Experimental metric parsing breaks with a server upgrade | Use a small versioned parser with strict required fields, tolerant unknown fields, capability detection, and fixture tests from real endpoint captures. |
| Monitoring changes inference results | Validate disabled and enabled runs under the same model/runtime contract; use fixed payloads and report prefill/decode medians separately. |
| Exposing request content or tokens | Keep the MVP aggregate-only; the proxy follow-on must not persist prompt or generated text, and must bind only to loopback by default. |

## Feasibility conclusion

The proposal is worthwhile and technically low-to-moderate risk as an aggregate monitor. It reuses the authoritative metrics that llama.cpp already maintains, the Orchestrator’s existing GUI refresh pattern, and existing `httpx` dependency. An optional aggregate monitor should be implemented first. A proxy-backed per-request monitor is feasible but should be treated as a second, separately specified increment because it changes traffic routing and privacy/security boundaries.

The accompanying implementation plan defines the specification, tests, rollout gates, and acceptance criteria.
