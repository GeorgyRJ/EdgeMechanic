"""
diagnose.py — ตรวจว่าปัญหา "ไม่พบข้อมูล" อยู่ที่ retrieval หรือ generation
วางไว้ใน src/rag/ แล้วรันจาก repo root:  python3 src/rag/diagnose.py
"""
import sqlite3
import chromadb
 
from retriever import (
    HVACRetriever, DB_PATH, CHROMA_PATH, COLLECTION_NAME, make_embedding_function
)
 
print("=" * 60)
print("STEP 1 — ตรวจว่า index มีของจริงไหม และชี้ path ถูกที่")
print("=" * 60)
print("CHROMA_PATH :", CHROMA_PATH)
print("DB_PATH     :", DB_PATH)
 
client = chromadb.PersistentClient(path=CHROMA_PATH)
try:
    col = client.get_collection(COLLECTION_NAME, embedding_function=make_embedding_function())
    print("chroma child count :", col.count())
    print("collection metadata:", col.metadata)   # ดูว่า hnsw:space เป็น cosine ไหม
except Exception as e:
    print("❌ เปิด collection ไม่ได้:", e)
    raise SystemExit
 
conn = sqlite3.connect(DB_PATH)
n_parent = conn.execute("SELECT COUNT(*) FROM parent_chunks").fetchone()[0]
print("sqlite parent count:", n_parent)
# ดูตัวอย่าง breadcrumb ที่ index ไว้ (ตรวจว่า chunk หน้า disassembly/capacitor มีจริง)
print("\nตัวอย่าง breadcrumb ที่เกี่ยวกับ capacitor/disassembly:")
for row in conn.execute(
    "SELECT breadcrumb FROM parent_chunks "
    "WHERE lower(content) LIKE '%capacitor%' OR breadcrumb LIKE '%isassembl%' LIMIT 5"
):
    print("  -", row[0][:80])
 
print("\n" + "=" * 60)
print("STEP 2 — ยิง query ดู distance + เนื้อ child ที่ match")
print("=" * 60)
queries = [
    "คาปาซิเตอร์ คอยล์ร้อน แคป capacitor",
    "Error Code E5",
    "ใช้รีโมท Daikin กับ Trane",   # อันนี้ควร 'ไม่เกี่ยว' จริง → distance ควรสูง
]
for q in queries:
    res = col.query(query_texts=[q], n_results=5,
                    include=["distances", "documents", "metadatas"])
    print(f"\nquery: {q}")
    for d, doc in zip(res["distances"][0], res["documents"][0]):
        print(f"  dist={d:.3f}  {doc[:85].strip()!r}")
 
print("\n" + "=" * 60)
print("STEP 3 — context เต็มที่ retriever ส่งให้โมเดล (คำถาม capacitor)")
print("=" * 60)
r = HVACRetriever()
ctx = r.retrieve("อธิบายวิธีเปลี่ยนคาปาซิเตอร์คอยล์ร้อน", top_k_child=8, top_k_parent=3)
print(ctx[:1800] if ctx else "(ว่าง — retriever ไม่คืนอะไรเลย)")
r.close()
conn.close()
 