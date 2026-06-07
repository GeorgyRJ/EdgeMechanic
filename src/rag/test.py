"""
Unit tests สำหรับ chunk_pipeline.py
รันด้วย: pytest test_chunk_pipeline.py -v   (หรือ python test_chunk_pipeline.py)
 
เน้นเทส 4 บั๊กที่ version เดิมพัง:
  - chunk สุดท้ายหาย
  - breadcrumb ติด section ถัดไป
  - ตารางหาย
  - heading โรมัน/หลายระดับจับไม่ได้
"""
 
import chunker as cp
 
 
def _fake_pages():
    """จำลอง pages_data โดยไม่ต้องเปิด PDF จริง."""
    return [
        {"page_num": 1, "garbage": False, "has_tables": False,
         "lines": ["Part Ⅰ Technical Information",
                   "1. Important Notice",
                   "This manual is for qualified technicians only."],
         "tables": []},
        {"page_num": 2, "garbage": False, "has_tables": True,
         "lines": ["3.1 Failure code",
                   "List of codes below."],
         "tables": [[["Code", "Reason"], ["E0", "Communication failure"], ["E5", "Mismatch"]]]},
        {"page_num": 3, "garbage": True, "has_tables": False,
         "lines": [], "tables": []},  # หน้า garbage ต้องถูกข้าม
        {"page_num": 4, "garbage": False, "has_tables": False,
         "lines": ["6.2.10.1 Overload protection",
                   "If OPT >= 62C unit stops working."]},
    ]
 
 
def test_heading_levels():
    assert cp.heading_level("Part Ⅰ Technical Information") == 1   # โรมัน Unicode
    assert cp.heading_level("APPENDIX") == 1
    assert cp.heading_level("1. Important Notice") == 2
    assert cp.heading_level("2.1 Specifications") == 3
    assert cp.heading_level("6.2.10.1 Overload protection") == 4   # 4 ระดับ
    assert cp.heading_level("just a normal sentence") is None
 
 
def test_last_chunk_not_lost():
    """[FIX-2] เนื้อหาหลัง heading สุดท้ายต้องถูกเก็บ."""
    pages = _fake_pages()
    for p in pages:
        p.setdefault("tables", [])
    chunks = cp.build_chunks(pages)
    last = chunks[-1]
    assert "62C unit stops" in last["content"]
    assert "6.2.10.1" in last["breadcrumb"]
 
 
def test_breadcrumb_belongs_to_own_section():
    """[FIX-3] chunk ของ 'Important Notice' ต้องไม่ติด breadcrumb ของ 3.1."""
    chunks = cp.build_chunks([p | {"tables": p.get("tables", [])} for p in _fake_pages()])
    notice = next(c for c in chunks if "manual is for qualified" in c["content"])
    assert "Important Notice" in notice["breadcrumb"]
    assert "Failure code" not in notice["breadcrumb"]
 
 
def test_table_captured():
    """[FIX-4] ตารางต้องโผล่ใน content เป็น markdown."""
    chunks = cp.build_chunks([p | {"tables": p.get("tables", [])} for p in _fake_pages()])
    fc = next(c for c in chunks if "Failure code" in c["breadcrumb"])
    assert "[TABLE]" in fc["content"]
    assert "E0" in fc["content"] and "Communication failure" in fc["content"]
    assert fc["chunk_type"] == "flowchart_stub"
 
 
def test_garbage_filtered():
    """[FIX-5] หน้า garbage ไม่กลายเป็น chunk."""
    chunks = cp.build_chunks([p | {"tables": p.get("tables", [])} for p in _fake_pages()])
    assert all("YWfcb" not in c["content"] for c in chunks)
 
 
def test_alias_word_boundary():
    """[FIX-7] 'e5' ใน 'these50V' ต้องไม่ trigger alias."""
    c1 = cp.inject_aliases({"content": "show E5 failure code", "metadata": ""}, cp.ALIAS_DICT)
    assert "e5" in c1["metadata"].lower()
    c2 = cp.inject_aliases({"content": "voltage these50V measured", "metadata": ""}, cp.ALIAS_DICT)
    assert "error e5" not in c2["metadata"].lower()
 
 
def test_children_have_overlap():
    """[FIX-6] child chunks ต้อง overlap กัน."""
    parent = {"id": "x", "breadcrumb": "A > B", "metadata": "[Keywords: x]",
              "content": "\n".join(f"line{i}" for i in range(12)), "page_num": 1,
              "chunk_type": "procedure"}
    kids = cp.make_children(parent)
    assert len(kids) >= 2
    # บรรทัดสุดท้ายของ child แรก ควรปรากฏใน child ที่สองด้วย (overlap)
    assert "line4" in kids[0]["document"] and "line4" in kids[1]["document"]
 
 
if __name__ == "__main__":
    import sys, traceback
    funcs = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for f in funcs:
        try:
            f()
            print(f"  ✅ {f.__name__}")
        except Exception:
            failed += 1
            print(f"  ❌ {f.__name__}")
            traceback.print_exc()
    print(f"\n{len(funcs) - failed}/{len(funcs)} passed")
    sys.exit(1 if failed else 0)
