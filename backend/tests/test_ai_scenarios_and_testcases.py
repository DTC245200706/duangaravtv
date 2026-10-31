# -*- coding: utf-8 -*-
import pytest
from backend.app.ai.service import AIService
from backend.app.database import SessionLocal
from backend.app.ai.admin_copilot import ask_admin_copilot

def test_customer_ai_testcases_routing():
    # 1. Tổng hợp testcase khách hàng
    ans, _ = AIService._fallback_engine("", "Tổng hợp các testcase kiểm thử cho khách hàng")
    assert "TỔNG HỢP CÁC KỊCH BẢN" in ans
    assert "TC-CUS-01.1" in ans
    assert "TC-CUS-02.1" in ans
    assert "TC-CUS-03.1" in ans

    # 2. Gọi mã TC-CUS-01.1 (Phanh)
    ans1, _ = AIService._fallback_engine("", "Cho tôi hỏi mã TC-CUS-01.1")
    assert "phanh" in ans1.lower() or "đĩa phanh" in ans1.lower()

    # 3. Gọi mã TC-CUS-01.2 (Hụt ga)
    ans2, _ = AIService._fallback_engine("", "Kiểm tra testcase TC-CUS-01.2")
    assert "hụt ga" in ans2.lower() or "đánh lửa" in ans2.lower() or "buồng đốt" in ans2.lower()

    # 4. Gọi mã TC-CUS-02.1 (Chi phí)
    ans3, _ = AIService._fallback_engine("", "Xem kết quả TC-CUS-02.1")
    assert "BẢNG DỰ TOÁN CHI PHÍ" in ans3 or "Gói Cấp 1" in ans3

    # 5. Gọi mã TC-CUS-03.1 (So sánh Cấp 1 vs 2)
    ans4, _ = AIService._fallback_engine("", "Chạy kịch bản TC-CUS-03.1")
    assert "SO SÁNH BẢO DƯỠNG CẤP 1" in ans4 or "Tiêu chí" in ans4

def test_admin_ai_testcases_routing():
    db = SessionLocal()
    try:
        # 1. Tổng hợp testcase quản trị
        res_summary = ask_admin_copilot(db, "Tổng hợp testcase cho AI Quản trị")
        assert res_summary["success"] is True
        assert "TỔNG HỢP CÁC KỊCH BẢN" in res_summary["output"]
        assert "TC-ADM-01.1" in res_summary["output"]
        assert "TC-ADM-04.1" in res_summary["output"]

        # 2. Gọi mã TC-ADM-01.1 (Tiếp nhận xe)
        res_ro = ask_admin_copilot(db, "Kiểm tra TC-ADM-01.1")
        assert "TIẾP NHẬN & PHÂN TÍCH CHẨN ĐOÁN XE" in res_ro["output"] or "xe" in res_ro["output"].lower()

        # 3. Gọi mã TC-ADM-02.1 (Kho phụ tùng)
        res_inv = ask_admin_copilot(db, "Chạy kịch bản TC-ADM-02.1")
        assert "TỒN KHO" in res_inv["output"] or "SKU" in res_inv["output"]

        # 4. Gọi mã TC-ADM-03.1 (Tài chính)
        res_fin = ask_admin_copilot(db, "Kịch bản TC-ADM-03.1")
        assert "TÀI CHÍNH" in res_fin["output"] or "Doanh thu" in res_fin["output"]

        # 5. Gọi mã TC-ADM-04.1 (CRM)
        res_crm = ask_admin_copilot(db, "Test case TC-ADM-04.1")
        assert "CRM" in res_crm["output"] or "QUẢN TRỊ QUAN HỆ KHÁCH HÀNG" in res_crm["output"]
    finally:
        db.close()

