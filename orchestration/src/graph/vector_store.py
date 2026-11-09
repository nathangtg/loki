from __future__ import annotations

import logging
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional

LOGGER = logging.getLogger(__name__)

_QDRANT_PATH = str(Path(__file__).resolve().parent.parent.parent / "data" / "qdrant_data")
_COLLECTION = "loki_knowledge"
_EMBED_MODEL = "google/embeddinggemma-300m"
_EMBED_DIM = 768

_embedding_model = None
_QUERY_VEC_CACHE: OrderedDict[str, list[float]] = OrderedDict()
_DOC_VEC_CACHE: OrderedDict[str, Any] = OrderedDict()
_QUERY_CACHE_MAX = 128
_DOC_CACHE_MAX = 512


def _get_embedding_model():
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer

        LOGGER.info("Loading embedding model %s ...", _EMBED_MODEL)
        _embedding_model = SentenceTransformer(_EMBED_MODEL)
        LOGGER.info("Embedding model loaded (dim=%d)", _EMBED_DIM)
    return _embedding_model


def embed_query(text: str, use_cache: bool = True) -> list[float]:
    if use_cache and text in _QUERY_VEC_CACHE:
        _QUERY_VEC_CACHE.move_to_end(text)
        return _QUERY_VEC_CACHE[text]

    model = _get_embedding_model()
    vec: Any = model.encode_query(text)
    out = [float(x) for x in (vec.tolist() if hasattr(vec, "tolist") else vec)]
    if use_cache and text:
        _QUERY_VEC_CACHE[text] = out
        if len(_QUERY_VEC_CACHE) > _QUERY_CACHE_MAX:
            _QUERY_VEC_CACHE.popitem(last=False)
    return out


def embed_document(text: str, use_cache: bool = True) -> list[float]:
    if use_cache and text in _DOC_VEC_CACHE:
        _DOC_VEC_CACHE.move_to_end(text)
        cached = _DOC_VEC_CACHE[text]
        return [float(x) for x in (cached.tolist() if hasattr(cached, "tolist") else cached)]

    model = _get_embedding_model()
    vec: Any = model.encode_document(text)
    out = [float(x) for x in (vec.tolist() if hasattr(vec, "tolist") else vec)]
    if use_cache and text:
        import numpy as np

        _DOC_VEC_CACHE[text] = np.asarray(out, dtype=np.float32)
        if len(_DOC_VEC_CACHE) > _DOC_CACHE_MAX:
            _DOC_VEC_CACHE.popitem(last=False)
    return out


def embed_documents(texts: list[str], batch_size: int = 32, use_cache: bool = True) -> Any:
    import numpy as np

    if not texts:
        return np.empty((0, _EMBED_DIM), dtype=np.float32)

    if not use_cache:
        model = _get_embedding_model()
        if hasattr(model, "encode_document"):
            return model.encode_document(texts, batch_size=batch_size, convert_to_numpy=True)
        return model.encode(texts, batch_size=batch_size, convert_to_numpy=True)

    missing_texts: list[str] = []
    missing_set: set[str] = set()
    for t in texts:
        if t in _DOC_VEC_CACHE:
            _DOC_VEC_CACHE.move_to_end(t)
        elif t not in missing_set:
            missing_set.add(t)
            missing_texts.append(t)

    if missing_texts:
        model = _get_embedding_model()
        if hasattr(model, "encode_document"):
            new_vecs = model.encode_document(
                missing_texts, batch_size=batch_size, convert_to_numpy=True
            )
        else:
            new_vecs = model.encode(
                missing_texts, batch_size=batch_size, convert_to_numpy=True
            )
        new_vecs = np.asarray(new_vecs, dtype=np.float32)
        for t, v in zip(missing_texts, new_vecs):
            _DOC_VEC_CACHE[t] = v
            if len(_DOC_VEC_CACHE) > _DOC_CACHE_MAX:
                _DOC_VEC_CACHE.popitem(last=False)

    return np.stack([_DOC_VEC_CACHE[t] for t in texts], axis=0)


_NS = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


def _to_uuid(entry_id: str) -> str:
    return str(uuid.uuid5(_NS, entry_id))


class VectorStore:

    _instance: VectorStore | None = None

    def __new__(cls) -> VectorStore:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._client = None
        self._init_qdrant()

    def _init_qdrant(self) -> None:
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, VectorParams
            import atexit

            self._client = QdrantClient(path=_QDRANT_PATH)
            atexit.register(lambda c=self._client: c.close() if c is not None else None)

            collections = [c.name for c in self._client.get_collections().collections]
            if _COLLECTION not in collections:
                self._client.create_collection(
                    collection_name=_COLLECTION,
                    vectors_config=VectorParams(
                        size=_EMBED_DIM,
                        distance=Distance.COSINE,
                    ),
                )
                LOGGER.info("VectorStore: created collection '%s'", _COLLECTION)
            else:
                info = self._client.get_collection(_COLLECTION)
                LOGGER.info(
                    "VectorStore: opened collection '%s' (%d points)",
                    _COLLECTION,
                    info.points_count,
                )
        except Exception as exc:
            LOGGER.warning("VectorStore: Qdrant init failed: %s", exc)
            self._client = None

    def upsert(
        self, entry_id: str, category: str, text: str, metadata: dict[str, Any] | None = None
    ) -> bool:
        if self._client is None:
            return False

        from qdrant_client.models import PointStruct

        point_id = _to_uuid(entry_id)

        try:
            existing = self._client.retrieve(
                collection_name=_COLLECTION,
                ids=[point_id],
                with_payload=True,
            )
            if existing and existing[0].payload and existing[0].payload.get("text") == text:
                return False

            vec = embed_document(text)
            payload = {
                "entry_id": entry_id,
                "category": category,
                "text": text,
                **(metadata or {}),
            }
            self._client.upsert(
                collection_name=_COLLECTION,
                points=[PointStruct(id=point_id, vector=vec, payload=payload)],
            )
            return True
        except Exception as exc:
            LOGGER.warning("VectorStore upsert failed for '%s': %s", entry_id, exc)
            return False

    def search(
        self,
        query: str,
        category: Optional[str] = None,
        top_k: int = 3,
        threshold: float = 0.40,
    ) -> list[dict[str, Any]]:
        if self._client is None:
            return []

        try:
            query_vec = embed_query(query)
            query_filter = None
            if category:
                from qdrant_client.models import FieldCondition, Filter, MatchValue

                query_filter = Filter(
                    must=[FieldCondition(key="category", match=MatchValue(value=category))]
                )

            response = self._client.query_points(
                collection_name=_COLLECTION,
                query=query_vec,
                query_filter=query_filter,
                limit=top_k,
                score_threshold=threshold,
            )

            results = []
            for point in response.points:
                payload = point.payload or {}
                results.append(
                    {
                        "id": point.id,
                        "category": payload.get("category", ""),
                        "text": payload.get("text", ""),
                        "metadata": {
                            k: v
                            for k, v in payload.items()
                            if k not in ("category", "text", "entry_id")
                        },
                        "score": point.score,
                    }
                )
            return results
        except Exception as exc:
            LOGGER.warning("VectorStore search failed: %s", exc)
            return []

    def sync_known_sites(self, data: dict[str, Any]) -> int:
        count = 0

        for name, url in data.get("sites", {}).items():
            eid = f"site::{name}"
            text = f"{name} ({url})"
            if self.upsert(eid, "site", text, {"name": name, "url": url}):
                LOGGER.info("VectorStore: embedded site '%s'", name)
                count += 1

        for domain, paths in data.get("navigation", {}).items():
            for dest, parents in paths.items():
                eid = f"nav::{domain}::{dest}"
                text = f"{dest} on {domain}"
                meta = {"domain": domain, "destination": dest, "path": parents}
                if self.upsert(eid, "navigation", text, meta):
                    LOGGER.info("VectorStore: embedded nav '%s' on %s", dest, domain)
                    count += 1

        for domain, creds in data.get("credentials", {}).items():
            eid = f"cred::{domain}"
            text = f"login credentials for {domain}"
            meta = {
                "domain": domain,
                "has_username": bool(creds.get("username")),
                "has_password": bool(creds.get("password")),
            }
            if self.upsert(eid, "credential", text, meta):
                LOGGER.info("VectorStore: embedded cred for %s", domain)
                count += 1

        if count > 0:
            LOGGER.info("VectorStore: synced %d new entries from known_sites", count)

        return count

    def build_context(self, goal: str, top_k: int = 5) -> str:
        results = self.search(goal, top_k=top_k, threshold=0.35)
        if not results:
            return ""

        parts = ["RELEVANT KNOWLEDGE (from previous sessions):"]
        for r in results:
            meta = r["metadata"]
            if r["category"] == "site":
                parts.append(f"  • Site: {meta.get('name', '')} → {meta.get('url', '')}")
            elif r["category"] == "navigation":
                path_list = meta.get("path", [])
                path = " → ".join(path_list) if isinstance(path_list, list) else str(path_list)
                parts.append(
                    f"  • Navigation: {meta.get('destination', '')} on {meta.get('domain', '')} (click {path} first)"
                )
            elif r["category"] == "credential":
                parts.append(f"  • Credentials stored for: {meta.get('domain', '')}")
            parts[-1] += f"  [relevance: {r['score']:.0%}]"

        return "\n".join(parts)

    @property
    def count(self) -> int:
        if self._client is None:
            return 0
        try:
            info = self._client.get_collection(_COLLECTION)
            return info.points_count or 0
        except Exception:
            return 0
