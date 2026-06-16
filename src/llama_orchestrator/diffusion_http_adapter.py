"""OpenAI-compatible HTTP adapter for the experimental DiffusionGemma runner."""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


class DiffusionRunner:
    """Own one persistent upstream visual-server process."""

    def __init__(self, runner: Path, model: Path, max_tokens: int, gpu_layers: int) -> None:
        self.model = model
        self.lock = threading.Lock()
        env = dict(os.environ)
        env.update({"MAXTOK": str(max_tokens), "NGL": str(gpu_layers), "FA": "1"})
        self.proc = subprocess.Popen(
            [str(runner), str(model)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=env,
        )
        threading.Thread(target=self._forward_stderr, daemon=True).start()
        ready = self.proc.stdout.readline().strip() if self.proc.stdout else ""
        if not ready.startswith("READY "):
            code = self.proc.poll()
            raise RuntimeError(f"Diffusion runner did not become ready (exit={code}, output={ready!r})")
        parts = ready.split()
        self.vocab_size = int(parts[1])
        self.context_size = int(parts[2])

    def _forward_stderr(self) -> None:
        if not self.proc.stderr:
            return
        for line in self.proc.stderr:
            sys.stderr.write(f"[diffusion-runner] {line}")
            sys.stderr.flush()

    def healthy(self) -> bool:
        return self.proc.poll() is None

    def complete(self, messages: list[dict[str, Any]], max_tokens: int, seed: int) -> tuple[str, dict[str, str]]:
        if not self.proc.stdin or not self.proc.stdout:
            raise RuntimeError("Diffusion runner pipes are unavailable")
        request = {
            "seed": seed,
            "n_blocks": max(1, math.ceil(max_tokens / 256)),
            "messages": messages,
        }
        request_path: Path | None = None
        with self.lock:
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".json", delete=False, encoding="utf-8"
                ) as handle:
                    json.dump(request, handle, ensure_ascii=False)
                    request_path = Path(handle.name)
                self.proc.stdin.write(str(request_path) + "\n")
                self.proc.stdin.flush()
                content = ""
                stats: dict[str, str] = {}
                while True:
                    line = self.proc.stdout.readline()
                    if not line:
                        raise RuntimeError("Diffusion runner exited during generation")
                    line = line.rstrip("\r\n")
                    if line == "DONE":
                        return content, stats
                    if line.startswith("ERR "):
                        raise RuntimeError(line[4:])
                    if line.startswith("C "):
                        _, _, payload = line.split(" ", 2)
                        content = json.loads(payload)
                    elif line.startswith("STATS "):
                        stats = dict(item.split("=", 1) for item in line[6:].split() if "=" in item)
            finally:
                if request_path is not None:
                    request_path.unlink(missing_ok=True)

    def close(self) -> None:
        if self.proc.poll() is not None:
            return
        if self.proc.stdin:
            try:
                self.proc.stdin.write("QUIT\n")
                self.proc.stdin.flush()
            except OSError:
                pass
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.terminate()


class AdapterHandler(BaseHTTPRequestHandler):
    server_version = "llama-orchestrator-diffusion/1.0"

    @property
    def runner(self) -> DiffusionRunner:
        return self.server.runner  # type: ignore[attr-defined]

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._json(200 if self.runner.healthy() else 503, {"status": "ok" if self.runner.healthy() else "error"})
            return
        if self.path == "/v1/models":
            self._json(200, {"object": "list", "data": [{"id": self.runner.model.stem, "object": "model"}]})
            return
        self._json(404, {"error": {"message": "Not found"}})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/chat/completions":
            self._json(404, {"error": {"message": "Not found"}})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(size))
            if body.get("stream"):
                raise ValueError("Streaming is not supported by the experimental diffusion adapter")
            messages = body.get("messages")
            if not isinstance(messages, list) or not messages:
                raise ValueError("messages must be a non-empty list")
            max_tokens = int(body.get("max_tokens", 256))
            seed = int(body.get("seed", 0))
            started = time.time()
            content, stats = self.runner.complete(messages, max_tokens, seed)
            prompt_tokens = int(stats.get("prompt_n", 0))
            completion_tokens = int(stats.get("predicted_n", 0))
            self._json(200, {
                "id": f"chatcmpl-{uuid.uuid4().hex}",
                "object": "chat.completion",
                "created": int(started),
                "model": self.runner.model.stem,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens, "total_tokens": prompt_tokens + completion_tokens},
                "diffusion_stats": stats,
            })
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json(400, {"error": {"message": str(exc)}})
        except Exception as exc:
            self._json(500, {"error": {"message": str(exc)}})

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("[diffusion-http] " + fmt % args + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--max-tokens", type=int, required=True)
    parser.add_argument("--gpu-layers", type=int, default=0)
    args = parser.parse_args()

    runner = DiffusionRunner(args.runner, args.model, args.max_tokens, args.gpu_layers)
    server = ThreadingHTTPServer((args.host, args.port), AdapterHandler)
    server.runner = runner  # type: ignore[attr-defined]
    try:
        server.serve_forever()
    finally:
        runner.close()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
