# -*- coding: utf-8 -*-
import pytest
from fastapi.testclient import TestClient
from backend.app.main import app

client = TestClient(app)

def get_auth_token():
    login_res = client.post(
        "/api/v1/auth/login",
        data={"username": "admin", "password": "adminpassword"}
    )
    if login_res.status_code == 200:
        return login_res.json().get("access_token")
    # Thử tài khoản khác nếu có
    return None

def test_crm_endpoints():
    token = get_auth_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    # 1. CRM Overview
    res = client.get("/api/v1/crm/overview", headers=headers)
    assert res.status_code in [200, 401]
    if res.status_code == 200:
        data = res.json()
        assert "retention_rate" in data
        assert "total_customers" in data
        assert "vip_count" in data
        assert "reminders_due_count" in data
        assert "average_csat" in data

    # 2. CRM Reminders
    res_reminders = client.get("/api/v1/crm/reminders", headers=headers)
    assert res_reminders.status_code in [200, 401]
    if res_reminders.status_code == 200:
        reminders = res_reminders.json()
        assert isinstance(reminders, list)

    # 3. CRM Segmented Customers
    res_seg = client.get("/api/v1/crm/customers-segmented", headers=headers)
    assert res_seg.status_code in [200, 401]
    if res_seg.status_code == 200:
        seg_list = res_seg.json()
        assert isinstance(seg_list, list)

    # 4. CRM Interactions List
    res_inter = client.get("/api/v1/crm/interactions", headers=headers)
    assert res_inter.status_code in [200, 401]
    if res_inter.status_code == 200:
        inter_list = res_inter.json()
        assert isinstance(inter_list, list)



def test_admin_copilot_crm_intelligence():
    from backend.app.database import SessionLocal
    from backend.app.ai.admin_copilot import ask_admin_copilot
    
    db = SessionLocal()
    try:
        res = ask_admin_copilot(db, "Báo cáo cho tôi tình hình CRM và nhắc bảo dưỡng định kỳ xe")
        assert res["success"] is True
        assert "live_metrics" in res
        assert "crm_due_reminders" in res["live_metrics"]
        assert "QUẢN TRỊ QUAN HỆ KHÁCH HÀNG" in res["output"] or "CRM" in res["output"]
    finally:
        db.close()


def test_crm_vehicle_history_carsoft():
    token = get_auth_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    # Gọi với vehicle_id = 1
    res = client.get("/api/v1/crm/vehicle-history/1", headers=headers)
    assert res.status_code in [200, 401, 404]
    if res.status_code == 200:
        data = res.json()
        assert "vehicle" in data
        assert "stats" in data
        assert "timeline" in data
        assert "license_plate" in data["vehicle"]
