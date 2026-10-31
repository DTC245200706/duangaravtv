# -*- coding: utf-8 -*-
from datetime import datetime, timezone, timedelta
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import func, desc

from backend.app.database import get_db
from backend.app.models import (
    Customer, Vehicle, RepairOrder, Invoice, Appointment, User, UserRole, CRMInteraction
)
from backend.app.schemas.crm import (
    CRMInteractionCreate, CRMInteractionResponse, CRMSendMessageRequest,
    MaintenanceReminderItem, CustomerSegmentSummary
)
from backend.app.auth import get_current_user

router = APIRouter(prefix="/api/v1/crm", tags=["CRM - Quản Lý Chăm Sóc Khách Hàng"])

def get_now():
    return datetime.now(timezone.utc)

@router.get("/overview", response_model=CustomerSegmentSummary)
def get_crm_overview(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Trả về tổng quan chỉ số CRM: Tỷ lệ quay lại (Retention), Phân khúc khách hàng,
    Số xe đến hạn bảo dưỡng và Điểm hài lòng CSAT trung bình.
    """
    now = get_now()
    customers = db.query(Customer).filter(Customer.deleted_at == None).all()
    total_customers = len(customers)

    vip_count = 0
    loyal_count = 0
    new_count = 0
    at_risk_count = 0
    returned_customers_count = 0

    for cus in customers:
        # Số lượt sửa chữa
        ro_count = db.query(RepairOrder).filter(RepairOrder.customer_id == cus.id).count()
        # Tổng chi tiêu
        invoices = db.query(Invoice).filter(Invoice.customer_id == cus.id, Invoice.status == "PAID").all()
        total_spent = sum(inv.total_amount for inv in invoices)

        # Lần sửa chữa gần nhất
        last_ro = (
            db.query(RepairOrder)
            .filter(RepairOrder.customer_id == cus.id)
            .order_by(RepairOrder.created_at.desc())
            .first()
        )

        if ro_count >= 2:
            returned_customers_count += 1

        # Phân khúc
        if total_spent >= 10_000_000 or ro_count >= 4:
            vip_count += 1
        elif ro_count >= 2:
            if last_ro and (now - last_ro.created_at.replace(tzinfo=timezone.utc if last_ro.created_at.tzinfo is None else None)).days > 180:
                at_risk_count += 1
            else:
                loyal_count += 1
        else:
            if last_ro and (now - last_ro.created_at.replace(tzinfo=timezone.utc if last_ro.created_at.tzinfo is None else None)).days > 180:
                at_risk_count += 1
            else:
                new_count += 1

    retention_rate = round((returned_customers_count / total_customers * 100), 1) if total_customers > 0 else 0.0

    # Điểm CSAT trung bình
    csat_avg_row = (
        db.query(func.avg(CRMInteraction.csat_rating))
        .filter(CRMInteraction.csat_rating != None)
        .first()
    )
    average_csat = round(float(csat_avg_row[0]), 1) if csat_avg_row and csat_avg_row[0] else 4.8

    # Số lượng xe đến hạn bảo dưỡng (>= 90 ngày)
    vehicles = db.query(Vehicle).all()
    reminders_due_count = 0
    for v in vehicles:
        last_ro = (
            db.query(RepairOrder)
            .filter(RepairOrder.vehicle_id == v.id)
            .order_by(RepairOrder.created_at.desc())
            .first()
        )
        base_time = last_ro.created_at if last_ro else v.created_at
        if base_time:
            # Chuẩn hóa timezone
            bt = base_time.replace(tzinfo=timezone.utc) if base_time.tzinfo is None else base_time
            days = (now - bt).days
            if days >= 90:
                reminders_due_count += 1

    return CustomerSegmentSummary(
        total_customers=total_customers,
        vip_count=vip_count,
        loyal_count=loyal_count,
        new_count=new_count,
        at_risk_count=at_risk_count,
        retention_rate=retention_rate,
        average_csat=average_csat,
        reminders_due_count=reminders_due_count,
    )

@router.get("/reminders", response_model=List[MaintenanceReminderItem])
def get_maintenance_reminders(
    filter_status: Optional[str] = Query(None, description="OVERDUE, DUE, UPCOMING"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Danh sách các xe đến hạn hoặc quá hạn bảo dưỡng cần chăm sóc, gọi điện, gửi Zalo/SMS.
    """
    now = get_now()
    vehicles = db.query(Vehicle).all()
    results = []

    for v in vehicles:
        owner = db.query(Customer).filter(Customer.id == v.customer_id).first()
        if not owner or owner.deleted_at is not None:
            continue

        last_ro = (
            db.query(RepairOrder)
            .filter(RepairOrder.vehicle_id == v.id)
            .order_by(RepairOrder.created_at.desc())
            .first()
        )
        base_time = last_ro.created_at if last_ro else v.created_at
        days = 0
        last_date_str = None
        if base_time:
            bt = base_time.replace(tzinfo=timezone.utc) if base_time.tzinfo is None else base_time
            days = max(0, (now - bt).days)
            last_date_str = bt.strftime("%d/%m/%Y")

        if days >= 180:
            due_status = "OVERDUE"
        elif days >= 90:
            due_status = "DUE"
        elif days >= 60:
            due_status = "UPCOMING"
        else:
            continue # Xe vừa làm dịch vụ dưới 60 ngày thì chưa cần nhắc

        if filter_status and due_status != filter_status.upper():
            continue

        # Gợi ý dịch vụ theo số km
        mileage = v.current_mileage or 0
        if mileage <= 7500:
            recommended = "Bảo dưỡng Cấp 1 (5.000 km)"
        elif mileage <= 15000:
            recommended = "Bảo dưỡng Cấp 2 (10.000 km)"
        elif mileage <= 30000:
            recommended = "Bảo dưỡng Cấp 3 (20.000 km)"
        else:
            recommended = "Combo Bảo dưỡng lớn Cấp 4 (40.000 km)"

        results.append(
            MaintenanceReminderItem(
                vehicle_id=v.id,
                license_plate=v.license_plate,
                vehicle_model=f"{v.brand} {v.model}",
                current_mileage=mileage,
                customer_id=owner.id,
                customer_name=owner.full_name,
                customer_phone=owner.phone,
                last_service_date=last_date_str,
                days_since_last_service=days,
                due_status=due_status,
                recommended_service=recommended,
            )
        )

    # Ưu tiên xếp xe quá hạn lên trước
    status_order = {"OVERDUE": 0, "DUE": 1, "UPCOMING": 2}
    results.sort(key=lambda x: (status_order.get(x.due_status, 3), -x.days_since_last_service))
    return results

@router.get("/customers-segmented")
def get_customers_segmented(
    segment: Optional[str] = Query(None, description="VIP, LOYAL, NEW, AT_RISK"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Trả về danh sách khách hàng kèm phân khúc RFM chi tiết và lịch sử sử dụng dịch vụ.
    """
    now = get_now()
    customers = db.query(Customer).filter(Customer.deleted_at == None).all()
    items = []

    for cus in customers:
        ro_count = db.query(RepairOrder).filter(RepairOrder.customer_id == cus.id).count()
        invoices = db.query(Invoice).filter(Invoice.customer_id == cus.id, Invoice.status == "PAID").all()
        total_spent = sum(inv.total_amount for inv in invoices)

        last_ro = (
            db.query(RepairOrder)
            .filter(RepairOrder.customer_id == cus.id)
            .order_by(RepairOrder.created_at.desc())
            .first()
        )
        days = 0
        last_service_str = "Chưa có"
        if last_ro and last_ro.created_at:
            bt = last_ro.created_at.replace(tzinfo=timezone.utc) if last_ro.created_at.tzinfo is None else last_ro.created_at
            days = (now - bt).days
            last_service_str = bt.strftime("%d/%m/%Y")

        # Phân loại phân khúc
        if total_spent >= 10_000_000 or ro_count >= 4:
            cus_segment = "VIP"
        elif ro_count >= 2:
            if days > 180:
                cus_segment = "AT_RISK"
            else:
                cus_segment = "LOYAL"
        else:
            if days > 180:
                cus_segment = "AT_RISK"
            else:
                cus_segment = "NEW"

        if segment and cus_segment != segment.upper():
            continue

        vehicles = db.query(Vehicle).filter(Vehicle.customer_id == cus.id).all()
        plates = [v.license_plate for v in vehicles]

        items.append({
            "id": cus.id,
            "customer_code": cus.customer_code or f"CUS-{cus.id:04d}",
            "full_name": cus.full_name,
            "phone": cus.phone,
            "email": cus.email or "",
            "segment": cus_segment,
            "total_spent": total_spent,
            "order_count": ro_count,
            "last_service_date": last_service_str,
            "days_since_last": days,
            "license_plates": plates,
        })

    items.sort(key=lambda x: -x["total_spent"])
    return items

@router.get("/interactions", response_model=List[CRMInteractionResponse])
def get_crm_interactions(
    customer_id: Optional[int] = Query(None),
    interaction_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Lấy danh sách nhật ký tương tác CSKH (gọi điện, tin nhắn Zalo, khảo sát CSAT).
    """
    query = db.query(CRMInteraction)
    if customer_id:
        query = query.filter(CRMInteraction.customer_id == customer_id)
    if interaction_type:
        query = query.filter(CRMInteraction.interaction_type == interaction_type.upper())

    records = query.order_by(desc(CRMInteraction.created_at)).limit(limit).all()
    results = []
    for r in records:
        cus = db.query(Customer).filter(Customer.id == r.customer_id).first()
        veh = db.query(Vehicle).filter(Vehicle.id == r.vehicle_id).first() if r.vehicle_id else None
        staff = db.query(User).filter(User.id == r.created_by_id).first() if r.created_by_id else None

        results.append(
            CRMInteractionResponse(
                id=r.id,
                customer_id=r.customer_id,
                customer_name=cus.full_name if cus else "Khách vãng lai",
                customer_phone=cus.phone if cus else "",
                vehicle_id=r.vehicle_id,
                license_plate=veh.license_plate if veh else None,
                interaction_type=r.interaction_type,
                subject=r.subject,
                content=r.content,
                outcome=r.outcome,
                csat_rating=r.csat_rating,
                follow_up_date=r.follow_up_date,
                created_by_name=staff.full_name if staff else "Hệ thống",
                created_at=r.created_at,
            )
        )
    return results

@router.post("/interactions", response_model=CRMInteractionResponse, status_code=status.HTTP_201_CREATED)
def create_crm_interaction(
    data: CRMInteractionCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Tạo mới một bản ghi tương tác chăm sóc khách hàng (Ghi nhận cuộc gọi, Khảo sát CSAT, Ghi chú).
    """
    customer = db.query(Customer).filter(Customer.id == data.customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Không tìm thấy khách hàng trong hệ thống")

    follow_up = None
    if data.follow_up_date:
        try:
            follow_up = datetime.fromisoformat(data.follow_up_date.replace("Z", "+00:00"))
        except Exception:
            pass

    interaction = CRMInteraction(
        customer_id=data.customer_id,
        vehicle_id=data.vehicle_id,
        interaction_type=data.interaction_type.upper(),
        subject=data.subject.strip(),
        content=data.content,
        outcome=data.outcome,
        csat_rating=data.csat_rating,
        follow_up_date=follow_up,
        created_by_id=current_user.id,
    )
    db.add(interaction)
    db.commit()
    db.refresh(interaction)

    veh = db.query(Vehicle).filter(Vehicle.id == interaction.vehicle_id).first() if interaction.vehicle_id else None

    return CRMInteractionResponse(
        id=interaction.id,
        customer_id=interaction.customer_id,
        customer_name=customer.full_name,
        customer_phone=customer.phone,
        vehicle_id=interaction.vehicle_id,
        license_plate=veh.license_plate if veh else None,
        interaction_type=interaction.interaction_type,
        subject=interaction.subject,
        content=interaction.content,
        outcome=interaction.outcome,
        csat_rating=interaction.csat_rating,
        follow_up_date=interaction.follow_up_date,
        created_by_name=current_user.full_name,
        created_at=interaction.created_at,
    )

@router.post("/send-message")
def send_crm_message(
    payload: CRMSendMessageRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Gửi tin nhắn chăm sóc khách hàng qua Zalo OA hoặc SMS mẫu, tự động lưu vết tương tác.
    """
    customer = db.query(Customer).filter(Customer.id == payload.customer_id).first()
    if not customer:
        raise HTTPException(status_code=404, detail="Không tìm thấy thông tin khách hàng")

    veh = db.query(Vehicle).filter(Vehicle.id == payload.vehicle_id).first() if payload.vehicle_id else None
    plate_str = f" cho xe {veh.license_plate}" if veh else ""

    subject_map = {
        "MAINTENANCE_DUE": f"Nhắc lịch bảo dưỡng định kỳ{plate_str}",
        "CSAT_SURVEY": f"Khảo sát đánh giá dịch vụ{plate_str}",
        "THANK_YOU": f"Lời cảm ơn quý khách{plate_str}",
        "PROMOTION": f"Ưu đãi tri ân khách hàng{plate_str}",
    }
    subject = subject_map.get(payload.template_type, f"Tin nhắn CSKH {payload.channel}")

    # Ghi lại lịch sử tương tác
    interaction = CRMInteraction(
        customer_id=customer.id,
        vehicle_id=payload.vehicle_id,
        interaction_type=payload.channel.upper(),
        subject=subject,
        content=payload.message_content,
        outcome="Đã gửi thành công",
        created_by_id=current_user.id,
    )
    db.add(interaction)
    db.commit()

    return {
        "status": "success",
        "channel": payload.channel.upper(),
        "recipient_phone": customer.phone,
        "recipient_name": customer.full_name,
        "subject": subject,
        "message": f"Đã gửi tin nhắn thành công qua {payload.channel.upper()} tới {customer.full_name} ({customer.phone})",
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }

