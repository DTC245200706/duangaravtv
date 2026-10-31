import logging
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session
from sqlalchemy import func

from backend.app.models import (
    RepairOrder, RepairOrderStatus, RepairOrderItem,
    Part, Appointment, CustomerRequest, User, UserRole,
    Invoice, InvoiceStatus, Customer, Vehicle
)
from backend.app.ai.service import AIService
from backend.app.config import settings

logger = logging.getLogger("admin_copilot")

SYSTEM_ADMIN_COPILOT = """Bạn là VTV AutoPilot — Trợ Lý AI Điều Hành & Cố Vấn Tác Nghiệp Chuyên Sâu của Hệ thống Garage Ô tô VTV Engine Pro.
Bạn đang phục vụ Ban Giám Đốc, Trưởng Xưởng, Kỹ Thuật Viên, Lễ Tân và Thu Ngân trong khu vực Quản Trị (Admin Dashboard).

Bạn có QUYỀN TRUY CẬP VÀO DỮ LIỆU THỜI GIAN THỰC CỦA TOÀN BỘ HỆ THỐNG WEBSITE & DATABASE:
1. TIẾP NHẬN & CHẨN ĐOÁN LỖI XE: Nắm rõ các phiếu sửa chữa đang chạy, triệu chứng, mã lỗi, lịch sử xe, phác đồ xử lý kỹ thuật.
2. TỐI ƯU KHO PHỤ TÙNG: Nắm rõ danh mục linh kiện, tồn kho an toàn, các mặt hàng dưới ngưỡng báo động (low-stock), đề xuất đặt hàng.
3. LỊCH HẸN & CHĂM SÓC KHÁCH HÀNG: Nắm rõ lịch hẹn hôm nay, các yêu cầu trực tuyến mới từ khách, thói quen bảo dưỡng, chăm sóc khách VIP.
4. ĐIỀU PHỐI CÔNG VIỆC & NHÂN SỰ: Nắm rõ danh sách Kỹ thuật viên, số lượng xe đang phụ trách, tải việc từng khoang/cầu nâng, đề xuất phân bổ tối ưu.
5. TÀI CHÍNH & CHỐNG THẤT THOÁT: Nắm rõ doanh thu thực tế, hóa đơn đã thu (PAID), nợ đọng (UNPAID), tỷ trọng tiền công/phụ tùng, kiểm soát rủi ro thất thoát.

PHONG CÁCH TRẢ LỜI:
- Chuyên nghiệp, trực diện, chuẩn tác phong quản trị công nghiệp ô tô cao cấp.
- Cung cấp số liệu cụ thể, tên xe, biển số, mã phụ tùng, số tiền VNĐ từ dữ liệu thực tế được đính kèm.
- Luôn kèm theo "Khuyến nghị hành động (Actionable Insights)" cho nhân sự xưởng.
- Sử dụng định dạng Markdown rõ ràng, bullet points, icon kỹ thuật phù hợp.
"""

def extract_live_garage_context(db: Session, question: str, domain: Optional[str] = None) -> Dict[str, Any]:
    """Trích xuất dữ liệu vận hành thời gian thực từ Database SQLite/Postgres."""
    q_lower = (question or "").lower()
    
    # 1. Tiếp nhận & Xe đang sửa
    active_ros = db.query(RepairOrder).filter(
        RepairOrder.status.in_([
            RepairOrderStatus.RECEIVED,
            RepairOrderStatus.INSPECTING,
            RepairOrderStatus.QUOTATION_PENDING,
            RepairOrderStatus.WAITING_CUSTOMER_APPROVAL,
            RepairOrderStatus.APPROVED,
            RepairOrderStatus.IN_REPAIR,
            RepairOrderStatus.WAITING_PARTS
        ])
    ).all()

    ros_data = []
    for ro in active_ros:
        veh = ro.vehicle
        plate = veh.license_plate if veh else "N/A"
        brand_model = f"{veh.brand} {veh.model}" if veh else "N/A"
        ros_data.append({
            "code": ro.code or f"RO-{ro.id}",
            "plate": plate,
            "vehicle": brand_model,
            "status": ro.status.value if hasattr(ro.status, "value") else str(ro.status),
            "symptoms": ro.initial_symptoms or "Đang kiểm tra",
            "diagnosis": ro.technical_diagnosis or "Chưa chẩn đoán",
            "cost": ro.final_cost or ro.estimated_cost or 0
        })

    # 2. Kho phụ tùng & Cảnh báo tồn
    all_parts = db.query(Part).all()
    low_stock = [p for p in all_parts if p.stock_quantity <= p.min_stock_alert]
    total_qty = sum(p.stock_quantity for p in all_parts)
    total_val = sum((p.stock_quantity or 0) * (p.unit_price or 0) for p in all_parts)
    parts_items = [
        {
            "code": p.code,
            "name": p.name,
            "stock": p.stock_quantity,
            "min": p.min_stock_alert,
            "selling_price": p.unit_price or 0,
            "is_low": p.stock_quantity <= p.min_stock_alert
        }
        for p in all_parts
    ]
    parts_summary = {
        "total_sku": len(all_parts),
        "total_quantity": total_qty,
        "total_value": total_val,
        "low_stock_count": len(low_stock),
        "low_stock_items": [p for p in parts_items if p["is_low"]],
        "in_stock_items": [p for p in parts_items if not p["is_low"]],
        "all_parts": parts_items
    }

    # 3. Lịch hẹn & Yêu cầu trực tuyến
    pending_requests = db.query(CustomerRequest).filter(
        CustomerRequest.status.in_(["Pending", "Contacted"])
    ).order_by(CustomerRequest.created_at.desc()).all()

    all_appointments = db.query(Appointment).all()
    pending_appointments = [a for a in all_appointments if a.status == "pending"]

    # 4. Nhân sự & Điều phối
    technicians = db.query(User).filter(User.role == UserRole.TECHNICIAN).all()
    tech_workload = []
    for tech in technicians:
        assigned_count = db.query(RepairOrder).filter(
            RepairOrder.technician_id == tech.id,
            RepairOrder.status.in_([RepairOrderStatus.IN_REPAIR, RepairOrderStatus.INSPECTING])
        ).count()
        tech_workload.append({
            "id": tech.id,
            "name": tech.full_name,
            "active_orders": assigned_count
        })

    # 5. Tài chính & Chống thất thoát
    all_invoices = db.query(Invoice).all()
    paid_total = sum(inv.paid_amount or 0 for inv in all_invoices if inv.status == InvoiceStatus.PAID)
    unpaid_total = sum(inv.total_amount or 0 for inv in all_invoices if inv.status == InvoiceStatus.UNPAID)
    unpaid_count = len([inv for inv in all_invoices if inv.status == InvoiceStatus.UNPAID])

    return {
        "active_repair_orders": ros_data,
        "parts": parts_summary,
        "customer_requests": [
            {
                "code": req.request_code or f"REQ-{req.id}",
                "name": req.full_name,
                "phone": req.phone,
                "plate": req.license_plate,
                "model": req.vehicle_model,
                "service": req.service_type or "Bảo dưỡng",
                "status": req.status
            }
            for req in pending_requests[:6]
        ],
        "appointments": {
            "total": len(all_appointments),
            "pending": len(pending_appointments)
        },
        "technicians": tech_workload,
        "finances": {
            "paid_revenue": paid_total,
            "unpaid_total": unpaid_total,
            "unpaid_invoices_count": unpaid_count,
            "total_invoices": len(all_invoices)
        }
    }

def format_admin_context_string(context: Dict[str, Any], domain: Optional[str] = None) -> str:
    """Đóng gói dữ liệu thời gian thực thành prompt context có cấu trúc chuẩn."""
    lines = ["=== DỮ LIỆU THỜI GIAN THỰC TỪ HỆ THỐNG VẬN HÀNH GARAGE VTV ==="]

    # Domain 1: Tiếp nhận & Chẩn đoán xe
    lines.append("\n[1. TIẾP NHẬN & PHIẾU SỬA CHỮA ĐANG HOẠT ĐỘNG]")
    ros = context.get("active_repair_orders", [])
    if ros:
        lines.append(f"- Tổng số xe đang sửa/chẩn đoán: {len(ros)} xe.")
        for r in ros[:5]:
            lines.append(f"  • Mã {r['code']} | Xe: {r['plate']} ({r['vehicle']}) | Trạng thái: {r['status']} | Triệu chứng: {r['symptoms']} | Chi phí: {r['cost']:,.0f} VNĐ")
    else:
        lines.append("- Hiện không có xe nào đang chờ sửa chữa.")

    # Domain 2: Kho phụ tùng
    parts = context.get("parts", {})
    lines.append(f"\n[2. TỒN KHO PHỤ TÙNG & VẬT TƯ THỜI GIAN THỰC]")
    lines.append(f"- Tổng quy mô danh mục: {parts.get('total_sku', 0)} SKU | Tổng số lượng trong kho: {parts.get('total_quantity', 0):,} cái.")
    lines.append(f"- Tổng giá trị tồn kho ước tính: {parts.get('total_value', 0):,.0f} VNĐ.")
    lines.append(f"- Số mặt hàng chạm/dưới ngưỡng an toàn: {parts.get('low_stock_count', 0)} món.")
    for p in parts.get("low_stock_items", []):
        lines.append(f"  ⚠️ CẢNH BÁO THIẾU: {p['name']} ({p['code']}) - Còn: {p['stock']} (Tối thiểu: {p['min']}) - Giá: {p['selling_price']:,.0f} VNĐ")
    lines.append(f"- Danh mục phụ tùng đang sẵn sàng phục vụ:")
    for p in parts.get("all_parts", [])[:25]:
        status_tag = 'Cảnh báo thiếu' if p.get('is_low') else 'Đủ tồn'
        lines.append(f"  • {p['name']} ({p['code']}) | Tồn: {p['stock']} | Đơn giá: {p['selling_price']:,.0f} VNĐ | [{status_tag}]")

    # Domain 3: Lịch hẹn & Yêu cầu khách hàng
    reqs = context.get("customer_requests", [])
    lines.append(f"\n[3. LỊCH HẸN & YÊU CẦU ĐẶT LỊCH TRỰC TUYẾN]")
    lines.append(f"- Số yêu cầu trực tuyến đang chờ xử lý: {len(reqs)} yêu cầu.")
    for req in reqs:
        lines.append(f"  • {req['code']}: Khách {req['name']} ({req['phone']}) | Xe {req['plate']} ({req['model']}) | Dịch vụ: {req['service']} | Trạng thái: {req['status']}")

    # Domain 4: Điều phối nhân sự
    techs = context.get("technicians", [])
    lines.append(f"\n[4. ĐIỀU PHỐI NHÂN SỰ & KỸ THUẬT VIÊN]")
    if techs:
        for t in techs:
            lines.append(f"  • KTV {t['name']}: Đang phụ trách {t['active_orders']} xe.")
    else:
        lines.append("  • Chưa phân bổ kỹ thuật viên cụ thể.")

    # Domain 5: Tài chính & Chống thất thoát
    fin = context.get("finances", {})
    lines.append(f"\n[5. TÀI CHÍNH & KIỂM SOÁT THẤT THOÁT]")
    lines.append(f"- Doanh thu thực thu (Đã thanh toán): {fin.get('paid_revenue', 0):,.0f} VNĐ.")
    lines.append(f"- Công nợ tồn đọng (Chưa thanh toán): {fin.get('unpaid_total', 0):,.0f} VNĐ ({fin.get('unpaid_invoices_count', 0)} hóa đơn).")
    lines.append(f"- Tổng số hóa đơn phát sinh: {fin.get('total_invoices', 0)} hóa đơn.")

    return "\n".join(lines)

def run_admin_copilot_deterministic_fallback(question: str, context: Dict[str, Any], domain: Optional[str] = None) -> str:
    """Engine phân tích nội bộ chuyên sâu khi không có mạng ngoại vi hoặc chạy offline."""
    q_lower = (question or "").lower()
    
    # 1. Domain: Chẩn đoán & Tiếp nhận lỗi xe
    if any(k in q_lower for k in ["chẩn đoán", "chan doan", "lỗi xe", "loi xe", "sửa chữa", "triệu chứng", "phiếu", "ro-", "tiếp nhận", "macan", "porsche", "mercedes", "taplo"]):
        ros = context.get("active_repair_orders", [])
        ro_lines = "\n".join([f"  • **{r['code']}** — Xe **{r['plate']}** ({r['vehicle']}): {r['symptoms']} *(Trạng thái: {r['status']})*" for r in ros[:4]]) or "  • Hiện không có xe đang sửa."
        return f"""### 🚗 BÁO CÁO TIẾP NHẬN & PHÂN TÍCH CHẨN ĐOÁN XE

**1. Hiện trạng xưởng tiếp nhận:**
- Toàn xưởng đang xử lý **{len(ros)} xe** trên các khoang chẩn đoán và sửa chữa.
{ro_lines}

**2. Phác đồ kiểm tra kỹ thuật đề xuất:**
- **Bước 1 (OBD-II & Sensor Data):** Cắm máy quét chuyên dụng (Launch X431 / Autel MaxiSys) quét toàn bộ mã lỗi Freeze Frame & Live Data trên ECM/TCM.
- **Bước 2 (Kiểm tra gầm & truyền lực):** Đưa xe lên cầu 2 trụ kiểm tra độ rơ rotuyn lái, càng A, cao su chân máy và độ dày má phanh.
- **Bước 3 (Hệ thống bôi trơn & nhiên liệu):** Kiểm tra áp suất nén buồng đốt, mức dầu nhớt động cơ và lọc gió động cơ.

**💡 Khuyến nghị cho KTV:** Sau khi xác định chính xác nguyên nhân, cập nhật ngay chẩn đoán vào Phiếu Sửa Chữa để Lễ Tân xuất báo giá minh bạch cho khách hàng."""

    # 2. Domain: Tối ưu kho phụ tùng & Tra cứu hàng tồn kho
    inventory_keywords = [
        "kho", "phụ tùng", "phu tung", "linh kiện", "linh kien", "tồn kho", "ton kho",
        "hàng tồn", "hang ton", "nhập hàng", "nhap hang", "hết hàng", "het hang",
        "cảnh báo", "vật tư", "vat tu", "stock", "lọc", "nhớt", "dầu", "phanh",
        "bugi", "ắc quy", "ac quy", "bình điện", "gạt mưa", "lốp", "tồn"
    ]
    if any(k in q_lower for k in inventory_keywords):
        parts = context.get("parts", {})
        low_items = parts.get("low_stock_items", [])
        all_items = parts.get("all_parts", [])
        total_sku = parts.get("total_sku", 0)
        total_qty = parts.get("total_quantity", 0)
        total_val = parts.get("total_value", 0)
        low_count = parts.get("low_stock_count", 0)

        # Tìm các mặt hàng khớp từ khóa chi tiết nếu có
        matched_items = [p for p in all_items if any(k in p['name'].lower() or k in p['code'].lower() for k in ["lọc", "nhớt", "dầu", "phanh", "bugi", "ắc quy", "lốp", "gạt mưa"] if k in q_lower)]
        display_items = matched_items if matched_items else all_items[:12]

        items_table_rows = "\n".join([
            f"  • `{p['code']}` — **{p['name']}**: Còn **{p['stock']}** cái | Đơn giá: **{p['selling_price']:,.0f} ₫** | *(Tồn an toàn: {p['min']})* {' ⚠️ *[Cần nhập thêm]*' if p.get('is_low') else ' ✅ *[Đủ hàng]*'}"
            for p in display_items
        ])

        low_str = "\n".join([
            f"  ⚠️ **{p['name']}** (`{p['code']}`): Còn **{p['stock']}** cái *(Tồn an toàn: {p['min']})* — Đơn giá: {p['selling_price']:,.0f} ₫"
            for p in low_items
        ]) if low_items else "  ✅ Hiện tại không có phụ tùng nào dưới ngưỡng báo động an toàn."

        return f"""### 📦 BÁO CÁO TOÀN DIỆN HÀNG TỒN KHO & ĐỀ XUẤT NHẬP HÀNG

**1. Tổng quan tình trạng tồn kho thời gian thực:**
- Quy mô danh mục quản lý: **{total_sku} mã linh kiện (SKU)**.
- Tổng số lượng vật tư sẵn sàng: **{total_qty:,} sản phẩm**.
- Tổng giá trị kho hàng ước tính: **{total_val:,.0f} VNĐ**.
- Số mặt hàng **chạm ngưỡng cảnh báo thiếu (Low-stock)**: **{low_count} mã**.

**2. Danh sách phụ tùng & số lượng tồn kho thực tế:**
{items_table_rows}

**3. Danh mục phụ tùng cần lập đơn nhập bổ sung ngay:**
{low_str}

**4. Khuyến nghị tác nghiệp điều hành kho:**
- **Đặt hàng ưu tiên:** Nhập bổ sung ngay các mặt hàng có gắn nhãn ⚠️ để phục vụ bảo dưỡng định kỳ không bị gián đoạn.
- **Quản lý xuất nhập:** Bắt buộc 100% xuất kho phải liên kết với mã Phiếu Sửa Chữa (RO) trên hệ thống để chống thất thoát.
- **Kiểm kê định kỳ:** Tiến hành đối soát thực tế (Physical inventory count) vào cuối mỗi tuần."""

    # 3. Domain: Lịch hẹn & Khách hàng
    if any(k in q_lower for k in ["lịch hẹn", "lich hen", "khách hàng", "khach hang", "yêu cầu", "đặt lịch", "booking", "online", "chăm sóc"]):
        reqs = context.get("customer_requests", [])
        req_lines = "\n".join([f"  • **{r['code']}** — Khách **{r['name']}** (`{r['phone']}`): Xe **{r['plate']}** ({r['model']}) — Yêu cầu: *{r['service']}*" for r in reqs[:4]]) or "  • Hiện không có yêu cầu tồn đọng."
        
        return f"""### 📅 BÁO CÁO LỊCH HẸN & CHĂM SÓC KHÁCH HÀNG

**1. Tình trạng tiếp nhận đặt lịch trực tuyến:**
- Số yêu cầu đặt lịch mới từ Website/Chatbot chưa xác nhận: **{len(reqs)} yêu cầu**.
{req_lines}

**2. Kế hoạch tiếp nhận & chăm sóc hôm nay:**
- Toàn bộ yêu cầu online cần được Lễ tân gọi điện xác nhận trong vòng **5 phút** kể từ khi phát sinh.
- Kiểm tra sẵn sàng khoang tiếp nhận và bộ dụng cụ chuyên dụng trước giờ hẹn 15 phút.

**💡 Hành động đề xuất:**
- Nhắc hẹn tự động qua Zalo OA trước 2 giờ cho khách hàng có lịch trong ngày.
- Ghi nhận đặc thù thói quen lái xe và lịch sử bảo dưỡng của khách VIP vào hồ sơ khách hàng."""

    # 4. Domain: Điều phối công việc & Nhân sự
    if any(k in q_lower for k in ["nhân sự", "nhan su", "điều phối", "dieu phoi", "kỹ thuật viên", "ktv", "thợ", "công việc", "năng suất"]):
        techs = context.get("technicians", [])
        tech_lines = "\n".join([f"  • KTV **{t['name']}**: Đang thực hiện **{t['active_orders']} lệnh sửa chữa**" for t in techs]) or "  • Đội ngũ kỹ thuật viên đang túc trực."
        
        return f"""### 👥 BÁO CÁO ĐIỀU PHỐI CÔNG VIỆC & NĂNG SUẤT KỸ THUẬT

**1. Hiện trạng phân bổ công việc xưởng:**
{tech_lines}

**2. Ma trận phân công tối ưu:**
- **Nhóm Máy - Gầm:** Tập trung xử lý các xe Porsche, Mercedes và SUV có đại tu hệ thống treo và hộp số.
- **Nhóm Bảo Dưỡng Nhanh (Express Service):** Đảm bảo hoàn thành các gói 10.000 km trong tối đa 60 phút/xe.
- **Nhóm Điện & Điện Lạnh:** Phụ trách bắt bệnh các ca báo đèn taplo và điều hòa không mát.

**💡 Giải pháp nâng cao hiệu suất:**
- Trưởng xưởng chủ động rà soát tiến độ vào các mốc 10:30 sáng và 15:30 chiều để luân chuyển thợ hỗ trợ các ca khó."""

    # 5. Domain: Tài chính & Chống thất thoát
    if any(k in q_lower for k in ["tài chính", "tai chinh", "doanh thu", "thất thoát", "that thoat", "tiền", "hóa đơn", "công nợ", "lợi nhuận"]):
        fin = context.get("finances", {})
        paid = fin.get("paid_revenue", 0)
        unpaid = fin.get("unpaid_total", 0)
        unpaid_count = fin.get("unpaid_invoices_count", 0)
        
        return f"""### 💰 BÁO CÁO TÀI CHÍNH & KIỂM SOÁT THẤT THOÁT DOANH THU

**1. Chỉ số tài chính thực tế:**
- **Doanh thu thực thu (Đã thanh toán):** **{paid:,.0f} VNĐ**
- **Công nợ tồn đọng (Chưa thanh toán):** **{unpaid:,.0f} VNĐ** *(Gồm {unpaid_count} hóa đơn)*
- **Tỷ lệ thu hồi dòng tiền:** **{((paid / (paid + unpaid)) * 100) if (paid + unpaid) > 0 else 100:.1f}%**

**2. Các điểm rủi ro cần kiểm soát chống thất thoát:**
- **Khâu xuất kho linh kiện:** Bắt buộc 100% phụ tùng xuất kho phải có mã Phiếu Sửa Chữa (RO) hợp lệ; không xuất kho thủ công ngoài sổ sách.
- **Kiểm soát đơn giá & công thợ:** Đối chiếu định mức giờ công tiêu chuẩn (Flat-rate) tránh sai lệch giữa báo giá và quyết toán.
- **Xử lý công nợ:** Thu ngân liên hệ đối soát các hóa đơn quá hạn trên 48 giờ trước khi bàn giao xe hoặc hoàn tất dịch vụ."""

    # Mặc định: Báo cáo vận hành tổng thể
    ros = context.get("active_repair_orders", [])
    parts = context.get("parts", {})
    reqs = context.get("customer_requests", [])
    fin = context.get("finances", {})
    
    return f"""### 🛡️ TỔNG HỢP VẬN HÀNH GARAGE VTV (EXECUTIVE COPILOT)

Tôi là **VTV AutoPilot** — Trợ lý Quản Trị Hệ Thống. Dưới đây là bức tranh vận hành tổng thể thời gian thực:

1. 🚗 **Xe Đang Sửa:** Đang xử lý **{len(ros)} xe** trong xưởng tiếp nhận.
2. 📦 **Kho Phụ Tùng:** Quản lý **{parts.get('total_sku', 0)} SKU**, có **{parts.get('low_stock_count', 0)} linh kiện** chạm ngưỡng cảnh báo.
3. 📅 **Khách & Lịch Hẹn:** **{len(reqs)} yêu cầu online** đang chờ lễ tân liên hệ xác nhận.
4. 👥 **Nhân Lực Kỹ Thuật:** Toàn bộ kỹ thuật viên đã được bố trí vị trí trực khoang.
5. 💰 **Tài Chính:** Đã thu **{fin.get('paid_revenue', 0):,.0f} VNĐ**, công nợ cần thu hồi: **{fin.get('unpaid_total', 0):,.0f} VNĐ**.

*Bạn có thể bấm các nút nghiệp vụ chuyên sâu bên dưới hoặc đặt câu hỏi chi tiết về bất kỳ mã phiếu, linh kiện, nhân viên hay khoản tài chính nào.*"""

def ask_admin_copilot(db: Session, question: str, role: str = "manager", domain: Optional[str] = None) -> Dict[str, Any]:
    """Hàm trung tâm phục vụ AI Quản Trị Hệ Thống."""
    context = extract_live_garage_context(db, question, domain)
    context_str = format_admin_context_string(context, domain)
    
    user_prompt = f"""CÂU HỎI TỪ NHÂN SỰ VẬN HÀNH ({role.upper()}):
{question}

{context_str}

Hãy phân tích và đưa ra giải đáp tác nghiệp toàn diện, chính xác với các số liệu thực tế phía trên."""

    # Thử gọi LLM Agent với Prompt chuyên sâu nếu có cấu hình API Key
    has_api_key = bool(
        getattr(settings, "GEMINI_API_KEY", "") or
        getattr(settings, "GROQ_API_KEY", "") or
        getattr(settings, "DEEPSEEK_API_KEY", "")
    )
    
    output_text = None
    model_used = "VTV Operational Engine (Live DB)"
    
    if has_api_key:
        try:
            output_text, model_used = AIService._call_llm_agent(db, SYSTEM_ADMIN_COPILOT, user_prompt)
        except Exception as e:
            logger.warning("LLM API failed for Admin Copilot, falling back to internal engine: %s", e)
            output_text = None

    if not output_text or len(output_text.strip()) < 20 or model_used == "Internal Fallback":
        output_text = run_admin_copilot_deterministic_fallback(question, context, domain)
        model_used = "VTV AutoPilot Realtime Core"

    return {
        "success": True,
        "feature": "admin_copilot",
        "domain": domain or "general",
        "output": output_text,
        "model_used": model_used,
        "live_metrics": {
            "active_cars": len(context.get("active_repair_orders", [])),
            "low_stock_count": context.get("parts", {}).get("low_stock_count", 0),
            "pending_requests": len(context.get("customer_requests", [])),
            "total_revenue": context.get("finances", {}).get("paid_revenue", 0),
            "unpaid_total": context.get("finances", {}).get("unpaid_total", 0)
        }
    }
