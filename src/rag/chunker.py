"""
HVAC Service Manual Chunking Pipeline (EdgeMechanic)
=====================================================
Parent/child chunking สำหรับ Trane mini-split service manual
- ดึงตารางเข้า chunk (failure code, thermistor, torque ฯลฯ)
- รองรับ heading หลายระดับ + เลขโรมัน Unicode (Part Ⅰ/Ⅱ)
- Alias injection ภาษาไทยแบบ word-boundary
- กรองหน้า garbage (embedded font เพี้ยน)
- Batch insert ลง SQLite + ChromaDB, รันซ้ำได้ (idempotent)

แก้จาก version เดิม 10 จุด — ดูหมายเหตุ [FIX-n] ในโค้ด
"""

from __future__ import annotations

import os
import re
import uuid
import sqlite3
from typing import List, Dict, Optional

import pdfplumber
import chromadb

# ==========================================
# 0. Constants & Dictionaries
# ==========================================
PDF_PATH = "data/raw/trane_minisplit.pdf"
DB_PATH = "trane_manual.db"
CHROMA_PATH = "./chroma_db"
COLLECTION_NAME = "hvac_index"

CHILD_WINDOW = 5      # บรรทัดต่อ child chunk
CHILD_OVERLAP = 1     # [FIX-6] overlap กันเนื้อหาขาดกลางเงื่อนไข
GARBAGE_THRESHOLD = 0.35  # สัดส่วน ascii ที่อ่านได้ขั้นต่ำ

# Alias สำหรับช่างที่พิมพ์ศัพท์สแลงไทย
ALIAS_DICT: Dict[str, List[str]] = {
    "outdoor pcb": ["บอร์ดคอยล์ร้อน", "เมนบอร์ดนอก", "บอร์ด odu"],
    "indoor pcb": ["บอร์ดคอยล์เย็น", "เมนบอร์ดใน", "บอร์ด idu"],
    "capacitor": ["แคป", "คาปา", "ตัวเก็บประจุ"],
    "compressor": ["คอม", "คอมเพรสเซอร์", "ลูกสูบ"],
    "4-way valve": ["โฟร์เวย์", "วาล์วสลับทิศ"],
    "evaporator": ["คอยล์เย็น", "แผงเย็น"],
    "condenser": ["คอยล์ร้อน", "แผงร้อน"],
    "thermistor": ["เซ็นเซอร์อุณหภูมิ", "เทอร์มิสเตอร์"],
    "e0": ["error e0", "โค้ด e0", "สื่อสารขัดข้อง"],
    "e5": ["error e5", "โค้ด e5"],
    "p0": ["error p0", "โค้ด p0", "ipm protection"],
}

# [FIX-7] master error table จะถูก "สร้างจากตารางจริง" ในเอกสาร ไม่ hardcode แล้ว
# ดู build_error_reference() ด้านล่าง


# ==========================================
# Helpers
# ==========================================
def is_garbage(text: Optional[str], threshold: float = GARBAGE_THRESHOLD) -> bool:
    """[FIX-5] ตรวจหน้า embedded-font เพี้ยน เช่น ')\"9`YWfcb]W7cbhfc'."""
    if not text or not text.strip():
        return True
    good = sum(c.isspace() or (c.isascii() and c.isalnum()) or c in ".,:;-/°℃%()" for c in text)
    return (good / len(text)) < threshold


def table_to_markdown(table: List[List[Optional[str]]]) -> str:
    """[FIX-4] serialize ตาราง pdfplumber เป็น markdown ให้ retriever อ่านได้."""
    rows = [[(c or "").strip().replace("\n", " ") for c in row] for row in table if row]
    rows = [r for r in rows if any(r)]  # ทิ้งแถวว่าง
    if not rows:
        return ""
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]  # pad ให้เท่ากัน
    header = "| " + " | ".join(rows[0]) + " |"
    sep = "| " + " | ".join(["---"] * ncol) + " |"
    body = "\n".join("| " + " | ".join(r) + " |" for r in rows[1:])
    return "\n".join(filter(None, [header, sep, body]))


def make_chunk(breadcrumb: str, content_lines: List[str], page_num: int,
               has_tables: bool) -> Dict:
    """[FIX-10] ศูนย์กลางการสร้าง chunk + กำหนด chunk_type."""
    breadcrumb_lower = breadcrumb.lower()
    if "failure code" in breadcrumb_lower or "trouble" in breadcrumb_lower:
        chunk_type = "flowchart_stub"
    elif "specification" in breadcrumb_lower or "dimension" in breadcrumb_lower or "thermistor" in breadcrumb_lower:
        chunk_type = "spec"
    elif has_tables:
        chunk_type = "table"
    else:
        chunk_type = "procedure"

    return {
        "id": str(uuid.uuid4()),
        "breadcrumb": breadcrumb,
        "content": "\n".join(content_lines).strip(),
        "chunk_type": chunk_type,
        "page_num": page_num,
        "metadata": "",
    }


# ==========================================
# 1. Load PDF & Extract
# ==========================================
def load_pdf(path: str) -> List[Dict]:
    pages_data: List[Dict] = []
    if not os.path.exists(path):
        print(f"⚠️ ไม่พบไฟล์: {path}")
        return pages_data

    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            # [FIX-5] ข้ามหน้าขยะทั้งหน้า แต่ยังเก็บ slot ไว้กันเลขหน้าเพี้ยน
            garbage = is_garbage(text)
            lines = [] if garbage else [ln.strip() for ln in text.split("\n") if ln.strip()]
            tables = [] if garbage else (page.extract_tables() or [])
            pages_data.append({
                "page_num": i + 1,
                "lines": lines,
                "tables": tables,
                "has_tables": len(tables) > 0,
                "garbage": garbage,
            })
    return pages_data


# ==========================================
# 2. Detect Headings
# ==========================================
# [FIX-1] รองรับ Part + เลขโรมัน Unicode และ section 1–4 ระดับ (เช่น 6.2.10.1)
R_PART = re.compile(r"^(PART|Part)\s+[ⅠⅡⅢⅣⅤIVX\d]+", re.IGNORECASE)
# 1. / 1 / 2.1 / 6.2.10.1  — รับจุดท้ายเลขเดี่ยว ('1.') ด้วย
R_SECTION_L2 = re.compile(r"^(\d+)\.\s+[A-Z][a-z]")  # "2. Specifications" — รับได้
R_SECTION_L3 = re.compile(r"^(\d+)\.(\d+)(\.(\d+))*\.?\s+[A-Za-z]") # "6.2.1 Auto mode"
R_APPENDIX = re.compile(r"^(APPENDIX|Appendix)\b", re.IGNORECASE)

TOP_SECTION_PREFIXES = {
    "1. Important Notice",
    "2. Specifications",
    "3. Product",           # รับทั้ง Production และ Product
    "4. Refrigeration cycle diagram",
    "5. Electric Diagram",
    "6. Electronic Controller Introduction",
    "1. Notes for installation and maintenance",
    "2. Installation",
    "3. Maintenance",
    "4. Exploded view and parts list",
    "5. Disassembly IDU & ODU",
}

def heading_level(line: str) -> Optional[int]:
    if R_PART.match(line) or R_APPENDIX.match(line):
        return 1
    # เช็กแบบ startswith แทน exact match
    for prefix in TOP_SECTION_PREFIXES:
        if line.strip().startswith(prefix):
            return 2
    if R_SECTION_L3.match(line):
        depth = line.count(".")
        return min(2 + depth, 4)
    return None


# ==========================================
# 3. Build Parent Chunks (state machine)
# ==========================================
def build_chunks(pages_data: List[Dict]) -> List[Dict]:
    chunks: List[Dict] = []
    current_path = {1: "", 2: "", 3: "", 4: ""}
    current_lines: List[str] = []
    current_page = 1
    current_has_tables = False

    def flush():
        """[FIX-2/FIX-3] flush ด้วย path 'ปัจจุบัน' ก่อนอัปเดตไป heading ใหม่."""
        nonlocal current_lines, current_has_tables
        if current_lines:
            breadcrumb = " > ".join(v for v in current_path.values() if v)
            chunks.append(make_chunk(breadcrumb, current_lines, current_page, current_has_tables))
        current_lines = []
        current_has_tables = False

    for page in pages_data:
        if page["garbage"]:
            continue
        page_num = page["page_num"]

        for line in page["lines"]:
            lvl = heading_level(line)
            if lvl is not None:
                # [FIX-3] flush chunk เก่าด้วย path เดิม "ก่อน" เปลี่ยน path
                flush()
                current_path[lvl] = line
                for deeper in range(lvl + 1, 5):
                    current_path[deeper] = ""
                current_page = page_num
            else:
                current_lines.append(line)
                current_page = page_num

        # [FIX-4] ผนวกตารางของหน้านี้เข้า chunk ที่กำลังสะสมอยู่
        for tbl in page["tables"]:
            md = table_to_markdown(tbl)
            if md:
                current_lines.append("\n[TABLE]\n" + md)
                current_has_tables = True

    # [FIX-2] flush chunk สุดท้าย (เดิมหายตลอด — Appendix 4 thermistor!)
    flush()
    return chunks


# ==========================================
# 4. Pipeline Modifiers
# ==========================================
def inject_aliases(chunk: Dict, alias_dict: Dict[str, List[str]]) -> Dict:
    text = chunk["content"].lower()
    injected = set()
    for canonical, aliases in alias_dict.items():
        # [FIX-7] word-boundary กัน false positive (เช่น 'e5' ใน 'these50V')
        pattern = r"(?<![a-z0-9])" + re.escape(canonical) + r"(?![a-z0-9])"
        if re.search(pattern, text):
            injected.add(canonical)
            injected.update(aliases)
    if injected:
        chunk["metadata"] = f"[Keywords: {', '.join(sorted(injected))}]"
    return chunk


def build_error_reference(chunks: List[Dict]) -> str:
    """[FIX-7] ดึง failure-code table จริงจากเอกสาร แทนการ hardcode E1/U4."""
    for c in chunks:
        if c["chunk_type"] == "flowchart_stub" and "[TABLE]" in c["content"]:
            for block in c["content"].split("[TABLE]"):
                if re.search(r"\b(E0|E1|P0)\b", block):
                    return block.strip()
    return ""


def process_chunk_modifiers(chunks: List[Dict]) -> List[Dict]:
    error_ref = build_error_reference(chunks)
    for chunk in chunks:
        inject_aliases(chunk, ALIAS_DICT)
        if chunk["chunk_type"] == "flowchart_stub" and error_ref and "[TABLE]" not in chunk["content"]:
            chunk["content"] = (
                f"[Master Error Reference]\n{error_ref}\n\n"
                f"[Flowchart Content]\n{chunk['content']}"
            )
    return chunks


# ==========================================
# 5. Child chunking with overlap
# ==========================================
def make_children(parent: Dict) -> List[Dict]:
    """[FIX-6] sliding window + overlap, แต่ไม่ตัดกลางบล็อกตาราง."""
    children = []
    lines = parent["content"].split("\n")
    step = max(CHILD_WINDOW - CHILD_OVERLAP, 1)
    for i in range(0, len(lines), step):
        window = lines[i:i + CHILD_WINDOW]
        body = "\n".join(window).strip()
        if not body:
            continue
        enriched = f"[Path: {parent['breadcrumb']}] {parent['metadata']}\n{body}"
        children.append({
            "id": f"{parent['id']}-c{i}",
            "document": enriched,
            "metadata": {
                "parent_id": parent["id"],
                "page_num": parent["page_num"],
                "type": parent["chunk_type"],
            },
        })
        if i + CHILD_WINDOW >= len(lines):
            break
    return children


# ==========================================
# 6. Save to Stores (idempotent + batched)
# ==========================================
def save_to_stores(chunks: List[Dict]):
    # ---- SQLite ----
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS parent_chunks (
            id TEXT PRIMARY KEY,
            breadcrumb TEXT,
            content TEXT,
            chunk_type TEXT,
            page_num INTEGER,
            metadata TEXT
        )
    """)
    cur.execute("DELETE FROM parent_chunks")  # [FIX-9] รันซ้ำไม่ซ้อน
    cur.executemany(
        "INSERT INTO parent_chunks (id, breadcrumb, content, chunk_type, page_num, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(c["id"], c["breadcrumb"], c["content"], c["chunk_type"], c["page_num"], c["metadata"])
         for c in chunks],
    )
    conn.commit()
    conn.close()

    # ---- ChromaDB ----
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    # [FIX-9] เคลียร์ collection เดิมก่อน build ใหม่
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass

    from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
    ef = SentenceTransformerEmbeddingFunction(
        model_name="/home/georgy/.cache/huggingface/hub/models--BAAI--bge-m3",
        device="cuda"
    )
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=ef  # ← แทน default ONNXMiniLM
    )

    docs, metas, ids = [], [], []
    for parent in chunks:
        for child in make_children(parent):
            docs.append(child["document"])
            metas.append(child["metadata"])
            ids.append(child["id"])

    # [FIX-8] batch add (เดิม add ทีละตัวในลูป ช้ามาก)
    BATCH = 256
    for i in range(0, len(docs), BATCH):
        collection.add(
            documents=docs[i:i + BATCH],
            metadatas=metas[i:i + BATCH],
            ids=ids[i:i + BATCH],
        )

    print(f"✅ บันทึก {len(chunks)} parent chunks / {len(docs)} child chunks เรียบร้อย")


# ==========================================
# 7. Main
# ==========================================
def run_pipeline(pdf_path: str = PDF_PATH) -> List[Dict]:
    print("🚀 เริ่ม Chunking Pipeline...")
    pages = load_pdf(pdf_path)
    if not pages:
        print("❌ ดึงข้อมูลจาก PDF ไม่ได้")
        return []
    good = sum(1 for p in pages if not p["garbage"])
    print(f"📄 โหลด {len(pages)} หน้า (อ่านได้ {good}, ข้าม garbage {len(pages) - good})")

    chunks = build_chunks(pages)
    print(f"🧩 สร้าง parent chunks {len(chunks)} ชิ้น")

    chunks = process_chunk_modifiers(chunks)
    print("💉 ฉีด aliases + master error table เรียบร้อย")

    save_to_stores(chunks)
    return chunks


if __name__ == "__main__":
    os.makedirs("data/raw", exist_ok=True)
    run_pipeline()
