"""
HVAC Service Manual Chunking Pipeline (EdgeMechanic)
=====================================================
Parent/child chunking สำหรับ Trane mini-split service manual
 
heading detection ขับด้วย "เลข section" (number-driven) ไม่ใช่ match ข้อความ body
-> ทนหน้าที่ font เพี้ยน: '6.2' รู้เองว่าแม่คือ '6' แม้ไม่เคยเห็นบรรทัด '6.' สวย ๆ
-> ชื่อ level-2 ดึงจาก TOC หน้า 2 (anchor) ตกมาเป็น 'N.' ถ้า TOC ไม่มี
"""
 
from __future__ import annotations
 
import os
import re
import uuid
import sqlite3
from typing import List, Dict, Optional, Tuple
 
import pdfplumber
import chromadb
 
# ==========================================
# 0. Constants & Dictionaries
# ==========================================
PDF_PATH = "data/raw/trane_minisplit.pdf"
DB_PATH = "trane_manual.db"
CHROMA_PATH = "./chroma_db"
COLLECTION_NAME = "hvac_index"
 
CHILD_WINDOW = 5
CHILD_OVERLAP = 1
GARBAGE_THRESHOLD = 0.35
 
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
 
 
# ==========================================
# Helpers
# ==========================================
def is_garbage(text: Optional[str], threshold: float = GARBAGE_THRESHOLD) -> bool:
    """[FIX-5] ตรวจหน้า embedded-font เพี้ยน."""
    if not text or not text.strip():
        return True
    # cid encoding เกิน 20 ตัว = หน้า garbled จริงๆ
    if text.count("(cid:") > 20:
        return True
    good = sum(c.isspace() or (c.isascii() and c.isalnum()) or c in ".,:;-/°℃%()" for c in text)
    return (good / len(text)) < threshold
 
# [FIX-3] ครอบคลุม CJK + Hiragana/Katakana + Hangul ให้ตรง docstring
_CJK_RANGES = [(0x4E00, 0x9FFF), (0x3400, 0x4DBF), (0x3040, 0x30FF), (0xAC00, 0xD7AF)]
 
def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return any(a <= o <= b for a, b in _CJK_RANGES)
 
def has_cjk(text: Optional[str]) -> bool:
    """มีอักษรจีน/ญี่ปุ่น(kana)/เกาหลี(hangul) อย่างน้อยหนึ่งตัวไหม"""
    return any(_is_cjk(c) for c in (text or ""))
 
def strip_cjk(text: Optional[str]) -> str:
    """ลบเฉพาะอักษร CJK คงตัวลาตินไว้ (เช่น '螺钉 screws' -> ' screws')"""
    return "".join(c for c in (text or "") if not _is_cjk(c))
 
 
def table_to_markdown(table: List[List[Optional[str]]]) -> str:
    """[FIX-4] serialize ตาราง + [FIX-3] strip CJK ออกจากทุก cell."""
    rows = [[strip_cjk((c or "").strip().replace("\n", " ")) for c in row] for row in table if row]
    rows = [r for r in rows if any(x.strip() for x in r)]
    if not rows:
        return ""
    ncol = max(len(r) for r in rows)
    rows = [r + [""] * (ncol - len(r)) for r in rows]
    header = "| " + " | ".join(rows[0]) + " |"
    sep = "| " + " | ".join(["---"] * ncol) + " |"
    body = "\n".join("| " + " | ".join(r) + " |" for r in rows[1:])
    return "\n".join(filter(None, [header, sep, body]))
 
 
def make_chunk(breadcrumb: str, content_lines: List[str], page_num: int, has_tables: bool) -> Dict:
    bl = breadcrumb.lower()
    if "failure code" in bl or "trouble" in bl:
        chunk_type = "flowchart_stub"
    elif "specification" in bl or "dimension" in bl or "thermistor" in bl:
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
# 1. Load PDF
# ==========================================
def load_pdf(path: str) -> List[Dict]:
    pages_data: List[Dict] = []
    if not os.path.exists(path):
        print(f"⚠️ ไม่พบไฟล์: {path}")
        return pages_data
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            garbage = is_garbage(text)
            lines = [] if garbage else [ln.strip() for ln in text.split("\n") if ln.strip()]
            tables = [] if garbage else (page.extract_tables() or [])
            pages_data.append({
                "page_num": i + 1, "lines": lines, "tables": tables,
                "has_tables": len(tables) > 0, "garbage": garbage,
            })
    return pages_data
 
 
# ==========================================
# 2. Heading detection (number-driven)  [FIX-1]
# ==========================================
ROMAN = {"Ⅰ": 1, "Ⅱ": 2, "Ⅲ": 3, "Ⅳ": 4, "Ⅴ": 5, "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}
R_PART = re.compile(r"^(?:PART|Part)\s+(\S+)")
R_APPENDIX = re.compile(r"^(?:APPENDIX|Appendix)\b")
# เลขต้องมี "จุดภายใน" (6.2) หรือ "จุดท้ายเลขเดี่ยว" (1.)
# -> กัน false positive 'N Title' ของ PCB legend ('1 Transformer IN')
R_NUM = re.compile(r"^(\d+(?:\.\d+)+|\d+\.)\s+([A-Za-z].*)$")
 
 
def part_index(line: str) -> Optional[int]:
    m = R_PART.match(line.strip())
    return ROMAN.get(m.group(1)) if m else None
 
 
def parse_heading(line: str, valid_top: set = None, top_titles: dict = None) -> Optional[Tuple[str, object]]:
    line = line.strip()
    if R_PART.match(line):
        return ("part", line)
    if R_APPENDIX.match(line):
        return ("appendix", line)
    m = R_NUM.match(line)
    if not m:
        return None
    if line.endswith("?"):
        return None
    if len(line.split()) > 18:
        return None
    num = tuple(int(x) for x in m.group(1).rstrip(".").split("."))
    if len(num) == 1:
        if valid_top is None or num[0] not in valid_top:
            return None
        # เลขเดี่ยว: ต้องขึ้นต้นตรงกับ title จริงจาก TOC (กัน "1. Test voltage...")
        if top_titles is not None:
            expected = top_titles.get(num, "")
            # เทียบ prefix หลังตัด "N. " ออก —ยืดหยุ่นเรื่อง whitespace/punctuation เล็กน้อย
            expected_text = expected.split(".", 1)[-1].strip().lower()[:15]
            line_text = line.split(".", 1)[-1].strip().lower()[:15]
            if expected_text and not line_text.startswith(expected_text[:10]):
                return None
    return ("section", num)

 
 
def parse_toc(pages: List[Dict]) -> Dict[int, Dict[Tuple[int, ...], str]]:
    toc: Dict[int, Dict[Tuple[int, ...], str]] = {}
    cur: Optional[int] = None
    leader = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.+?)[.\u2026]{2,}\s*\d+\s*$")
    for page in pages[:4]:
        for ln in page["lines"]:
            pi = part_index(ln)
            if pi is not None:
                cur = pi
                toc.setdefault(cur, {})
                continue
            # เพิ่ม: เจอ APPENDIX ให้หยุด parse TOC
            if R_APPENDIX.match(ln.strip()):
                cur = None
                continue
            m = leader.match(ln.strip())
            if m and cur is not None:
                num = tuple(int(x) for x in m.group(1).split("."))
                toc[cur][num] = f"{m.group(1)}. {m.group(2).strip()}"
    return toc
 
 
def build_breadcrumb(part: str, num: Optional[Tuple[int, ...]], titles: Dict) -> str:
    segs = [part] if part else []
    if num:
        for d in range(1, len(num) + 1):
            anc = num[:d]
            segs.append(titles.get(anc, ".".join(map(str, anc)) + "."))
    return " > ".join(segs)
 
 
# ==========================================
# 3. Build Parent Chunks
# ==========================================
def build_chunks(pages_data: List[Dict],
                 toc: Optional[Dict[int, Dict[Tuple[int, ...], str]]] = None) -> List[Dict]:
    toc = toc or {}
    chunks: List[Dict] = []
    part = ""
    num: Optional[Tuple[int, ...]] = None
    titles: Dict[Tuple[int, ...], str] = {}
    lines: List[str] = []
    page_no = 1
    has_tables = False

    # # สร้าง valid_top จาก TOC
    # valid_top = set()
    # for part_sections in toc.values():
    #     for sec_num in part_sections:       # ← เปลี่ยนจาก num เป็น sec_num
    #         if len(sec_num) == 1:
    #             valid_top.add(sec_num[0])
    
    valid_top: Dict[int, set] = {}
    for part_idx, part_sections in toc.items():
        valid_top[part_idx] = set()
        for sec_num in part_sections:
            if len(sec_num) == 1:
                valid_top[part_idx].add(sec_num[0])

    top_titles_by_part: Dict[int, Dict[Tuple[int, ...], str]] = {}
    for part_idx, part_sections in toc.items():
        top_titles_by_part[part_idx] = {n: t for n, t in part_sections.items() if len(n) == 1}

    def flush():
        nonlocal lines, has_tables
        if lines:
            chunks.append(make_chunk(build_breadcrumb(part, num, titles), lines, page_no, has_tables))
        lines = []
        has_tables = False
 
    for page in pages_data:

        if page["garbage"]:
            continue
        pn = page["page_num"]
        current_part_idx = part_index(part) if part else None
        current_valid_top = valid_top.get(current_part_idx) if current_part_idx is not None else None

        current_top_titles = top_titles_by_part.get(current_part_idx)


        for line in page["lines"]:
            h = parse_heading(line, valid_top=current_valid_top, top_titles=current_top_titles)
            if h:
                flush()
                kind, val = h
                if kind == "part":
                    part = val
                    num = None
                    titles = dict(toc.get(part_index(val) or -1, {}))
                    current_part_idx = part_index(val)
                    current_valid_top = valid_top.get(current_part_idx)
                    current_top_titles = top_titles_by_part.get(current_part_idx)
                elif kind == "appendix":
                    part = val
                    num = None
                    titles = {}
                    current_part_idx = None
                    current_valid_top = None
                    current_top_titles = None 
                else:
                    num = val
                    titles.setdefault(num, line)
                page_no = pn
            else:
                clean = strip_cjk(line).strip()
                if clean:
                    lines.append(clean)
                page_no = pn
        for tbl in page["tables"]:
            md = table_to_markdown(tbl)
            if md:
                lines.append("\n[TABLE]\n" + md)
                has_tables = True
 
    flush()  # [FIX-2] flush chunk สุดท้าย
    return chunks
 
 
# ==========================================
# 4. Modifiers
# ==========================================
def inject_aliases(chunk: Dict, alias_dict: Dict[str, List[str]]) -> Dict:
    text = chunk["content"].lower()
    injected = set()
    for canonical, aliases in alias_dict.items():
        pattern = r"(?<![a-z0-9])" + re.escape(canonical) + r"(?![a-z0-9])"
        if re.search(pattern, text):
            injected.add(canonical)
            injected.update(aliases)
    if injected:
        chunk["metadata"] = f"[Keywords: {', '.join(sorted(injected))}]"
    return chunk
 
 
def build_error_reference(chunks: List[Dict]) -> str:
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
# 5. Child chunking
# ==========================================
def make_children(parent: Dict) -> List[Dict]:
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
            "metadata": {"parent_id": parent["id"], "page_num": parent["page_num"], "type": parent["chunk_type"]},
        })
        if i + CHILD_WINDOW >= len(lines):
            break
    return children
 
 
# ==========================================
# 6. Save to Stores
# ==========================================
def save_to_stores(chunks: List[Dict]):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS parent_chunks (
            id TEXT PRIMARY KEY, breadcrumb TEXT, content TEXT,
            chunk_type TEXT, page_num INTEGER, metadata TEXT
        )
    """)
    cur.execute("DELETE FROM parent_chunks")
    cur.executemany(
        "INSERT INTO parent_chunks (id, breadcrumb, content, chunk_type, page_num, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(c["id"], c["breadcrumb"], c["content"], c["chunk_type"], c["page_num"], c["metadata"]) for c in chunks],
    )
    conn.commit()
    conn.close()
 
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
 
    # [smell] ใช้ factory ตัวเดียวกับ retriever -> EF ไม่ drift (single source of truth)
    from retriever import make_embedding_function
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=make_embedding_function(),
        metadata={"hnsw:space": "cosine"},  # distance อ่านง่าย 0–2 ตอน calibrate
    )
 
    docs, metas, ids = [], [], []
    for parent in chunks:
        for child in make_children(parent):
            docs.append(child["document"])
            metas.append(child["metadata"])
            ids.append(child["id"])
 
    BATCH = 256
    for i in range(0, len(docs), BATCH):
        collection.add(documents=docs[i:i + BATCH], metadatas=metas[i:i + BATCH], ids=ids[i:i + BATCH])
 
    print(f"✅ บันทึก {len(chunks)} parent chunks / {len(docs)} child chunks เรียบร้อย")
 
 
# =========================================
# 7. Main
# =========================================
def run_pipeline(pdf_path: str = PDF_PATH) -> List[Dict]:
    print("🚀 เริ่ม Chunking Pipeline...")
    pages = load_pdf(pdf_path)
    if not pages:
        print("❌ ดึงข้อมูลจาก PDF ไม่ได้")
        return []
    good = sum(1 for p in pages if not p["garbage"])
    print(f"📄 โหลด {len(pages)} หน้า (อ่านได้ {good}, ข้าม garbage {len(pages) - good})")
 
    toc = parse_toc(pages)
    print(f"📑 parse TOC: {[(k, len(v)) for k, v in sorted(toc.items())]}")
 
    chunks = build_chunks(pages, toc)
    print(f"🧩 สร้าง parent chunks {len(chunks)} ชิ้น")
 
    chunks = process_chunk_modifiers(chunks)
    print("💉 ฉีด aliases + master error table เรียบร้อย")
 
    save_to_stores(chunks)
    return chunks
 
 
if __name__ == "__main__":
    os.makedirs("data/raw", exist_ok=True)
    run_pipeline()
