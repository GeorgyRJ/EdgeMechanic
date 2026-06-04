import pdfplumber
import sqlite3
import chromadb
import re
import uuid
from typing import List, Dict, Tuple

# ==========================================
# 1. Load PDF & Extract Text/Tables
# ==========================================
def load_pdf(path: str) -> List[Dict]:
    """
    อ่าน PDF คืนค่าเป็น List ของหน้าที่ประกอบด้วย text และ tables
    (ปรับจาก blocks เป็น line/table เพื่อให้เหมาะกับ pdfplumber)
    """
    pages_data = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages):
            # ดึงข้อความและแยกเป็นบรรทัด
            text = page.extract_text()
            lines = text.split('\n') if text else []
            
            # ดึงตาราง (ถ้ามี)
            tables = page.extract_tables()
            
            pages_data.append({
                "page_num": i + 1,
                "lines": lines,
                "tables": tables
            })
    return pages_data

# ==========================================
# 2. Detect Headings (Hierarchical Pathing)
# ==========================================
def detect_headings(lines: List[str], page_num: int) -> List[Tuple[str, int, int]]:
    """
    ใช้ Regex จับระดับของ Heading คืนค่า (text, level, page_num)
    """
    headings = []
    
    # Regex Patterns สำหรับคู่มือช่างมาตรฐาน
    r_level1 = re.compile(r"^(Part|Section)\s+[IVX0-9]+", re.IGNORECASE) # Part 1, Section I
    r_level2 = re.compile(r"^\d+\.\s+[A-Z]")                             # 1. Safety, 8. Troubleshooting
    r_level3 = re.compile(r"^\d+\.\d+(\.\d+)?\s+[A-Z]")                  # 8.1 Error Codes, 8.2.1 Sensors
    
    for line in lines:
        line_clean = line.strip()
        if not line_clean:
            continue
            
        if r_level1.match(line_clean):
            headings.append((line_clean, 1, page_num))
        elif r_level3.match(line_clean): # เช็ก L3 ก่อน L2 เพราะ Pattern ซ้อนทับกัน
            headings.append((line_clean, 3, page_num))
        elif r_level2.match(line_clean):
            headings.append((line_clean, 2, page_num))
            
    return headings

# ==========================================
# 3. Build Parent Chunks
# ==========================================
def build_chunks(pages_data: List[Dict]) -> List[Dict]:
    """
    รวบรวม lines เป็น Chunk โดยใช้ State ของ Headings เพื่อสร้าง Breadcrumb
    """
    chunks = []
    current_path = {1: "", 2: "", 3: ""}
    current_content = []
    
    for page in pages_data:
        page_num = page["page_num"]
        headings = detect_headings(page["lines"], page_num)
        
        for line in page["lines"]:
            line_clean = line.strip()
            
            # 1. อัปเดต Breadcrumb State ถ้าบรรทัดนี้คือ Heading
            is_heading = False
            for h_text, h_level, _ in headings:
                if line_clean == h_text:
                    current_path[h_level] = h_text
                    # ล้างค่าระดับที่ต่ำกว่า (เช่น ถ้าเจอ H2 ใหม่ ต้องล้าง H3 เก่าทิ้ง)
                    for l in range(h_level + 1, 4):
                        current_path[l] = ""
                    is_heading = True
                    
                    # ตัดก้อนเก่าเก็บเข้าลิสต์ (เมื่อเจอหัวข้อระดับ 2 หรือ 3 ใหม่)
                    if current_content and h_level in [2, 3]:
                        breadcrumb = " > ".join([v for k, v in current_path.items() if v])
                        chunks.append({
                            "id": str(uuid.uuid4()),
                            "breadcrumb": breadcrumb,
                            "content": "\n".join(current_content),
                            "chunk_type": "procedure", # ค่าเริ่มต้น (ต้องเขียนลอจิกวิเคราะห์เพิ่ม)
                            "page_num": page_num,
                            "metadata": ""
                        })
                        current_content = [] # รีเซ็ตเนื้อหาสำหรับก้อนใหม่
                    break
            
            # 2. สะสมเนื้อหา
            if not is_heading and line_clean:
                current_content.append(line_clean)
                
    return chunks

# ==========================================
# 4. Inject Aliases (Dictionary Match)
# ==========================================
def inject_aliases(chunk: Dict, alias_dict: Dict[str, List[str]]) -> Dict:
    """
    เช็ก Exact Match ถ่าเจอคำหลัก ให้ยัดคำพ้องความหมาย (Aliases) ลงไปใน metadata
    """
    text_to_search = chunk["content"].lower()
    injected_aliases = set()
    
    for canonical_term, aliases in alias_dict.items():
        if canonical_term in text_to_search:
            injected_aliases.update(aliases)
            injected_aliases.add(canonical_term)
            
    if injected_aliases:
        # แปะเข้าไปใน metadata string เช่น "[Keywords: บอร์ดคอยล์ร้อน, เมนบอร์ดนอก]"
        chunk["metadata"] = f"[Keywords: {', '.join(injected_aliases)}]"
        
    return chunk

# ==========================================
# 5. Save to Stores (Parent-Child Strategy)
# ==========================================
def save_to_stores(chunks: List[Dict]):
    """
    Parent -> SQLite (เก็บเต็ม)
    Child  -> ChromaDB (หั่นย่อย + แปะ Breadcrumb นำหน้า)
    """
    # 1. Setup SQLite (Parent Store)
    conn = sqlite3.connect("trane_manual.db")
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS parent_chunks (
            id TEXT PRIMARY KEY,
            breadcrumb TEXT,
            content TEXT,
            chunk_type TEXT,
            metadata TEXT
        )
    ''')
    
    # 2. Setup ChromaDB (Child Store)
    chroma_client = chromadb.PersistentClient(path="./chroma_db")
    collection = chroma_client.get_or_create_collection(name="hvac_index")
    
    for chunk in chunks:
        # --- Save Parent ---
        cursor.execute('''
            INSERT INTO parent_chunks (id, breadcrumb, content, chunk_type, metadata)
            VALUES (?, ?, ?, ?, ?)
        ''', (chunk["id"], chunk["breadcrumb"], chunk["content"], chunk["chunk_type"], chunk["metadata"]))
        
        # --- Split into Children & Save to Vector DB ---
        # สมมติวิธีหั่นแบบง่าย: แบ่งทุกๆ 3-4 บรรทัด หรือนับ Token (ในที่นี้ใช้บรรทัดเพื่อเป็นตัวอย่าง)
        lines = chunk["content"].split('\n')
        child_size = 5 # บรรทัดต่อ 1 child chunk
        
        for i in range(0, len(lines), child_size):
            child_content = "\n".join(lines[i:i + child_size])
            child_id = f"{chunk['id']}-child-{i}"
            
            # Title-wrapping: ยัด Breadcrumb + Metadata เข้าไปใน Text ที่จะถูกทำ Embedding
            enriched_text = f"[Path: {chunk['breadcrumb']}] {chunk['metadata']}\n{child_content}"
            
            collection.add(
                documents=[enriched_text],
                metadatas=[{"parent_id": chunk["id"], "page_num": chunk["page_num"]}],
                ids=[child_id]
            )
            
    conn.commit()
    conn.close()
    print(f"✅ บันทึก {len(chunks)} Parent Chunks ลง SQLite และ ChromaDB เรียบร้อยแล้ว")