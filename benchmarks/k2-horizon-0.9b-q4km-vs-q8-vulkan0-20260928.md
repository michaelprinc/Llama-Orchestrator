# K2-Horizon-0.9B GGUF: Q4_K_M vs Q8_0 on Vulkan0

Date: 2026-09-28

## Decision

The Orchestrator instance uses **Q4_K_M**. It was faster for decode and for the matched 512-token prompt plus 128-token generation workload. Q8_0 prefills slightly faster, but its decode is slower enough that total request time is longer.

## Matched speed comparison

Seven repetitions per benchmark case, sequentially on `Vulkan0` (AMD Radeon(TM) Graphics), using the same build and settings: batch/ubatch 512, 8 CPU threads, 99 GPU layers, Flash Attention auto, offline model loading.

| Workload | Q4_K_M | Q8_0 | Faster |
|---|---:|---:|---|
| Prefill, 512 tokens | 489.625 ± 2.878 tok/s | 514.414 ± 11.359 tok/s | Q8_0 by 5.1% |
| Decode, 128 tokens | 46.486 ± 0.451 tok/s | 32.662 ± 0.467 tok/s | Q4_K_M by 42.3% |
| Combined, 512 prompt + 128 generated | 159.600 ± 1.395 tok/s (4.010 s) | 124.722 ± 0.794 tok/s (5.131 s) | Q4_K_M by 28.0% |

Speeds and standard deviations are from the seven `llama-bench` samples. Combined latency is 640 tokens divided by the combined-case average throughput. These are results for this local Vulkan/iGPU setup and this workload.

Exact benchmark command:

```powershell
llama-bench.exe -m <Q4_K_M.gguf>,<Q8_0.gguf> -p 512 -n 128 -r 7 -ngl 99 -dev Vulkan0 -b 512 -ub 512 -t 8 -fa auto --offline -o jsonl
llama-bench.exe -m <Q4_K_M.gguf>,<Q8_0.gguf> -pg 512,128 -r 7 -ngl 99 -dev Vulkan0 -b 512 -ub 512 -t 8 -fa auto --offline -o jsonl
```

## Model files

Source: [IFM/K2-Horizon-0.9B-GGUF](https://huggingface.co/IFM/K2-Horizon-0.9B-GGUF). The official card says K2-Horizon requires a llama.cpp build with its K2 architecture support; this instance is pinned to the IFM llama.cpp branch that provides it.

| Quant | Local file | Size | SHA-256 |
|---|---|---:|---|
| Q4_K_M | `models/IFM__K2-Horizon-0.9B-GGUF/K2-Horizon-1B-Q4_K_M.gguf` | 666,184,064 bytes | `78BFF592AEBA235C767C93A0BDA5870D5A8DCA21C2A2D502930DD3078C79F2` |
| Q8_0 | `models/IFM__K2-Horizon-0.9B-GGUF/K2-Horizon-1B-Q8_0.gguf` | 1,148,614,016 bytes | `741FB9AD263956C003883CB7CADDEB14B4DFB9B4B67B5414AD12967E965DD547` |

The GGUF filenames use `1B`; the files report 1,078,285,824 parameters and belong to the repository's 0.9B model.

## Runtime and instance

- Instance: `k2-horizon-0-9b-q4km-vulkan-igpu`
- Instance UID: `0047e806-7927-4e31-b5bb-ef5e5f849c87` (instance 00000102)
- Config: `instances/00000102_0047e806-7927-4e31-b5bb-ef5e5f849c87/config.json`
- Backend/device: Vulkan device 0, `AMD Radeon(TM) Graphics`; the RX 6800 is Vulkan1 and was excluded from the benchmark.
- Placement: 99 GPU layers; context 65,536; batch 512; 8 threads; `--jinja` enabled.
- Pinned server: `ifm-k2horizon-llamacpp-42adf019-vulkan-win-x64-20260928-r1`, UUID `3548f6f6-ea98-4e49-b1b7-27de65c975d4`, source commit `42adf019f76013dac873b5b43950d54d5ab27216`.
- Registered server SHA-256: `494087abd58d8a005bcb832d513c84b851a7c8901c605824442b8b693641b287`.
- Orchestrator config/runtime validation passed. The managed server reached healthy status and completed a chat request (`finish_reason=stop`); it was then stopped. Port 8202 has no listener and the server PID no longer exists.

## Receipts

- `benchmarks/k2-horizon-0.9b-q4km-vs-q8-vulkan0-20260928.jsonl` — prefill and decode samples.
- `benchmarks/k2-horizon-0.9b-q4km-vs-q8-vulkan0-20260928.stderr.log` — device inventory and benchmark diagnostics.
- `benchmarks/k2-horizon-0.9b-q4km-vs-q8-vulkan0-pg512-128-20260928.jsonl` — matched combined workload samples.
- `benchmarks/k2-horizon-0.9b-q4km-vs-q8-vulkan0-pg512-128-20260928.stderr.log` — combined-run diagnostics.
