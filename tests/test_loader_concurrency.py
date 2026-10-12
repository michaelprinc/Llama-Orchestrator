"""
Thread-safety tests for the config loader cache and persist path (R16/D1, T3.4).

AC: concurrent load/save stress test (threads) with no lost updates or disk
mutations during pure reads; mtime unchanged across a pure-read phase.
"""

import hashlib
import json
import threading
from pathlib import Path
from uuid import uuid4

import pytest

from llama_orchestrator.config import InstanceConfig, ModelConfig, load_config, save_config
from llama_orchestrator.config.loader import _CONFIG_CACHE


def _file_state(path: Path) -> tuple[int, str]:
    """Return (mtime_ns, sha256-of-content) for mutation assertions."""
    data = path.read_bytes()
    return path.stat().st_mtime_ns, hashlib.sha256(data).hexdigest()


def _run_barrier_threads(workers) -> list[BaseException]:
    """Run zero-arg workers behind a common barrier; return recorded failures."""
    errors: list[BaseException] = []
    barrier = threading.Barrier(len(workers))

    def target(worker) -> None:
        try:
            barrier.wait(timeout=10)
            worker()
        except BaseException as exc:  # record; asserted after join
            errors.append(exc)

    threads = [threading.Thread(target=target, args=(w,)) for w in workers]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert all(not t.is_alive() for t in threads), "worker thread did not finish"
    return errors


class TestPureReadNonMutation:
    """AC part 1: pure reads must not mutate any config file or the catalog."""

    def test_concurrent_pure_reads_do_not_mutate_disk(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Concurrent load_config on legacy files: mtime + content unchanged, no catalog."""
        monkeypatch.setattr("llama_orchestrator.config.loader.get_project_root", lambda: tmp_path)
        _CONFIG_CACHE.clear()

        instances_dir = tmp_path / "instances"
        paths: list[Path] = []
        for i in range(6):
            name = f"pure-{i}"
            cfg_dir = instances_dir / name
            cfg_dir.mkdir(parents=True)
            p = cfg_dir / "config.json"
            # Legacy-style minimal files: the pre-R16 code would have
            # backfilled (rewritten) these on first read.
            p.write_text(
                json.dumps({"name": name, "model": {"path": f"models/{i}.gguf"}}),
                encoding="utf-8",
            )
            paths.append(p)

        before = {p: _file_state(p) for p in paths}
        workers = []
        for _ in range(4):
            def worker() -> None:
                for _ in range(25):
                    for p in paths:
                        cfg = load_config(p)
                        assert isinstance(cfg, InstanceConfig)
            workers.append(worker)

        errors = _run_barrier_threads(workers)
        assert not errors, f"worker failures: {errors!r}"

        after = {p: _file_state(p) for p in paths}
        assert after == before, "pure reads must not change mtime or content"
        # No catalog DB created as a side effect of the reads
        assert not (tmp_path / "state" / "instance_catalog.sqlite").exists()


class TestConcurrentSave:
    """AC part 2: concurrent saves are atomic with no lost updates."""

    def test_concurrent_saves_distinct_files_no_lost_updates(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Each thread saves its own file repeatedly; final content = last write."""
        monkeypatch.setattr("llama_orchestrator.config.loader.get_project_root", lambda: tmp_path)
        _CONFIG_CACHE.clear()

        n_threads, rounds = 6, 10
        paths: dict[int, Path] = {}
        for t in range(n_threads):
            name = f"save-{t}"
            cfg_dir = tmp_path / "instances" / name
            cfg_dir.mkdir(parents=True)
            p = cfg_dir / "config.json"
            cfg = InstanceConfig(
                name=name,
                instance_uid=str(uuid4()),
                instance_no=f"{100 + t:08d}",
                model=ModelConfig(path=Path(f"models/{t}.gguf")),
            )
            save_config(cfg, p)
            paths[t] = p

        def worker(t: int) -> None:
            for r in range(rounds):
                cfg = load_config(paths[t])
                cfg.display_name = f"t{t}-r{r}"
                save_config(cfg, paths[t])

        errors = _run_barrier_threads([lambda t=t: worker(t) for t in range(n_threads)])
        assert not errors, f"worker failures: {errors!r}"

        # Each file must hold exactly its owner thread's final write (no lost
        # updates), and a fresh load must reflect it (cache eviction works).
        for t in range(n_threads):
            saved = json.loads(paths[t].read_text(encoding="utf-8"))
            assert saved["display_name"] == f"t{t}-r{rounds - 1}"
            fresh = load_config(paths[t])
            assert fresh.display_name == f"t{t}-r{rounds - 1}"

        # Catalog mirrors the final state: one row per uid, distinct numbers.
        from llama_orchestrator.config.loader import _catalog_uid_by_instance_no

        owners = _catalog_uid_by_instance_no()
        assert len(owners) == n_threads
        assert len(set(owners.values())) == n_threads

    def test_concurrent_saves_same_file_always_complete(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Same-file contention: every save is one complete atomic write."""
        monkeypatch.setattr("llama_orchestrator.config.loader.get_project_root", lambda: tmp_path)
        _CONFIG_CACHE.clear()

        name = "shared"
        p = tmp_path / "instances" / name / "config.json"
        p.parent.mkdir(parents=True)
        cfg = InstanceConfig(
            name=name,
            instance_uid=str(uuid4()),
            instance_no="00000099",
            model=ModelConfig(path=Path("m.gguf")),
        )
        save_config(cfg, p)

        n_threads, rounds = 8, 12
        expected_markers = {f"t{t}-r{r}" for t in range(n_threads) for r in range(rounds)}

        def worker(t: int) -> None:
            for r in range(rounds):
                # Would raise ConfigLoadError on a torn mid-write read.
                loaded = load_config(p)
                assert loaded.name == name
                loaded.display_name = f"t{t}-r{r}"
                save_config(loaded, p)

        errors = _run_barrier_threads([lambda t=t: worker(t) for t in range(n_threads)])
        assert not errors, f"worker failures (torn reads or writes): {errors!r}"

        data = json.loads(p.read_text(encoding="utf-8"))  # one complete JSON doc
        assert data["name"] == name
        # Last complete write wins; no merged or torn state.
        assert data["display_name"] in expected_markers
