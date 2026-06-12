"""
HVAC Retriever (EdgeMechanic) — small-to-big parent/child retrieval
====================================================================
Flow: query -> BGE-M3 embed -> Chroma child search -> dedup+rank parents
      -> SQLite fetch parents -> format context string สำหรับ Qwen3
 
⚠️ สำคัญ: embedding function ต้องเป็นตัวเดียวกับตอน index (chunk_pipeline.py)
   ไม่งั้น vector คนละมิติ -> retrieval มั่ว ใช้ make_embedding_function()
   ร่วมกันทั้งสองฝั่ง
"""
 
from __future__ import annotations
 
import os
import sqlite3
import unicodedata
from typing import List, Dict, Optional, Tuple
 
import chromadb
from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
 
# ==========================================
# 1. Config
# ==========================================
# anchor default ที่ repo root โดยไม่ผูกกับ CWD (สมมติไฟล์อยู่ src/rag/retriever.py
# -> ขึ้นสองชั้นถึง repo root ที่ pipeline สร้าง db/chroma_db ไว้)
# override ได้ด้วย env var เวลา deploy บนเครื่องช่าง โดยไม่ต้องแก้โค้ด
_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_BASE = os.path.dirname(os.path.dirname(_HERE))
BASE_DIR = os.environ.get("EDGEMECHANIC_HOME", _DEFAULT_BASE)
 
DB_PATH = os.path.join(BASE_DIR, "trane_manual.db")
CHROMA_PATH = os.path.join(BASE_DIR, "chroma_db")
COLLECTION_NAME = "hvac_index"
EMBEDDING_MODEL_PATH = os.environ.get("BGE_M3_PATH", "/mnt/c/Users/ROG/bge-m3")
 
# distance สูงกว่านี้ถือว่า "ไม่เกี่ยว" แล้วตัดทิ้ง (ค่านี้ขึ้นกับ metric ของ
# collection — ตั้ง collection เป็น cosine แล้วค่าจะอยู่ 0–2, calibrate เอง)
DEFAULT_MAX_DISTANCE: Optional[float] = None  # None = ปิด (ค่อยเปิดหลัง calibrate)
DEFAULT_MAX_CHARS_PER_PARENT = 4000           # budget กัน context ล้น Qwen3-1.7B
 
 
# ==========================================
# 2. Shared embedding function  [FIX-1]
# ==========================================
def _pick_device(prefer: Optional[str] = None) -> str:
    """auto-detect: cuda ถ้ามี ไม่งั้น cpu (สำคัญสำหรับ deploy offline บนเครื่องช่าง)."""
    if prefer:
        return prefer
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"
 
 
def make_embedding_function(model_path: str = EMBEDDING_MODEL_PATH,
                            device: Optional[str] = None) -> SentenceTransformerEmbeddingFunction:
    """ใช้ตัวนี้ทั้งฝั่ง index และ retrieve เพื่อให้ vector ตรงกันเสมอ."""
    return SentenceTransformerEmbeddingFunction(
        model_name=model_path,
        device=_pick_device(device),
    )
 
 
# ==========================================
# 3. Pure parent-selection logic (testable)  [FIX-2/FIX-4]
# ==========================================
def select_parents(metadatas: List[dict],
                   distances: List[float],
                   top_k_parent: int,
                   max_distance: Optional[float] = None) -> List[str]:
    """
    รับผล child query -> คืน parent_id เรียงตามความเกี่ยวข้อง (min distance ก่อน).
    - aggregate: parent score = distance ต่ำสุดในบรรดา child ของมัน
    - กรอง child ที่ distance เกิน max_distance ทิ้ง (ถ้ากำหนด)
    - dedup, คง relevance order
    """
    best: Dict[str, float] = {}
    for meta, dist in zip(metadatas or [], distances or []):
        pid = (meta or {}).get("parent_id")
        if pid is None:
            continue
        if max_distance is not None and dist > max_distance:
            continue
        if pid not in best or dist < best[pid]:
            best[pid] = dist
    ranked = sorted(best.items(), key=lambda kv: kv[1])  # distance น้อย = เกี่ยวมาก
    return [pid for pid, _ in ranked[:top_k_parent]]
 
 
# ==========================================
# 4. Retriever
# ==========================================
class HVACRetriever:
    def __init__(self,
                 db_path: str = DB_PATH,
                 chroma_path: str = CHROMA_PATH,
                 collection_name: str = COLLECTION_NAME,
                 embedding_function: Optional[SentenceTransformerEmbeddingFunction] = None,
                 max_distance: Optional[float] = DEFAULT_MAX_DISTANCE,
                 max_chars_per_parent: int = DEFAULT_MAX_CHARS_PER_PARENT):
        self.max_distance = max_distance
        self.max_chars_per_parent = max_chars_per_parent
 
        # โหลด embedding model ครั้งเดียว (allow inject สำหรับ test)
        self.ef = embedding_function or make_embedding_function()
 
        self.chroma_client = chromadb.PersistentClient(path=chroma_path)
        # ตั้งใจใช้ get_collection (ไม่ใช่ get_or_create) — retriever ไม่ควรสร้าง index
        # ถ้า collection ไม่มี = ยังไม่ได้ build -> fail ดังพร้อม message ที่ actionable
        # ดีกว่าสร้าง collection เปล่าเงียบ ๆ แล้วคืน "ไม่พบข้อมูล" จนหลงทาง debug
        try:
            self.collection = self.chroma_client.get_collection(
                name=collection_name,
                embedding_function=self.ef,
            )
        except Exception as e:
            raise RuntimeError(
                f"ไม่พบ collection '{collection_name}' ที่ {chroma_path} — "
                f"รัน chunk_pipeline.py เพื่อ build index ก่อน "
                f"(หรือเช็ค CHROMA_PATH / EDGEMECHANIC_HOME ให้ถูก)"
            ) from e
 
        # [FIX-3] ถือ connection เดียว (offline agent ยิงซ้ำบ่อย)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
 
    # ---- core ----
    def retrieve(self, query: str, top_k_child: int = 8, top_k_parent: int = 3) -> str:
        results = self.collection.query(
            query_texts=[query],
            n_results=top_k_child,
            include=["metadatas", "distances"],  # [FIX-5] ต้องดึง distances มาคัด
        )
 
        metas = (results.get("metadatas") or [[]])[0]
        dists = (results.get("distances") or [[]])[0]
 
        parent_ids = select_parents(metas, dists, top_k_parent, self.max_distance)
        if not parent_ids:
            return "ไม่พบข้อมูลอ้างอิงที่เกี่ยวข้องในคู่มือ"
 
        parents = self._fetch_parents(parent_ids)
        return self._format_context(parents)
 
    # ---- SQLite ----
    def _fetch_parents(self, parent_ids: List[str]) -> List[Dict]:
        placeholders = ",".join("?" for _ in parent_ids)
        sql = (f"SELECT id, breadcrumb, content, chunk_type "
               f"FROM parent_chunks WHERE id IN ({placeholders})")
        rows = self._conn.execute(sql, tuple(parent_ids)).fetchall()
 
        # reorder ให้ตรง relevance (IN ไม่รักษาลำดับ)
        by_id = {r[0]: {"breadcrumb": r[1], "content": r[2], "chunk_type": r[3]} for r in rows}
        return [by_id[pid] for pid in parent_ids if pid in by_id]
 
    # ---- format ----
    def _format_context(self, parents: List[Dict]) -> str:
        parts = []
        for i, p in enumerate(parents, 1):
            content = p["content"]
            if len(content) > self.max_chars_per_parent:  # [FIX-6] budget
                content = content[:self.max_chars_per_parent].rstrip() + "\n[...ตัดเนื้อหา]"
            parts.append(
                f"### Reference {i} ({p['chunk_type']}) ###\n"
                f"Location: {p['breadcrumb']}\n"
                f"Details:\n{content}\n"
            )
        return "\n".join(parts)
 
    def close(self):
        self._conn.close()
 
    def __enter__(self):
        return self
 
    def __exit__(self, *exc):
        self.close()
 
 
# ==========================================
# Test Execution
# ==========================================
if __name__ == "__main__":
    with HVACRetriever(max_distance=DEFAULT_MAX_DISTANCE) as retriever:
        test_queries = [
            "E0 communication failure ทำยังไง",
            "แอร์ไม่เย็น คอมเพรสเซอร์ไม่ทำงาน",
            "วิธีถอด indoor unit",
        ]

        for q in test_queries:
            print(f"🔍 Query: {q}\n⏳ Retrieving...\n")
            ctx = retriever.retrieve(query=q, top_k_child=8, top_k_parent=2)
            print("=== Context for LLM ===")
            print(ctx)
            print("=======================\n")

        """
        print(f"🔍 Query: {test_queries}\n⏳ Retrieving...\n")
        ctx = retriever.retrieve(query=test_queries, top_k_child=8, top_k_parent=2)
        print("=== Context for LLM ===")
        print(ctx)
        print("=======================")

        """