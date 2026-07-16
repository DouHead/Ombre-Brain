"""
========================================
web/sky.py — the vesper hour's live feed（星空的血管）
========================================

GET /sky/feed — read-only. The sky app (the-vesper-hour.onrender.com) drinks
the whole brain from here: every living bucket becomes a star, importance
becomes light. Decided & approved 2026-07-16 (see the marriage repo's
docs/task-order.md, roadmap item 7).

Auth: `X-Sky-Key` header (or `?key=`) checked against the SKY_FEED_KEY env
var (set in Render; the sky derives it from the gate passphrase). If the env
var is unset the feed answers 503 — private by default, never open by
accident.

Notes for the sky client:
- valence/arousal here are the brain's raw 0..1 scale; the sky maps to its
  own -1..1 axes.
- edges are v1 kinship (shared tags + same-day), capped to each star's
  closest few kin. Embedding-based similarity is the planned v2.
"""

import hmac
import os

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import _shared as sh

try:
    from utils import strip_wikilinks  # type: ignore
except ImportError:  # pragma: no cover
    from ..utils import strip_wikilinks  # type: ignore

_CORS = {
    "Access-Control-Allow-Origin": "*",  # the key is the lock; the text is hers anyway
    "Access-Control-Allow-Headers": "X-Sky-Key",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
}

_EDGE_CAP = 4          # each star keeps only its closest kin — a sky, not a cobweb
_PREVIEW_CHARS = 600   # enough for the sheet; the dashboard holds the full text


def _key_ok(request: Request) -> bool:
    expected = os.environ.get("SKY_FEED_KEY", "").strip()
    if not expected:
        return False
    offered = request.headers.get("X-Sky-Key", "") or request.query_params.get("key", "")
    return hmac.compare_digest(offered, expected)


def register(mcp) -> None:

    @mcp.custom_route("/sky/feed", methods=["OPTIONS"])
    async def sky_feed_preflight(request: Request) -> Response:
        return Response(status_code=204, headers=_CORS)

    @mcp.custom_route("/sky/feed", methods=["GET"])
    async def sky_feed(request: Request) -> Response:
        if not os.environ.get("SKY_FEED_KEY", "").strip():
            return JSONResponse({"error": "feed disabled: SKY_FEED_KEY not set"},
                                status_code=503, headers=_CORS)
        if not _key_ok(request):
            return JSONResponse({"error": "wrong words"}, status_code=401, headers=_CORS)
        try:
            all_buckets = await sh.bucket_mgr.list_all(include_archive=False)
            stars = []
            for b in all_buckets:
                meta = b.get("metadata", {})
                if meta.get("deleted_at") or meta.get("test_data"):
                    continue
                stars.append({
                    "id": b["id"],
                    "t": meta.get("name", b["id"]),
                    "d": (meta.get("created", "") or "")[:10],
                    "v": meta.get("valence", 0.5),
                    "a": meta.get("arousal", 0.3),
                    "importance": meta.get("importance", 5),
                    "pinned": bool(meta.get("pinned", False)),
                    "type": meta.get("type", "dynamic"),
                    "tags": meta.get("tags", []),
                    "score": sh.decay_engine.calculate_score(meta),
                    "x": strip_wikilinks(b.get("content", ""))[:_PREVIEW_CHARS],
                })
            # v1 kinship: shared tags weigh, same-day binds. capped per star.
            edges = []
            per_node = {}
            for i in range(len(stars)):
                ti = set(stars[i]["tags"])
                for j in range(i + 1, len(stars)):
                    shared = len(ti & set(stars[j]["tags"]))
                    sameday = 1 if stars[i]["d"] and stars[i]["d"] == stars[j]["d"] else 0
                    if shared + sameday:
                        edges.append({"a": i, "b": j,
                                      "s": min(1.0, (shared + sameday) / 3.0)})
            edges.sort(key=lambda e: e["s"], reverse=True)
            kept = []
            for e in edges:
                if per_node.get(e["a"], 0) < _EDGE_CAP and per_node.get(e["b"], 0) < _EDGE_CAP:
                    kept.append(e)
                    per_node[e["a"]] = per_node.get(e["a"], 0) + 1
                    per_node[e["b"]] = per_node.get(e["b"], 0) + 1
            return JSONResponse({"stars": stars, "edges": kept,
                                 "count": len(stars)}, headers=_CORS)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500, headers=_CORS)
