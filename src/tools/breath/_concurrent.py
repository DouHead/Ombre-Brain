"""
========================================
tools/breath/_concurrent.py — 并发脱水辅助
========================================

浮现路径上的每一条桶都要调一次 dehydrate（外部模型调用）。原本四处的
循环都是 `for b in buckets: await dehydrate(...)`，串行执行。

问题：脱水缓存按 (prompt 版本 + 人名 + 原文) 做 key，_PROMPT_VERSION 一
升级，整个缓存同时失效。冷缓存下 breath 需要连续发 20+ 次模型请求，
远超 MCP 客户端 60 秒超时；而超时又意味着缓存写不回去，于是永远冷着，
自己恢复不了。

解决：把每个循环的脱水阶段改成并发（带信号量限流），冷启动一次跑完，
缓存写满，之后每次 breath 基本免费。

关键行为：
- 保持输入顺序返回，调用方原有的 token 预算/截断逻辑不变
- 单条失败不影响整批：返回 None，由调用方走各自的 fallback
- 并发度可用 config.surfacing.dehydrate_concurrency 调整，默认 8

不做什么（边界）：
- 不做 token 预算判断（那是各分支自己的事）
- 不改缓存逻辑，不改 prompt

对外暴露：dehydrate_many(buckets) → list[str | None]（与输入等长、同序）
========================================
"""

import asyncio

from .. import _runtime as rt
from utils import strip_wikilinks


_DEFAULT_CONCURRENCY = 8


def _concurrency() -> int:
    try:
        cfg = rt.config.get("surfacing", {}) or {}
        n = int(cfg.get("dehydrate_concurrency") or _DEFAULT_CONCURRENCY)
    except Exception:
        n = _DEFAULT_CONCURRENCY
    return max(1, min(n, 16))


async def dehydrate_many(buckets: list[dict], meta_transform=None) -> list[str | None]:
    """Dehydrate buckets concurrently, preserving input order.

    Returns a list the same length as `buckets`; an entry is None when that
    bucket's dehydration failed, so callers keep their existing per-bucket
    fallback behaviour instead of losing the whole batch.

    `meta_transform(clean_meta) -> clean_meta` lets a caller adjust the
    display metadata before dehydration (search.py shifts valence by the
    query's mood); it is applied per bucket, inside the worker.
    """
    if not buckets:
        return []

    sem = asyncio.Semaphore(_concurrency())

    async def one(b: dict) -> str | None:
        async with sem:
            try:
                clean_meta = {k: v for k, v in (b.get("metadata") or {}).items() if k != "tags"}
                if meta_transform is not None:
                    clean_meta = meta_transform(clean_meta)
                return await rt.dehydrator.dehydrate(
                    strip_wikilinks(b.get("content") or ""), clean_meta
                )
            except Exception as e:
                rt.logger.warning(
                    f"concurrent dehydrate failed / 并发脱水失败 "
                    f"[{b.get('id')}]: {type(e).__name__}: {e}"
                )
                return None

    return await asyncio.gather(*(one(b) for b in buckets))
