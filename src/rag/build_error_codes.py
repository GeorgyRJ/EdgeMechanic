"""
build_error_codes.py — สร้างตาราง error_codes จากตาราง 3.1 Failure code
=====================================================================
รันครั้งเดียว (build-time). parse markdown table ใน chunk 3.1
→ เก็บลง SQLite table error_codes (code, description, solution)

เฟส 1: description จาก 3.1 (พร้อมใช้)
เฟส 2: solution จาก 3.2 ค่อยเติมทีหลัง (ตอนนี้ NULL)

รัน: python3 build_error_codes.py
"""
import sqlite3
import re

DB_PATH = "trane_manual.db"
FAILURE_CODE_CHUNK_ID = "c76f874f-094c-4964-be76-19f80dd4141c"  # chunk 3.1

# code ขึ้นต้นด้วย E/P/F/b ตามด้วยอักขระเดียว (เช่น E5, P0, FA, bj)
CODE_RE = re.compile(r"^[EPFb][0-9A-Za-z]$")


def parse_failure_table(content: str) -> dict[str, str]:
    """parse markdown table → {code: description}
    จัดการ row ที่ reason ล้นหลายบรรทัด (ช่อง code ว่าง = ต่อของ code ก่อนหน้า)
    """
    rows: dict[str, list[str]] = {}
    last_code = None

    for line in content.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 3:
            continue

        code = cells[1]
        # รวมทุกช่องที่มีเนื้อหา (ข้าม '/' ที่แปลว่า "ไม่มีข้อมูล")
        body = " ".join(c for c in cells[2:] if c and c != "/")

        if CODE_RE.match(code):           # row ขึ้น code ใหม่
            rows[code] = [body] if body else []
            last_code = code
        elif code == "" and last_code:    # row ต่อ (ช่อง code ว่าง)
            if body:
                rows[last_code].append(body)

    # รวม parts เป็น description เดียว + เก็บกวาด whitespace
    return {code: " ".join(parts).strip() for code, parts in rows.items()}


def main():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()

    # 1. ดึง content ตาราง 3.1
    row = cur.execute(
        "SELECT content FROM parent_chunks WHERE id = ?",
        (FAILURE_CODE_CHUNK_ID,),
    ).fetchone()
    if not row:
        raise SystemExit("❌ ไม่เจอ chunk 3.1 Failure code — เช็ค chunk id")
    content = row[0]

    # 2. parse
    codes = parse_failure_table(content)
    if not codes:
        raise SystemExit("❌ parse ไม่ได้ codes เลย — เช็ค format ตาราง")

    # 3. สร้างตาราง (solution เผื่อเฟส 2 — ตอนนี้ NULL)
    cur.execute("DROP TABLE IF EXISTS error_codes")
    cur.execute("""
        CREATE TABLE error_codes (
            code        TEXT PRIMARY KEY,
            description TEXT NOT NULL,
            solution    TEXT
        )
    """)

    # 4. insert
    cur.executemany(
        "INSERT INTO error_codes (code, description, solution) VALUES (?, ?, NULL)",
        [(code, desc) for code, desc in codes.items()],
    )
    con.commit()

    # 5. verify — โชว์ตัวอย่าง + นับ
    print(f"✅ เก็บ {len(codes)} codes ลงตาราง error_codes")
    print("\nตัวอย่าง (E1 reason ล้นบรรทัด / E5 มีแค่ 3.1 / E0 ปกติ):")
    for c in ["E0", "E1", "E5", "bj"]:
        r = cur.execute(
            "SELECT code, description FROM error_codes WHERE code = ?", (c,)
        ).fetchone()
        if r:
            print(f"  {r[0]:4} → {r[1]}")

    con.close()


if __name__ == "__main__":
    main()