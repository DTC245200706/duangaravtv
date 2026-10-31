# -*- coding: utf-8 -*-
import pytest
from backend.app.ai.service import AIService

def test_customer_ai_symptom_consultation():
    # 1. Rung phanh
    ans, _ = AIService._fallback_engine("", "Xe tôi bị rung giật khi đạp phanh và kêu rít")
    assert "cong vênh" in ans or "phanh" in ans
    assert "Láng đĩa phanh" in ans or "phanh" in ans

    # 2. Hụt ga khó nổ
    ans, _ = AIService._fallback_engine("", "Xe bị hụt ga và khó nổ khi tăng tốc")
    assert "đánh lửa" in ans.lower() or "bugi" in ans.lower()
    assert "Combo Phục Hồi Công Suất" in ans or "họng ga" in ans

    # 3. Điều hòa không mát
    ans, _ = AIService._fallback_engine("", "Điều hòa xe không mát và có mùi chua ẩm mốc")
    assert "Dàn lạnh bám nấm mốc" in ans or "mùi" in ans
    assert "Combo Điều Hòa Mát Sâu" in ans or "nội soi" in ans

    # 4. Gầm kêu lục cục
    ans, _ = AIService._fallback_engine("", "Đi qua gờ giảm tốc nghe tiếng kêu lục cục dưới gầm")
    assert "Rô-tuyn" in ans or "càng A" in ans or "giảm xóc" in ans

    # 5. Vô lăng lệch lái
    ans, _ = AIService._fallback_engine("", "Vô lăng bị lệch và xe hay bị nhao lái sang phải")
    assert "Cân chỉnh góc đặt bánh xe 3D" in ans or "nhao lái" in ans

    # 6. Đèn Check Engine
    ans, _ = AIService._fallback_engine("", "Đèn báo lỗi động cơ check engine taplo bật sáng")
    assert "OBD-II" in ans or "máy quét" in ans

def test_customer_ai_cost_consultation():
    ans, _ = AIService._fallback_engine("", "Cho tôi xin bảng giá và chi phí bảo dưỡng xe")
    assert "BẢNG DỰ TOÁN CHI PHÍ" in ans
    assert "Gói Cấp 1" in ans
    assert "Gói Cấp 2" in ans
    assert "Gói Cấp 4" in ans
    assert "Combo Điều Hòa Mát Sâu" in ans

def test_customer_ai_service_comparison():
    # 1. Cấp 1 vs Cấp 2
    ans1, _ = AIService._fallback_engine("", "So sánh bảo dưỡng cấp 1 và cấp 2")
    assert "SO SÁNH BẢO DƯỠNG CẤP 1" in ans1
    assert "| Tiêu chí |" in ans1

    # 2. Dầu khoáng vs Dầu tổng hợp
    ans2, _ = AIService._fallback_engine("", "Nên chọn dầu khoáng hay dầu tổng hợp")
    assert "DẦU NHỚT KHOÁNG VS DẦU TỔNG HỢP" in ans2 or ("DẦU" in ans2 and "TỔNG HỢP" in ans2)
    assert "Fully Synthetic" in ans2

    # 3. Nội soi vs Tháo taplo
    ans3, _ = AIService._fallback_engine("", "So sánh vệ sinh nội soi dàn lạnh và tháo taplo")
    assert "VỆ SINH NỘI SOI" in ans3
    assert "TAPLO" in ans3

    # 4. Láng đĩa vs Thay đĩa
    ans4, _ = AIService._fallback_engine("", "So sánh láng đĩa phanh và thay đĩa mới")
    assert "LÁNG ĐĨA PHANH" in ans4
