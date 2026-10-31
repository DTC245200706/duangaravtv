"""Module RAG Generator — Tổng hợp câu trả lời dựa trên tri thức đã truy xuất (Augmented Generation).
"""
from typing import List, Dict, Any, Optional, Tuple
from sqlalchemy.orm import Session

from backend.app.ai.rag.knowledge_base import KnowledgeChunk

def build_rag_augmented_prompt(
    query: str,
    retrieved_chunks: List[KnowledgeChunk],
    live_db_data: Dict[str, Any]
) -> str:
    """Đóng gói toàn bộ tri thức kỹ thuật & bảng giá thành Prompt ngữ cảnh chuẩn RAG."""
    context_lines = [
        "=== HỆ THỐNG TRI THỨC KỸ THUẬT & DỮ LIỆU ĐÃ TRUY XUẤT TỪ GARAGE VTV (RAG CONTEXT) ==="
    ]

    for idx, chunk in enumerate(retrieved_chunks, 1):
        context_lines.append(f"\n[TÀI LIỆU {idx}: {chunk.title.upper()} (Chủ đề: {chunk.category})]")
        context_lines.append(chunk.content)
        if chunk.recommended_combo:
            context_lines.append(f"• Combo đề xuất: {chunk.recommended_combo}")
        if chunk.estimated_cost:
            context_lines.append(f"• Chi phí dự kiến: {chunk.estimated_cost}")
        if chunk.time_estimate:
            context_lines.append(f"• Thời gian thi công: {chunk.time_estimate}")

    services = live_db_data.get("related_services", [])
    if services:
        context_lines.append("\n[DỮ LIỆU DỊCH VỤ NIÊM YẾT TỪ DATABASE GARAGE]:")
        for s in services[:4]:
            context_lines.append(f"• {s['name']}: Tiền công {s['price']:,.0f} VNĐ")

    parts = live_db_data.get("related_parts", [])
    if parts:
        context_lines.append("\n[DỮ LIỆU PHỤ TÙNG TỒN KHO THỜI GIAN THỰC TỪ DATABASE]:")
        for p in parts[:4]:
            context_lines.append(f"• {p['name']}: {p['price']:,.0f} VNĐ (Tồn kho: {p['stock']} cái)")

    context_lines.append("\n=== YÊU CẦU TƯ VẤN CỦA KHÁCH HÀNG ===")
    context_lines.append(f'"{query}"')

    context_lines.append("""\n=== CHỈ THỊ CỐ VẤN DỊCH VỤ ===")
- Hãy đóng vai Cố vấn Kỹ thuật AI chuyên nghiệp của Garage VTV.
- Trả lời trực diện, chi tiết, chính xác các thắc mắc trong câu hỏi (ví dụ: có cần nạp gas không? có cần tháo taplo không? nên kiểm tra gì trước?).
- Phân tích nguyên nhân kỹ thuật, đưa ra các bước kiểm tra tuần tự từ đơn giản đến chuyên sâu.
- Báo giá minh bạch theo dữ liệu hệ thống và hướng dẫn khách đặt lịch hẹn.""")

    return "\n".join(context_lines)

def synthesize_rag_local_response(
    query: str,
    retrieved_chunks: List[KnowledgeChunk],
    live_db_data: Dict[str, Any]
) -> str:
    """
    Bộ tổng hợp tri thức RAG cục bộ (Local RAG Synthesizer):
    Được kích hoạt khi LLM API bên ngoài gặp sự cố mạng hoặc quota.
    Tự động biên tập tri thức từ các chunk truy xuất được thành câu trả lời chuyên sâu, trực diện, mạch lạc.
    """
    if not retrieved_chunks:
        # Fallback chung khi không có chunk phù hợp
        return """🤖 **TRỢ LÝ KỸ THUẬT AI GARAGE VTV CHÀO BẠN!**

Tôi là Trợ Lý Kỹ Thuật AI chuyên tư vấn dịch vụ bảo dưỡng và chẩn đoán lỗi xe ô tô tại Garage VTV.

Tôi có thể hỗ trợ bạn:
• 🩺 **Chẩn đoán triệu chứng:** Rung phanh, hụt ga khó nổ, điều hòa có mùi/mất lạnh, gầm kêu lục cục, lệch lái...
• 💰 **Báo giá & Dự toán:** Minh bạch bảng giá bảo dưỡng định kỳ các cấp (5k, 10k, 20k, 40k km) và combo chuyên sâu.
• ⚖️ **So sánh dịch vụ:** Nội soi dàn lạnh vs tháo taplo, láng đĩa vs thay mới, dầu khoáng vs tổng hợp...
• 📅 **Đặt lịch tiếp nhận ưu tiên:** Giữ chỗ cầu nâng không phải chờ đợi.

💡 *Bạn hãy mô tả cụ thể triệu chứng xe hoặc dịch vụ đang quan tâm để tôi giải đáp chi tiết nhé!*"""

    main_chunk = retrieved_chunks[0]

    # Biên tập câu trả lời từ tri thức đã truy xuất
    lines = []
    lines.append(f"🩺 **TƯ VẤN KỸ THUẬT: {main_chunk.title.upper()}**\n")
    lines.append(f"Chào bạn! Dựa trên câu hỏi của bạn về: *\"{query}\"*, Cố vấn Kỹ thuật Garage VTV giải đáp chi tiết như sau:\n")

    # Đưa nội dung phân tích chuyên môn vào
    lines.append(main_chunk.content)

    # Bổ sung phụ tùng & dịch vụ từ Database thực tế nếu có
    services = live_db_data.get("related_services", [])
    parts = live_db_data.get("related_parts", [])
    if services or parts:
        lines.append("\n📋 **Bảng giá tham khảo từ hệ thống Garage VTV:**")
        for s in services[:2]:
            lines.append(f"• Dịch vụ: **{s['name']}** — Tiền công: **{s['price']:,.0f} VNĐ**")
        for p in parts[:2]:
            lines.append(f"• Phụ tùng chính hãng: **{p['name']}** — Đơn giá: **{p['price']:,.0f} VNĐ**")

    # Thông tin Combo & Thời gian
    if main_chunk.recommended_combo:
        lines.append(f"\n💡 **Gói đề xuất:** **{main_chunk.recommended_combo}**")
    if main_chunk.estimated_cost:
        lines.append(f"• **Chi phí dự toán:** **{main_chunk.estimated_cost}** (Đã bao gồm công kiểm tra & vật tư)")
    if main_chunk.time_estimate:
        lines.append(f"• **Thời gian hoàn thành:** khoảng **{main_chunk.time_estimate}**")

    # Kêu gọi hành động (Call-to-Action)
    lines.append("\n📅 *Bạn hãy bấm nút **Xác Nhận Đặt Lịch** ngay phía trên hoặc gửi số điện thoại để được Garage VTV giữ chỗ cầu nâng và soi kiểm tra miễn phí nhé!*")

    return "\n".join(lines)
