"""Module RAG Retriever — Truy xuất tri thức kỹ thuật & dữ liệu giá từ Database Garage VTV.
"""
import re
import unicodedata
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session

from backend.app.ai.rag.knowledge_base import GARAGE_KNOWLEDGE_BASE, KnowledgeChunk

def strip_vietnamese_accents(text: str) -> str:
    """Loại bỏ dấu tiếng Việt để phục vụ matching không dấu."""
    if not text:
        return ""
    text = unicodedata.normalize('NFD', text)
    text = re.sub(r'[\u0300-\u036f]', '', text)
    return text.replace('đ', 'd').replace('Đ', 'D')

def calculate_relevance_score(query: str, chunk: KnowledgeChunk) -> float:
    """Tính điểm độ tương quan giữa câu hỏi của khách hàng và một tri thức."""
    q_raw = query.lower()
    q_no_accent = strip_vietnamese_accents(q_raw)
    
    score = 0.0

    # 1. Khớp từ khóa chuyên môn (Keywords match)
    for kw in chunk.keywords:
        kw_lower = kw.lower()
        kw_no_accent = strip_vietnamese_accents(kw_lower)
        if kw_lower in q_raw:
            # Từ khóa có độ dài càng lớn thì trọng số càng cao
            score += 3.0 * (len(kw_lower.split()) + 1)
        elif kw_no_accent in q_no_accent:
            score += 2.0 * len(kw_no_accent.split())

    # 2. Khớp tiêu đề chunk (Title match)
    title_words = [w for w in chunk.title.lower().split() if len(w) > 2]
    for w in title_words:
        if w in q_raw:
            score += 1.5

    # 3. Ưu tiên đặc biệt cho các câu hỏi kỹ thuật chuyên sâu (Symptom bonus)
    if chunk.chunk_id == "AC-001":
        # Triệu chứng điều hòa có mùi ẩm mốc khi mới bật / tháo taplo / nạp gas
        if ("mùi" in q_raw and ("ẩm" in q_raw or "chua" in q_raw or "hôi" in q_raw or "bật" in q_raw)):
            score += 12.0
        if "tháo taplo" in q_raw or "nạp gas" in q_raw or "nap gas" in q_no_accent or "thao taplo" in q_no_accent:
            score += 10.0
        if "vẫn lạnh" in q_raw or "con lanh" in q_no_accent:
            score += 8.0

    elif chunk.chunk_id == "AC-003":
        if "so sánh" in q_raw and ("nội soi" in q_raw or "tháo taplo" in q_raw):
            score += 15.0

    elif chunk.chunk_id == "BR-001":
        if "rung" in q_raw and ("phanh" in q_raw or "vô lăng" in q_raw or "đạp phanh" in q_raw):
            score += 12.0

    elif chunk.chunk_id == "BR-003":
        if "so sánh" in q_raw and ("láng đĩa" in q_raw or "thay đĩa" in q_raw):
            score += 15.0

    elif chunk.chunk_id == "ENG-001":
        if any(w in q_raw for w in ["hụt ga", "khó nổ", "máy rung", "giật"]):
            score += 12.0

    elif chunk.chunk_id == "ENG-002":
        if any(w in q_raw for w in ["check engine", "cá vàng", "báo lỗi", "đèn taplo"]):
            score += 12.0

    elif chunk.chunk_id == "SUS-001":
        if "lục cục" in q_raw or ("kêu" in q_raw and "gầm" in q_raw) or "gờ giảm tốc" in q_raw:
            score += 12.0

    elif chunk.chunk_id == "SUS-002":
        if "lệch lái" in q_raw or "nhao lái" in q_raw or ("vô lăng" in q_raw and "lệch" in q_raw):
            score += 12.0

    elif chunk.chunk_id == "MNT-001":
        if any(w in q_raw for w in ["bảng giá", "chi phí", "bảo dưỡng", "cấp 1", "cấp 2", "cấp 4"]):
            score += 10.0

    elif chunk.chunk_id == "MNT-002":
        if "so sánh" in q_raw and ("khoáng" in q_raw or "tổng hợp" in q_raw or "dầu nhớt" in q_raw):
            score += 15.0

    return score

def retrieve_knowledge(
    query: str,
    top_k: int = 2,
    db: Optional[Session] = None
) -> Tuple[List[KnowledgeChunk], Dict[str, Any]]:
    """
    Truy xuất Top K tri thức kỹ thuật phù hợp nhất + Bổ sung bảng giá phụ tùng từ database.
    """
    scored_chunks: List[Tuple[float, KnowledgeChunk]] = []

    for chunk in GARAGE_KNOWLEDGE_BASE:
        s = calculate_relevance_score(query, chunk)
        if s > 0:
            scored_chunks.append((s, chunk))

    # Sắp xếp theo điểm tương quan giảm dần
    scored_chunks.sort(key=lambda x: x[0], reverse=True)
    top_chunks = [c for _, c in scored_chunks[:top_k]]

    # Truy xuất dữ liệu thời gian thực từ Database (Live DB Augmentation)
    live_db_data: Dict[str, Any] = {
        "related_services": [],
        "related_parts": []
    }

    if db:
        try:
            from backend.app.models import Service, Part
            q_clean = query.lower()

            # Tìm kiếm dịch vụ liên quan trong DB
            services = db.query(Service).all()
            for s in services:
                s_name = (s.name or "").lower()
                if any(w in s_name for w in ["điều hòa", "phanh", "dầu", "bảo dưỡng", "buồng đốt", "họng ga"]):
                    if any(w in q_clean for w in s_name.split() if len(w) > 3):
                        live_db_data["related_services"].append({
                            "name": s.name,
                            "price": s.labor_cost
                        })

            # Tìm kiếm phụ tùng liên quan trong DB
            parts = db.query(Part).all()
            for p in parts:
                p_name = (p.name or "").lower()
                if any(w in p_name for w in ["lọc gió", "má phanh", "dầu nhớt", "gas", "bugi", "rô-tuyn"]):
                    if any(w in q_clean for w in p_name.split() if len(w) > 3):
                        live_db_data["related_parts"].append({
                            "name": p.name,
                            "price": p.unit_price,
                            "stock": p.stock_quantity
                        })
        except Exception:
            pass

    return top_chunks, live_db_data
