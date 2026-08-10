"""Standalone check for breath/_concurrent.dehydrate_many.

Stubs the runtime + utils so we can assert three things without a live server:
  1. results come back in input order
  2. a single failing bucket yields None, not a dead batch
  3. calls actually overlap (wall clock << sum of latencies)
"""
import asyncio
import sys
import time
import types

ROOT = "/workspace/ombre-brain/src"
sys.path.insert(0, ROOT)

# --- stub utils ---
utils = types.ModuleType("utils")
utils.strip_wikilinks = lambda s: s
utils.count_tokens_approx = lambda s: max(1, len(s) // 4)
sys.modules["utils"] = utils

# --- stub package + runtime ---
pkg = types.ModuleType("tools")
pkg.__path__ = [ROOT + "/tools"]
sys.modules["tools"] = pkg

rt = types.ModuleType("tools._runtime")
LATENCY = 0.20
concurrent_peak = 0
in_flight = 0


class _Dehydrator:
    async def dehydrate(self, content, meta):
        global concurrent_peak, in_flight
        in_flight += 1
        concurrent_peak = max(concurrent_peak, in_flight)
        try:
            await asyncio.sleep(LATENCY)
            if content == "BOOM":
                raise RuntimeError("simulated dehydrate failure")
            return f"summary::{content}::v{meta.get('valence')}"
        finally:
            in_flight -= 1


class _Logger:
    def warning(self, *a, **k):
        pass


rt.dehydrator = _Dehydrator()
rt.logger = _Logger()
rt.config = {"surfacing": {"dehydrate_concurrency": 8}}
sys.modules["tools._runtime"] = rt

from tools.breath._concurrent import dehydrate_many  # noqa: E402


def bucket(i, content=None):
    return {"id": f"b{i}", "content": content or f"c{i}",
            "metadata": {"tags": ["x"], "valence": 0.5}}


async def main():
    n = 20
    buckets = [bucket(i) for i in range(n)]
    buckets[7]["content"] = "BOOM"

    t0 = time.monotonic()
    out = await dehydrate_many(buckets)
    elapsed = time.monotonic() - t0

    assert len(out) == n, f"length {len(out)} != {n}"
    assert out[7] is None, "failing bucket should be None"
    for i, s in enumerate(out):
        if i == 7:
            continue
        assert s == f"summary::c{i}::v0.5", f"order broken at {i}: {s}"

    serial = n * LATENCY
    assert elapsed < serial / 2, f"no real concurrency: {elapsed:.2f}s vs serial {serial:.2f}s"
    assert concurrent_peak <= 8, f"semaphore breached: peak {concurrent_peak}"

    # meta_transform is applied per bucket
    out2 = await dehydrate_many([bucket(0)], meta_transform=lambda m: {**m, "valence": 0.9})
    assert out2[0].endswith("v0.9"), out2[0]

    # empty input is a no-op
    assert await dehydrate_many([]) == []

    print(f"OK  {n} buckets in {elapsed:.2f}s (serial would be {serial:.2f}s), peak concurrency {concurrent_peak}")

def test_dehydrate_many():
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
