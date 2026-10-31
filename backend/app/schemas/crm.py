# -*- coding: utf-8 -*-
from typing import Optional, List, Dict, Any
from datetime import datetime
from pydantic import BaseModel, Field, ConfigDict

class CRMInteractionCreate(BaseModel):
    customer_id: int
    vehicle_id: Optional[int] = None
    interaction_type: str = Field(default="CALL", description="CALL, SMS, ZALO, VISIT, SURVEY")
    subject: str = Field(..., max_length=150)
    content: Optional[str] = None
    outcome: Optional[str] = Field(None, description="Đã đặt hẹn, Quan tâm, Chưa liên hệ được, Từ chối")
    csat_rating: Optional[int] = Field(None, ge=1, le=5, description="Đánh giá CSAT 1 - 5 sao")
    follow_up_date: Optional[str] = None

class CRMInteractionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    vehicle_id: Optional[int] = None
    license_plate: Optional[str] = None
    interaction_type: str
    subject: str
    content: Optional[str] = None
    outcome: Optional[str] = None
    csat_rating: Optional[int] = None
    follow_up_date: Optional[datetime] = None
    created_by_name: Optional[str] = None
    created_at: datetime

class CRMSendMessageRequest(BaseModel):
    customer_id: int
    vehicle_id: Optional[int] = None
    channel: str = Field(default="ZALO", description="ZALO or SMS")
    template_type: str = Field(default="MAINTENANCE_DUE", description="MAINTENANCE_DUE, CSAT_SURVEY, THANK_YOU, PROMOTION")
    message_content: str

class MaintenanceReminderItem(BaseModel):
    vehicle_id: int
    license_plate: str
    vehicle_model: str
    current_mileage: int
    customer_id: int
    customer_name: str
    customer_phone: str
    last_service_date: Optional[str] = None
    days_since_last_service: int
    due_status: str # OVERDUE, DUE, UPCOMING
    recommended_service: str

class CustomerSegmentSummary(BaseModel):
    total_customers: int
    vip_count: int
    loyal_count: int
    new_count: int
    at_risk_count: int
    retention_rate: float
    average_csat: float
    reminders_due_count: int
