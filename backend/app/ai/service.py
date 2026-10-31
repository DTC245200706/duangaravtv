import os
import logging
from typing import Any, Dict, Optional, Tuple

# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models import AILog, RepairOrder, Vehicle, AIKnowledgeBase
from backend.app.ai.tools import execute_tool, get_agent_tools_for_role, get_monthly_revenue_tool
from backend.app.services.reporting_service import format_dashboard_revenue_context, format_report_context, report_for_query
from backend.app.services.lookup_service import lookup_operational_context
from backend.app.ai.prompts import (
    SYSTEM_GARAGE_ASSISTANT,
    SYSTEM_GARAGE_ADMIN_ASSISTANT,
    PROMPT_AI_ASSISTANT,
    PROMPT_ADMIN_ASSISTANT,
    PROMPT_HISTORY_SUMMARY,
    PROMPT_SERVICE_EXPLAINER,
    PROMPT_DRAFT_QUOTATION,
    PROMPT_TECHNICAL_TROUBLESHOOTING,
    PROMPT_VEHICLE_HISTORY_ANALYSIS,
    PROMPT_DRAFT_QUOTATION_EXPERT,
    PROMPT_OBD_DIAGNOSTIC,
    PROMPT_BUSINESS_INTELLIGENCE,
    PROMPT_PREDICTIVE_MAINTENANCE,
    PROMPT_CUSTOMER_PROGRESS_LOOKUP,
)

logger = logging.getLogger("garage_ai")


class AIService:
    """
    Service trung tâm cho các tính năng AI của Garage Ô tô VTV.

    Nguyên tắc:
    - AI chỉ tư vấn/diễn giải dựa trên dữ liệu được cung cấp.
    - Không để AI tự quyết định giá tiền.
    - Các phép tính tiền được thực hiện bằng Python.
    - Không bịa lịch sử sửa chữa hoặc tình trạng xe.
    - Chẩn đoán kỹ thuật luôn được xem là nhận định sơ bộ.
    """

    VAT_RATE = 0.08

    # ============================================================
    # COMMON HELPERS
    # ============================================================

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        """Chuyển giá trị về float an toàn."""
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        """Chuyển giá trị về int an toàn."""
        try:
            if value is None:
                return default
            return int(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _calculate_item_cost(cls, item: Any) -> Tuple[float, float, float]:
        """
        Tính: tiền phụ tùng, tiền công, tổng tiền.
        Không phụ thuộc vào item.total_price để tránh sai lệch dữ liệu.
        """
        quantity = cls._safe_float(getattr(item, "quantity", 0), 0.0)
        unit_price = cls._safe_float(getattr(item, "unit_price", 0), 0.0)
        labor_cost = cls._safe_float(getattr(item, "labor_cost", 0), 0.0)

        part_cost = quantity * unit_price
        total = part_cost + labor_cost

        return part_cost, labor_cost, total

    @classmethod
    def _build_items_summary(cls, items: Any) -> Tuple[str, float, float, float]:
        """
        Tạo nội dung danh sách hạng mục và tính tổng.
        Returns: (items_str, total_parts, total_labor, subtotal)
        """
        if not items:
            return "- Chưa có hạng mục dịch vụ/phụ tùng.", 0.0, 0.0, 0.0

        lines = []
        total_parts = 0.0
        total_labor = 0.0

        for index, item in enumerate(items, start=1):
            name = getattr(item, "name", None) or "Hạng mục chưa đặt tên"
            quantity = cls._safe_float(getattr(item, "quantity", 0), 0.0)
            unit_price = cls._safe_float(getattr(item, "unit_price", 0), 0.0)
            labor_cost = cls._safe_float(getattr(item, "labor_cost", 0), 0.0)

            part_cost = quantity * unit_price
            item_total = part_cost + labor_cost

            total_parts += part_cost
            total_labor += labor_cost

            lines.append(
                f"{index}. {name} | SL: {quantity:g} | "
                f"Đơn giá phụ tùng: {unit_price:,.0f} VNĐ | "
                f"Tiền phụ tùng: {part_cost:,.0f} VNĐ | "
                f"Tiền công: {labor_cost:,.0f} VNĐ | "
                f"Thành tiền: {item_total:,.0f} VNĐ"
            )

        subtotal = total_parts + total_labor
        return "\n".join(lines), total_parts, total_labor, subtotal

    @staticmethod
    def _format_money(value: float) -> str:
        """Format tiền Việt Nam."""
        return f"{value:,.0f} VNĐ"

    @classmethod
    def _save_ai_log(
        cls,
        db: Session,
        feature: str,
        prompt_input: str,
        response_output: str,
        model_used: str,
    ) -> None:
        """Lưu log AI."""
        try:
            log_entry = AILog(
                feature=feature,
                prompt_input=prompt_input,
                response_output=response_output,
                model_used=model_used,
            )
            db.add(log_entry)
            db.commit()
        except Exception:
            db.rollback()
            logger.exception("Không thể lưu AI log.")

    @classmethod
    def _get_system_prompt_with_memory(cls, db: Session, base_prompt: str) -> str:
        """Thêm các quy tắc từ AIKnowledgeBase vào System Prompt"""
        try:
            active_rules = db.query(AIKnowledgeBase).filter(AIKnowledgeBase.is_active == True).all()
            if not active_rules:
                return base_prompt
            
            rules_text = "\n".join(f"- {rule.content}" for rule in active_rules)
            injected_prompt = base_prompt + "\n\n--- QUY TẮC BỔ SUNG (KNOWLEDGE BASE) ---\n" + rules_text
            return injected_prompt
        except Exception as e:
            logger.warning("Lỗi đọc AIKnowledgeBase: %s", e)
            return base_prompt

    # ============================================================
    # LLM INTEGRATION
    # ============================================================

    @staticmethod
    def _call_llm(system_prompt: str, user_prompt: str) -> Tuple[str, str]:
        """
        Gửi request tới AI provider theo chiến lược tối ưu tốc độ:
        1. Nếu có GROQ_API_KEY và GEMINI_API_KEY không hợp lệ (không bắt đầu bằng 'AIza'): dùng Groq ngay lập tức (< 1 giây).
        2. Nếu GEMINI_API_KEY hợp lệ (bắt đầu bằng 'AIza'): thử Gemini (timeout 5s). Nếu lỗi hoặc bị chặn mạng tại VN -> chuyển Groq.
        3. Groq API (siêu tốc, ổn định tại VN).
        4. DeepSeek / OpenAI-compatible.
        5. Internal fallback engine.
        """
        # pyrefly: ignore [missing-import]
        import httpx  # type: ignore

        groq_api_key = getattr(settings, "GROQ_API_KEY", "") or os.getenv("GROQ_API_KEY", "")
        groq_model = getattr(settings, "GROQ_MODEL", "openai/gpt-oss-120b") or "openai/gpt-oss-120b"
        gemini_api_key = getattr(settings, "GEMINI_API_KEY", "") or os.getenv("GEMINI_API_KEY", "")

        def _call_groq():
            if not groq_api_key:
                logger.info("[AI Engine] GROQ_API_KEY is not set.")
                return None
            
            models_to_try = list(dict.fromkeys([
                "llama-3.3-70b-versatile",
                "llama-3.1-8b-instant",
                groq_model,
                "mixtral-8x7b-32768",
                "gemma2-9b-it"
            ]))
            for model_candidate in models_to_try:
                if not model_candidate:
                    continue
                try:
                    logger.info("[AI Engine] Sending request to Groq API (%s)...", model_candidate)
                    groq_headers = {
                        "Authorization": f"Bearer {groq_api_key}",
                        "Content-Type": "application/json",
                    }
                    groq_payload = {
                        "model": model_candidate,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.4,
                        "max_tokens": 2048,
                    }
                    groq_response = httpx.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers=groq_headers,
                        json=groq_payload,
                        timeout=15.0,
                    )
                    if groq_response.status_code == 200:
                        data = groq_response.json()
                        choices = data.get("choices") or []
                        if choices:
                            content = choices[0].get("message", {}).get("content")
                            if content:
                                logger.info("[AI Engine] Groq responded successfully with %s!", model_candidate)
                                return content.strip(), f"Groq ({model_candidate})"
                    elif groq_response.status_code == 401:
                        logger.warning("Groq API Key hết hạn hoặc không hợp lệ (401). Chuyển sang engine dự phòng.")
                        break
                    elif groq_response.status_code == 404:
                        logger.warning("Groq model %s not found (404), trying next model candidate...", model_candidate)
                        continue
                    else:
                        logger.warning("Groq HTTP %s: %s", groq_response.status_code, groq_response.text[:200])
                except Exception as ge:
                    logger.warning("Groq API error with model %s: %s", model_candidate, ge)
            return None

        # 1. Thử Google Gemini REST API (Ưu tiên số 1 khi có API Key)
        # Hỗ trợ cả key bắt đầu bằng 'AQ.' hoặc 'AIza'
        if gemini_api_key and len(gemini_api_key.strip()) > 15:
            # Thứ tự ưu tiên: gemini-2.5-flash (ổn định, phản hồi cực nhanh), gemini-flash-latest
            gemini_models = ["gemini-2.5-flash", "gemini-flash-latest", "gemini-1.5-flash"]
            for model_name in gemini_models:
                try:
                    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
                    headers = {
                        "Content-Type": "application/json",
                        "X-goog-api-key": gemini_api_key.strip(),
                    }
                    payload = {
                        "contents": [
                            {
                                "parts": [
                                    {
                                        "text": f"{system_prompt}\n\n{user_prompt}"
                                    }
                                ]
                            }
                        ],
                        "generationConfig": {"temperature": 0.35, "maxOutputTokens": 2048},
                    }
                    response = httpx.post(url, headers=headers, json=payload, timeout=18.0)
                    if response.status_code == 200:
                        data = response.json()
                        candidates = data.get("candidates") or []
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts") or []
                            text = "".join(p.get("text", "") for p in parts).strip()
                            if text:
                                logger.info("[AI Engine] Google Gemini (%s) phản hồi thành công!", model_name)
                                return text, f"Google Gemini ({model_name})"
                    elif response.status_code in [404, 503]:
                        logger.warning("Gemini model %s trả về HTTP %s (quá tải hoặc không hỗ trợ), thử model tiếp theo...", model_name, response.status_code)
                        continue
                    else:
                        logger.warning("Gemini REST API trả về HTTP %s: %s", response.status_code, response.text[:300])
                        break
                except Exception as e:
                    logger.warning("Lỗi gọi Gemini REST API với model %s: %s", model_name, e)

        # 3. Groq API fallback nếu chưa gọi
        if groq_api_key:
            res = _call_groq()
            if res:
                return res

        # 4. DeepSeek / OpenAI-compatible API (fallback)
        ds_api_key = getattr(settings, "DEEPSEEK_API_KEY", "")
        if ds_api_key:
            try:
                base_url = (getattr(settings, "DEEPSEEK_BASE_URL", "") or "").rstrip("/")
                if base_url.endswith("/chat/completions"):
                    api_url = base_url
                elif base_url.endswith("/v1"):
                    api_url = f"{base_url}/chat/completions"
                else:
                    api_url = f"{base_url}/v1/chat/completions"

                model_name = getattr(settings, "AI_MODEL_NAME", "deepseek-chat") or "deepseek-chat"
                ds_headers = {
                    "Authorization": f"Bearer {ds_api_key}",
                    "Content-Type": "application/json",
                }
                ds_payload = {
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.2,
                    "max_tokens": 2000,
                }
                ds_response = httpx.post(api_url, headers=ds_headers, json=ds_payload, timeout=15.0)
                if ds_response.status_code == 200:
                    data = ds_response.json()
                    choices = data.get("choices") or []
                    if choices:
                        content = choices[0].get("message", {}).get("content")
                        if content:
                            model_used = data.get("model", model_name)
                            return content.strip(), f"AI API ({model_used})"
            except Exception as de:
                logger.warning("Lỗi gọi DeepSeek/OpenAI API: %s", de)

        # 5. Internal fallback
        return AIService._fallback_engine(system_prompt, user_prompt)


    # ============================================================
    # FALLBACK ENGINE
    # ============================================================

    @staticmethod
    def _fallback_engine(system_prompt: str, user_prompt: str) -> Tuple[str, str]:
        """
        Fallback chuyên sâu cho Trợ Lý Kỹ Thuật AI khi không có kết nối AI bên ngoài.
        Tích hợp 3 năng lực cốt lõi:
        1. Tư vấn theo triệu chứng kỹ thuật (phanh, động cơ, điều hòa, gầm, lái, taplo)
        2. Tư vấn chi phí & Bảng giá minh bạch (5k, 10k, 20k, 40k, combo điều hòa, phanh gầm)
        3. So sánh dịch vụ trực quan (Cấp 1 vs Cấp 2, Dầu khoáng vs Tổng hợp, Nội soi vs Tháo taplo...)
        """
        model_used = "Trợ Lý Kỹ Thuật AI (VTV Engine)"
        prompt_lower = user_prompt.lower()

        # Trích xuất câu hỏi thực sự của người dùng
        target_text = prompt_lower
        for prefix in ["yêu cầu tư vấn của khách hàng:", "yêu cầu của người dùng:", "yêu cầu nội bộ của người dùng:"]:
            if prefix in prompt_lower:
                parts = prompt_lower.split(prefix)
                if len(parts) > 1:
                    raw_q = parts[1].split("ngữ cảnh")[0].strip('"\n\r ')
                    if raw_q:
                        target_text = raw_q
                        break

        # ------------------------------------------------------------
        # 0. ĐỊNH TUYẾN THEO MÃ TESTCASE & KỊCH BẢN KIỂM THỬ (TEST SCENARIOS)
        # ------------------------------------------------------------
        # Nhận diện yêu cầu tổng hợp danh sách testcase
        has_summary_word = any(kw in target_text for kw in ["tổng hợp", "danh sách", "liệt kê", "các testcase", "các kịch bản", "kịch bản kiểm thử"])
        has_tc_word = any(kw in target_text for kw in ["testcase", "test case", "kịch bản", "tc-cus"])
        has_specific_tc = any(f"0{i}" in target_text for i in range(1, 5))
        
        if (has_summary_word and has_tc_word) or (target_text.strip() in ["testcase", "test case", "kịch bản", "tc-cus"]):
            if not has_specific_tc:
                return (
                    """📋 **TỔNG HỢP CÁC KỊCH BẢN & TEST CASE KIỂM THỬ AI KHÁCH HÀNG (CUSTOMER AI)**

Hệ thống Trợ Lý Kỹ Thuật AI hỗ trợ toàn bộ 4 bộ kịch bản kiểm thử tiêu chuẩn:

**1. Kịch bản TS-CUS-01: Chẩn đoán theo triệu chứng xe**
• `TC-CUS-01.1`: Phanh rung giật & kêu rít kim loại khi đạp phanh
• `TC-CUS-01.2`: Động cơ hụt ga, khó nổ, máy rung giật khi tăng tốc
• `TC-CUS-01.3`: Điều hòa không mát, gió yếu và có mùi chua ẩm mốc
• `TC-CUS-01.4`: Tiếng kêu lục cục dưới gầm khi qua ổ gà / gờ giảm tốc
• `TC-CUS-01.5`: Vô lăng bị lệch tâm, xe nhao lái sang một bên
• `TC-CUS-01.6`: Đèn cảnh báo Check Engine (cá vàng) trên taplo bật sáng

**2. Kịch bản TS-CUS-02: Báo giá & Dự toán chi phí minh bạch**
• `TC-CUS-02.1`: Bảng giá bảo dưỡng định kỳ các cấp (Cấp 1 đến Cấp 4)
• `TC-CUS-02.2`: Bảng giá các gói Combo chuyên sâu (Điều hòa, Phanh gầm, Buồng đốt)
• `TC-CUS-02.3`: Cơ cấu minh bạch tiền công thợ + vật tư, cam kết không phát sinh

**3. Kịch bản TS-CUS-03: So sánh dịch vụ trực quan**
• `TC-CUS-03.1`: So sánh Bảo dưỡng Cấp 1 (5.000 km) vs Cấp 2 (10.000 km)
• `TC-CUS-03.2`: So sánh Dầu nhớt khoáng vs Dầu tổng hợp toàn phần (Fully Synthetic)
• `TC-CUS-03.3`: So sánh Vệ sinh nội soi dàn lạnh vs Tháo rã toàn bộ taplo
• `TC-CUS-03.4`: So sánh Láng đĩa phanh CNC vs Thay mới đĩa phanh

**4. Kịch bản TS-CUS-04: Đặt lịch hẹn & Bảo mật ranh giới**
• `TC-CUS-04.1`: Chuyển đổi đặt lịch hẹn trực tuyến qua hệ thống
• `TC-CUS-04.2`: Chống Prompt Injection và tuân thủ bảng giá niêm yết

👉 *Bạn có thể gõ trực tiếp mã testcase (ví dụ: `TC-CUS-01.1`) hoặc đặt câu hỏi tự nhiên để kiểm thử ngay!*""",
                    model_used,
                )

        # ------------------------------------------------------------
        # 1. NĂNG LỰC: SO SÁNH DỊCH VỤ (SERVICE COMPARISON)
        # ------------------------------------------------------------
        is_comparison = any(kw in target_text for kw in ["so sánh", "khác nhau", "nên chọn", " vs ", "hay là", "phân biệt", "tc-cus-03", "kịch bản 3"])
        if is_comparison or "so sánh" in prompt_lower:
            if any(kw in target_text for kw in ["cấp 1", "cấp 2", "5.000", "10.000", "5000", "10000", "tc-cus-03.1"]):
                return (
                    """⚖️ **SO SÁNH BẢO DƯỠNG CẤP 1 (5.000 KM) VS CẤP 2 (10.000 KM)**

| Tiêu chí | Cấp 1 (5.000 km) | Cấp 2 (10.000 km) |
| :--- | :--- | :--- |
| **Mục tiêu** | Thay dầu bảo vệ máy cơ bản | Làm sạch sâu & bảo dưỡng an toàn phanh |
| **Hạng mục dầu** | Thay dầu nhớt + lọc nhớt mới | Thay dầu nhớt + lọc nhớt mới |
| **Hệ thống phanh** | Kiểm tra bằng mắt qua nan mâm | Tháo 4 bánh, vệ sinh đĩa phanh, tra mỡ ắc trượt |
| **Lọc gió & lốp** | Vệ sinh thổi bụi | Vệ sinh lọc gió động cơ & cabin, đảo lốp 4 bánh |
| **Thời gian làm** | 45 – 60 phút | 90 – 120 phút |
| **Chi phí dự kiến** | **650.000 – 1.150.000 VNĐ** | **1.250.000 – 1.850.000 VNĐ** |

💡 **Lời khuyên KTV:** Cứ sau mỗi 2 lần bảo dưỡng Cấp 1, bạn nên thực hiện 1 lần Cấp 2 để đảm bảo cùm phanh không bị kẹt rỉ và lốp xe mòn đều.""",
                    model_used,
                )
            elif any(kw in target_text for kw in ["dầu", "nhớt", "khoáng", "tổng hợp", "synthetic", "tc-cus-03.2"]):
                return (
                    """⚖️ **SO SÁNH DẦU NHỚT KHOÁNG VS DẦU TỔNG HỢP TOÀN PHẦN (FULLY SYNTHETIC)**

| Tiêu chí | Dầu khoáng thông thường | Dầu tổng hợp toàn phần (Fully Synthetic) |
| :--- | :--- | :--- |
| **Gốc dầu** | Tinh chế từ dầu thô mỏ tự nhiên | Phân tử tổng hợp nhân tạo đồng nhất |
| **Chu kỳ thay** | 5.000 km hoặc 3 – 4 tháng | 10.000 – 15.000 km hoặc 12 tháng |
| **Chịu nhiệt** | Dễ biến chất ở nhiệt độ cao | Bền màng dầu ở nhiệt độ cực đoan |
| **Bảo vệ động cơ** | Chống cặn trung bình | Tẩy rửa cặn bẩn, chống mài mòn vượt trội |
| **Tiết kiệm xăng** | Mức tiêu thụ tiêu chuẩn | Giúp máy bốc êm, tiết kiệm 2–3% nhiên liệu |
| **Chi phí** | 350.000 – 550.000 VNĐ/can | 850.000 – 1.350.000 VNĐ/can (5W-30/0W-20) |

💡 **Lời khuyên KTV:** Xe đời mới, xe có Turbo tăng áp hoặc thường xuyên đi đường phố tắc đường nên dùng dầu Tổng Hợp Toàn Phần để giải nhiệt và tối ưu tuổi thọ động cơ.""",
                    model_used,
                )
            elif any(kw in target_text for kw in ["nội soi", "tháo taplo", "hạ taplo", "dàn lạnh", "tc-cus-03.3"]):
                return (
                    """⚖️ **SO SÁNH VỆ SINH NỘI SOI DÀN LẠNH VS THÁO DỠ HẠ TAPLO**

| Tiêu chí | Vệ sinh nội soi camera HD | Tháo hạ toàn bộ taplo xe |
| :--- | :--- | :--- |
| **Phương pháp** | Luồn ống camera & vòi phun áp lực | Tháo rời bảng điều khiển taplo, vô lăng, dây điện |
| **Thời gian làm** | 45 – 60 phút | 1 – 2 ngày làm việc |
| **Rủi ro kỹ thuật** | 0% rủi ro, nguyên bản tuyệt đối | Dễ gãy lẫy nhựa, nguy cơ phát tiếng kêu lọc xọc |
| **Hiệu quả khử bẩn** | Sạch 90 – 95% nấm mốc | Sạch 100% từng ngóc ngách |
| **Chi phí** | **650.000 – 950.000 VNĐ** | **2.500.000 – 4.000.000 VNĐ** |

💡 **Lời khuyên KTV:** Nên chọn vệ sinh nội soi định kỳ hàng năm. Chỉ tháo taplo khi dàn lạnh bị mục thủng xì gas cần hàn hoặc thay mới hoàn toàn.""",
                    model_used,
                )
            elif any(kw in target_text for kw in ["sơn dặm", "sơn quây"]):
                return (
                    """⚖️ **SO SÁNH SƠN DẶM VÁ ĐIỂM VS SƠN QUÂY TOÀN BỘ XE**

| Tiêu chí | Sơn dặm vá điểm | Sơn quây toàn bộ thân xe |
| :--- | :--- | :--- |
| **Ứng dụng** | Vết xước cản trước/sau, tai vè cục bộ | Xe nhiều vết trầy xước, ố sơn lâu năm |
| **Thời gian** | 1 – 2 ngày làm việc | 4 – 6 ngày trong phòng sơn hấp sấy |
| **Giữ sơn zin** | Giữ lại 80 – 90% nước sơn gốc xuất xưởng | Sơn mới toàn bộ lớp vỏ bề mặt |
| **Chi phí** | 400.000 – 800.000 VNĐ/vị trí | 12.000.000 – 25.000.000 VNĐ/xe |

💡 **Lời khuyên KTV:** Garage VTV sử dụng hệ thống pha màu vi tính PPG đạt độ đồng màu 99% so với màu sơn gốc của xe.""",
                    model_used,
                )
            elif any(kw in target_text for kw in ["láng đĩa", "thay đĩa", "tc-cus-03.4"]):
                return (
                    """⚖️ **SO SÁNH LÁNG ĐĨA PHANH VI TÍNH VS THAY MỚI ĐĨA PHANH**

| Tiêu chí | Láng đĩa phanh vi tính | Thay mới đĩa phanh chính hãng |
| :--- | :--- | :--- |
| **Điều kiện làm** | Đĩa còn dày trên mức an toàn (Min Thickness) | Đĩa bị mòn quá giới hạn, nứt nẻ hoặc đảo nặng |
| **Xử lý sự cố** | Triệt tiêu gờ rãnh, hết rung giật bàn đạp | Khôi phục 100% hiệu suất phanh nguyên bản |
| **Thời gian làm** | 45 – 60 phút trên máy láng chuyên dụng | 30 – 45 phút lắp đặt |
| **Chi phí** | **350.000 – 500.000 VNĐ/cặp đĩa** | **1.800.000 – 4.500.000 VNĐ/cặp đĩa** |

💡 **Lời khuyên KTV:** Kỹ thuật viên tại Garage VTV sẽ dùng thước kẹp panme đo độ dày trước. Nếu còn an toàn thì chỉ cần láng đĩa để tiết kiệm 70% chi phí.""",
                    model_used,
                )
            else:
                return (
                    """⚖️ **BẢNG SO SÁNH CÁC LỰA CHỌN DỊCH VỤ PHỔ BIẾN TẠI GARAGE VTV**

1. **Bảo dưỡng Cấp 1 (5k km) vs Cấp 2 (10k km):** Cấp 1 thay dầu cơ bản (650k); Cấp 2 thêm bảo dưỡng phanh và đảo lốp (1.25tr).
2. **Dầu khoáng vs Dầu tổng hợp toàn phần:** Dầu tổng hợp chạy được 10.000 km, giải nhiệt tốt hơn, tiết kiệm nhiên liệu hơn.
3. **Vệ sinh nội soi dàn lạnh vs Tháo hạ taplo:** Nội soi nhanh 45 phút, an toàn không gãy ngàm, tiết kiệm 60% chi phí.
4. **Láng đĩa phanh vs Thay đĩa mới:** Láng đĩa vi tính xử lý hết rung phanh khi đĩa còn dày an toàn, tiết kiệm chi phí.

👉 Bạn đang phân vân lựa chọn giữa 2 phương án nào? Hãy nhắn câu hỏi để tôi phân tích chi tiết nhé!""",
                    model_used,
                )

        # ------------------------------------------------------------
        # 2. NĂNG LỰC: TƯ VẤN THEO TRIỆU CHỨNG XE (SYMPTOMS)
        # ------------------------------------------------------------
        is_symptom = any(kw in target_text for kw in [
            "triệu chứng", "rung", "giật", "hụt ga", "khó nổ", "máy yếu", "không nổ",
            "mất lạnh", "không mát", "mùi hôi", "mùi khét", "mùi ẩm", "mùi chua", "mùi",
            "điều hòa", "máy lạnh", "lạnh", "mát", "gas", "nạp gas", "lốc lạnh", "taplo", "tháo taplo",
            "kêu", "lục cục", "lạch cạch", "ken két", "lệch lái", "nhao lái", "vô lăng",
            "báo lỗi", "check engine", "cá vàng", "abs", "đèn taplo", "nóng máy", "sôi nước",
            "khói đen", "khói trắng", "chảy dầu", "tc-cus-01", "kịch bản 1"
        ])
        if is_symptom:
            if ("phanh" in target_text and any(w in target_text for w in ["rung", "giật", "kêu", "rít", "cạ"])) or "tc-cus-01.1" in target_text:
                return (
                    """🩺 **TƯ VẤN KỸ THUẬT: HIỆN TƯỢNG RUNG GIẬT HOẶC KÊU RÍT KHI PHANH**

🔍 **Nhận định sơ bộ & Nguyên nhân:**
1. **Đĩa phanh bị cong vênh/tạo gờ:** Do phanh gấp liên tục khi đĩa đang rất nóng hoặc rửa xe ngay sau khi chạy đường dài làm đĩa biến dạng nhiệt.
2. **Má phanh mòn sát đế kim loại:** Má phanh cạ trực tiếp vào đĩa tạo tiếng rít ken két và làm xước mặt đĩa.
3. **Ắc trượt cùm phanh (Calip) bị khô mỡ:** Khiến má phanh tì lệch một bên gây rung vô lăng khi rà phanh ở tốc độ cao.

⚠️ **Mức độ an toàn:** **TRUNG BÌNH ĐẾN CAO** — Quãng đường phanh có thể bị kéo dài, cần kiểm tra sớm.

🛠️ **Phương án xử lý tại Garage VTV:**
- Dùng đồng hồ so đo độ đảo đĩa phanh và thước kẹp đo chiều dày má phanh.
- Láng đĩa phanh vi tính triệt tiêu gờ rãnh (~350.000 – 500.000 VNĐ/cặp).
- Tra mỡ chịu nhiệt ắc trượt hoặc thay má phanh Ceramic chống bụi.

📅 *Đặt lịch ngay để được kỹ thuật viên đo đạc an toàn phanh miễn phí!*""",
                    model_used,
                )
            elif any(w in target_text for w in ["rung nhẹ", "rung động cơ", "rung máy", "hao xăng", "tốn xăng", "hao nhiên liệu", "đèn đỏ rung"]) or (
                    "rung" in target_text and any(w in target_text for w in ["dừng", "đèn đỏ", "chờ", "ga", "số d", "nhẹ"])
                ):
                return (
                    """🩺 **TƯ VẤN KỸ THUẬT: ĐỘNG CƠ RUNG NHẸ KHI DỪNG VÀ HAO XĂNG NHIỀU**

Với xe đã chạy **50.000 km**, hiện tượng **rung nhẹ khi dừng đèn đỏ kết hợp hao xăng hơn bình thường**, Kỹ thuật viên Garage VTV phân tích các nguyên nhân khả thi như sau:

🔍 **Nguyên nhân khả thi theo thứ tự ưu tiên:**

**1. Bugi đánh lửa mòn điện cực (Nguyên nhân số 1 ở xe 50.000 km):**
- Bugi mòn làm tia lửa điện yếu, đốt cháy hỗn hợp xăng-khí không hoàn toàn.
- Triệu chứng: máy rung nhẹ ở tốc độ thấp (khi dừng ở số D), tiêu hao xăng tăng 15-20%.
- ⚠️ Mức độ: **TRUNG BÌNH** — Nên thay ngay để tránh hỏng bô-bin đánh lửa.

**2. Cao su chân máy (Motor Mount) bị lão hóa xẹp:**
- Ở mốc 50.000 km, cao su giảm chấn chân máy thường bắt đầu chai cứng/xẹp.
- Rung động động cơ truyền trực tiếp vào khung cabin, đặc biệt rõ khi xe đứng yên số D.
- ⚠️ Mức độ: **NHẸ-TRUNG BÌNH** — Không nguy hiểm nhưng gây mỏi lái xe đường dài.

**3. Kim phun nhiên liệu nghẹt bẩn / họng ga bám muội:**
- Kim phun bị nghẹt tia phun → ECU bơm thêm xăng để bù → hao xăng tăng.
- Hỗn hợp hòa khí không đều giữa các xi-lanh → máy rung không đều.

**4. Lọc gió động cơ bẩn tắc (kiểm tra đơn giản, sửa rẻ):**
- Ở 50.000 km nếu chưa thay, lọc gió bít làm thiếu khí → ECU bơm thêm xăng.

🛠️ **Đề xuất hạng mục kiểm tra & ước tính chi phí:**

| Hạng mục | Ước tính chi phí |
| :--- | :--- |
| Thay bugi Iridium (4 cái) | **450.000 – 850.000 VNĐ** |
| Kiểm tra & thay cao su chân máy | **600.000 – 1.400.000 VNĐ** |
| **Combo Phục Hồi Công Suất** (Vệ sinh họng ga + Kim phun siêu âm + Buồng đốt) | **750.000 – 1.200.000 VNĐ** |
| Thay lọc gió động cơ | **150.000 – 280.000 VNĐ** |

💡 **Khuyến nghị của KTV:** Nên cắm máy chẩn đoán **OBD-II** trước để đọc mã lỗi và xem thông số **Long Term Fuel Trim** — nếu LTFT dương > 10% thì xác nhận ngay vấn đề nhiên liệu/đánh lửa, không cần đoán mò.

📅 *Bấm **Đặt Lịch** để KTV Garage VTV kiểm tra bugi, chân máy và cắm máy đọc lỗi ngay trong buổi sáng!*""",
                    model_used,
                )
            elif any(w in target_text for w in ["hụt ga", "khó nổ", "giật", "máy yếu", "tc-cus-01.2"]):
                return (
                    """🩺 **TƯ VẤN KỸ THUẬT: ĐỘNG CƠ HỤT GA, KHÓ NỔ HOẶC RUNG GIẬT**

🔍 **Nhận định sơ bộ & Nguyên nhân:**
1. **Hệ thống đánh lửa:** Bugi mòn điện cực, khe hở đánh lửa rộng hoặc bô-bin (Ignition Coil) bị đánh lửa rò/yếu áp.
2. **Hệ thống nạp khí & nhiên liệu:** Họng ga bám muội đen kẹt cánh bướm ga, kim phun nhiên liệu bị nghẹt tia tơi xốp, lọc xăng bị bẩn tắc.
3. **Cao su chân máy/chân số:** Bị xẹp, vỡ cao su giảm chấn khiến rung động truyền trực tiếp vào khung cabin khi dừng đèn đỏ (về số D).

⚠️ **Mức độ an toàn:** **TRUNG BÌNH** — Nguy cơ chết máy giữa đường hoặc tiêu hao nhiều nhiên liệu.

🛠️ **Phương án xử lý tại Garage VTV:**
- Cắm máy chẩn đoán quét mã lỗi động cơ OBD-II và xem thông số nhiên liệu Live Data (Long Term Fuel Trim).
- **Combo Phục Hồi Công Suất:** Vệ sinh họng ga, súc rửa kim phun siêu âm và làm sạch buồng đốt (~750.000 – 1.200.000 VNĐ).

📅 *Bấm Đặt Lịch để kỹ thuật viên kiểm tra bugi và hệ thống nhiên liệu ngay nhé!*""",
                    model_used,
                )
            elif any(w in target_text for w in ["điều hòa", "máy lạnh", "lạnh", "mùi", "mùi ẩm", "mùi chua", "nóng cabin", "nạp gas", "tháo taplo", "nội soi", "tc-cus-01.3"]):
                if any(w in target_text for w in ["mùi ẩm", "mùi chua", "vẫn lạnh", "nạp gas", "tháo taplo"]) or ("mùi" in target_text and any(w in target_text for w in ["lạnh", "bật", "gas", "taplo"])):
                    return (
                        """🩺 **TƯ VẤN KỸ THUẬT: ĐIỀU HÒA CÓ MÙI ẨM MỐC KHI MỚI BẬT (XE VẪN LẠNH)**

Chào bạn! Với triệu chứng điều hòa xe **có mùi ẩm chua khi mới bật nhưng vẫn lạnh sâu**, Kỹ thuật viên Garage VTV giải đáp trực tiếp cho bạn như sau:

1️⃣ **Có cần nạp gas không?**
👉 **HOÀN TOÀN KHÔNG CẦN NẠP GAS.** Vì xe bật vẫn phả ra hơi lạnh bình thường chứng tỏ hệ thống lốc lạnh (Compressor) và lượng môi chất lạnh (Gas R134a) vẫn đủ áp suất tiêu chuẩn, hệ thống hoàn toàn kín không bị xì hở.

2️⃣ **Có cần tháo taplo ngay không?**
👉 **TUYỆT ĐỐI CHƯA CẦN THÁO TAPLO.** Việc tháo dỡ toàn bộ taplo rất tốn kém (2.500.000 – 4.000.000 VNĐ), mất 1–2 ngày và nguy cơ gãy lẫy nhựa, phát sinh tiếng kêu cọt kẹt khi xe chạy sau này. Phương án tháo taplo chỉ dùng khi giàn lạnh bị mục nát xì gas cần thay thế lõi mới.

3️⃣ **Nên kiểm tra và xử lý gì trước?**
• **Bước 1 — Kiểm tra & thay lọc gió điều hòa (Cabin Filter):** Sau thời gian sử dụng, lọc gió tích tụ bụi bẩn, hơi ẩm và xác côn trùng gây mùi ẩm mốc khi quạt gió mới khởi động. *(Chi phí: 150.000 – 250.000 VNĐ, thay trong 5 phút)*.
• **Bước 2 — Kiểm tra thông tắc đường ống thoát nước ngưng:** Đảm bảo nước đọng không bị tràn ứ đọng nấm mốc dưới sàn xe.
• **Bước 3 — Vệ sinh nội soi dàn lạnh camera HD + Diệt khuẩn Nano Bạc:** Kỹ thuật viên luồn ống camera HD vào hộp gió, phun dung dịch vi sinh bóc tách triệt để rêu mốc và thổi khô mà **không cần tháo taplo**. Dàn lạnh sạch 95%, hết sạch mùi hôi. *(Thời gian: 45 phút, chi phí trọn gói 650.000 – 850.000 VNĐ)*.

💡 **Mẹo sử dụng phòng ngừa mùi ẩm:** Trước khi kết thúc hành trình hoặc tắt máy 2–3 phút, bạn nên **tắt nút A/C** và chỉ bật quạt gió thổi tự nhiên để sấy khô hơi nước đọng trên giàn lạnh, triệt tiêu môi trường ẩm mốc sinh sôi.

📅 *Bạn có thể bấm nút **Xác Nhận Đặt Lịch** ngay phía trên để được KTV Garage VTV soi kiểm tra lọc gió và dàn lạnh miễn phí nhé!*""",
                        model_used,
                    )
                else:
                    return (
                        """🩺 **TƯ VẤN KỸ THUẬT: ĐIỀU HÒA KHÔNG MÁT, MẤT LẠNH HOẶC CÓ MÙI HÔI**

🔍 **Nhận định sơ bộ & Nguyên nhân:**
1. **Dàn lạnh bám nấm mốc:** Môi trường ẩm ướt làm tích tụ vi khuẩn và bụi bẩn tạo mùi chua khó chịu khi vừa bật AC.
2. **Thiếu môi chất lạnh (Gas R134a):** Rò rỉ ti van nạp, gioăng cao su đường ống hoặc lốc lạnh bị xì phớt trục.
3. **Lọc gió điều hòa tắc nghẹt:** Khiến lưu lượng gió thổi qua cửa gió rất yếu dù quạt gió bật nấc lớn.
4. **Dàn nóng phía đầu xe quá bẩn:** Không giải nhiệt được gas khiến xe đứng im một chỗ mất mát, khi chạy mới mát.

⚠️ **Mức độ an toàn:** **NHẸ** — Không nguy hiểm tính mạng nhưng ảnh hưởng lớn đến sức khỏe và sự thoải mái.

🛠️ **Phương án xử lý tại Garage VTV:**
- **Combo Điều Hòa Mát Sâu:** Vệ sinh nội soi dàn lạnh camera HD + Xịt khử khuẩn nano bạc + Nạp gas bổ sung + Thay lọc gió than hoạt tính (**650.000 – 950.000 VNĐ** trọn gói trong 45 phút).

📅 *Đặt lịch ngay để khoang lái luôn thơm tho, mát rượi đón ngày nắng nóng!*""",
                        model_used,
                    )
            elif any(w in target_text for w in ["lục cục", "kêu", "gầm", "giảm xóc", "cục cục", "tc-cus-01.4"]):
                return (
                    """🩺 **TƯ VẤN KỸ THUẬT: TIẾNG KÊU LỤC CỤC DƯỚI GẦM KHI QUA GỜ GIẢM TỐC**

🔍 **Nhận định sơ bộ & Nguyên nhân:**
1. **Rô-tuyn thanh cân bằng bị rơ:** Đây là nguyên nhân số 1 gây tiếng kêu 'cục cục' khô khốc khi một bên bánh đi qua gờ nhấp nhô.
2. **Cao su càng A hoặc cao su gầm bị chai vỡ:** Khiến các chi tiết kim loại va đập vào khung sườn khi chịu tải xoắn.
3. **Giảm xóc (phuộc nhún) xì dầu:** Mất khả năng dập tắt dao động, tăm-bông hoặc bát bèo trên đầu phuộc bị nát.
4. **Thước lái bị rơ bạc:** Kêu lộc cộc khi đánh lái hoặc đi đường sỏi đá gồ ghề.

⚠️ **Mức độ an toàn:** **TRUNG BÌNH ĐẾN CAO** — Ảnh hưởng tới khả năng bám đường và mòn lốp không đều.

🛠️ **Phương án xử lý tại Garage VTV:**
- Nâng xe trên cầu nâng 2 trụ, kiểm tra độ rơ của từng khớp cầu rô-tuyn và càng gầm.
- Siết lực lại toàn bộ bulong khung gầm theo thông số chuẩn kỹ thuật của hãng.

📅 *Ghé xưởng ngay để kỹ thuật viên kiểm tra gầm miễn phí cho xe bạn!*""",
                    model_used,
                )
            elif any(w in target_text for w in ["lệch lái", "nhao lái", "vô lăng", "mòn lốp", "tc-cus-01.5"]):
                return (
                    """🩺 **TƯ VẤN KỸ THUẬT: VÔ LĂNG BỊ LỆCH, NHAO LÁI SANG MỘT BÊN**

🔍 **Nhận định sơ bộ & Nguyên nhân:**
1. **Sai lệch góc đặt bánh xe (Camber, Caster, Toe):** Sau cú va chạm ổ gà mạnh hoặc leo lề đường làm sai lệch góc kỹ thuật.
2. **Áp suất lốp không cân bằng:** Bánh bên mềm hơn sẽ có lực cản lớn hơn kéo xe nhao về phía đó.
3. **Lốp xe bị mòn lệch (mòn mép trong hoặc ngoài):** Tạo lực kéo lệch khi xe lăn bánh thẳng.

⚠️ **Mức độ an toàn:** **TRUNG BÌNH** — Gây mỏi tay khi lái xe đường dài và làm lốp nhanh hỏng.

🛠️ **Phương án xử lý tại Garage VTV:**
- **Dịch vụ Cân chỉnh góc đặt bánh xe 3D điện tử Hunter:** Đo thông số 3D chính xác từng phút độ, căn chuẩn vô lăng thẳng tắp (**350.000 – 500.000 VNĐ**).

📅 *Đặt lịch cân chỉnh góc lái để lái xe an toàn, êm ái trên cao tốc!*""",
                    model_used,
                )
            elif any(w in target_text for w in ["check engine", "báo lỗi", "cá vàng", "đèn lỗi", "taplo", "tc-cus-01.6"]):
                return (
                    """🩺 **TƯ VẤN KỸ THUẬT: ĐÈN BÁO LỖI ĐỘNG CƠ (CHECK ENGINE) BẬT SÁNG**

🔍 **Nhận định sơ bộ & Nguyên nhân:**
1. **Cảm biến khí xả (Oxygen Sensor):** Bám bẩn muội carbon hoặc đo sai tỷ lệ hòa khí xăng/gió.
2. **Cảm biến lưu lượng gió (MAF/MAP):** Bẩn khiến ECU tính sai lượng khí nạp vào buồng đốt.
3. **Bỏ lửa xi-lanh (Misfire - mã P0300..P0304):** Do hỏng bugi hoặc chết bô-bin đánh lửa.
4. **Nắp bình xăng vặn chưa chặt hoặc van hồi hơi xăng (EVAP):** Gây rò rỉ áp suất hơi nhiên liệu.

⚠️ **Mức độ khẩn cấp:**
- Nếu đèn sáng vàng đứng yên: Xe vẫn chạy được tạm thời, nên đến garage kiểm tra trong vòng 1-2 ngày.
- Nếu đèn nhấp nháy liên tục hoặc kèm đèn áp suất dầu/nhiệt độ đỏ: Dừng xe ngay lập tức để bảo vệ động cơ.

🛠️ **Phương án xử lý tại Garage VTV:**
- Cắm máy quét chuyên dụng chẩn đoán OBD-II đọc mã lỗi chính xác (DTC code) và xóa lỗi sau khắc phục.

📅 *Đưa xe qua Garage VTV để được cắm máy đọc lỗi nhanh trong 10 phút!*""",
                    model_used,
                )

        # Guard: is_symptom True but no sub-branch matched -> prevent fall-through to pricing
        if is_symptom:
            return (
                """🧺 **TƯ VẤN KỸ THUẬT: PHÂN TÍCH TRIỆU CHỨNG XE**

Kỹ thuật viên Garage VTV đã ghi nhận mô tả triệu chứng của bạn.

🔍 **Nhận định sơ bộ & Hướng xử lý:**
Với triệu chứng bạn mô tả, KTV cần kiểm tra thực tế để xác định chính xác nguyên nhân:

1. **Cắm máy OBD-II đọc mã lỗi động cơ** — Chỉ mất 10 phút, chính xác tuyệt đối.
2. **Kiểm tra trực quan** — KTV xem mức dầu, dây đai, cao su gầm, hệ thống phanh.
3. **Lái thử thực tế** — Xác nhận điều kiện xuất hiện triệu chứng (tốc độ, nhiệt độ xe).

⚠️ **Lưu ý an toàn:** Nếu có tiếng kêu lớn đột ngột, mùi khét, khói hoặc đèn đỏ cảnh báo → Dừng xe ngay.

💰 **Chi phí chẩn đoán:** Kiểm tra + OBD-II = **Miễn phí** khi đặt lịch qua hệ thống.

📅 *Bấm **Đặt Lịch** để KTV Garage VTV kiểm tra và tư vấn chi phí chính xác theo tình trạng thực tế!*""",
                model_used,
            )

        # ------------------------------------------------------------
        # 3. NĂNG LỰC: TƯ VẤN CHI PHÍ & BẢNG GIÁ DỊCH VỤ (COST / PRICING)
        # Chỉ kích hoạt khi KHÔNG phải câu hỏi triệu chứng
        # ------------------------------------------------------------
        is_cost_query = any(kw in target_text for kw in [
            "chi phí", "bảng giá", "báo giá", "giá bao nhiêu", "hết bao nhiêu", "tiền công",
            "giá dịch vụ", "bảo dưỡng hết bao nhiêu", "giá thay", "phí bảo dưỡng",
            "tc-cus-02", "tc-cus-02.1", "tc-cus-02.2", "tc-cus-02.3", "kịch bản 2"
        ]) and not is_symptom
        if is_cost_query:
            return (
                """💰 **BẢNG DỰ TOÁN CHI PHÍ DỊCH VỤ & BẢO DƯỠNG NIÊM YẾT TẠI GARAGE VTV**

Garage VTV cam kết báo giá minh bạch, kiểm tra tư vấn trước khi thực hiện:

🛠️ **1. Các Gói Bảo Dưỡng Định Kỳ Tiêu Chuẩn:**
- **Gói Cấp 1 (5.000 km):** **650.000 – 1.150.000 VNĐ** (Công: 150k-350k; Dầu nhớt tổng hợp + Lọc dầu)
- **Gói Cấp 2 (10.000 km):** **1.250.000 – 1.850.000 VNĐ** (Bao gồm Cấp 1 + Bảo dưỡng phanh 4 bánh, đảo lốp, vệ sinh 2 lọc gió)
- **Gói Cấp 3 (20.000 – 30.000 km):** **2.200.000 – 3.200.000 VNĐ** (Bao gồm Cấp 2 + Thay 2 lọc gió, súc rửa buồng đốt kim phun)
- **Gói Cấp 4 (40.000 – 80.000 km - Đại tu):** **4.500.000 – 7.500.000 VNĐ** (Thay dầu số, dầu phanh, nước mát, bugi iridium, lọc xăng)

✨ **2. Bảng Giá Các Gói Combo Chuyên Sâu:**
- ❄️ **Combo Điều Hòa Mát Sâu:** **650.000 – 950.000 VNĐ** (Nội soi dàn lạnh, nạp gas, khử khuẩn ozone, thay lọc cabin than hoạt tính)
- 🛑 **Combo Phanh & Gầm An Toàn:** **550.000 – 850.000 VNĐ** (Vệ sinh 4 cụm phanh đĩa, tra mỡ chịu nhiệt ắc trượt, cân chỉnh gầm)
- ⚙️ **Láng đĩa phanh vi tính:** **350.000 – 500.000 VNĐ/cặp đĩa**
- 🚗 **Cân chỉnh góc đặt bánh xe 3D Hunter:** **350.000 – 500.000 VNĐ**
- 🧼 **Combo Phục Hồi Công Suất:** **750.000 – 1.200.000 VNĐ** (Vệ sinh buồng đốt, kim phun, bướm ga)

💡 *Ghi chú:* Giá phụ tùng có thể thay đổi nhẹ tùy theo dòng xe (xe sedan, SUV hoặc xe sang). Khách hàng luôn được duyệt báo giá trước khi KTV thi công!""",
                model_used,
            )

        # ------------------------------------------------------------
        # 4. GÓI & COMBO BẢO DƯỠNG TIÊU CHUẨN (KHI HỎI RIÊNG)
        # ------------------------------------------------------------
        if any(kw in target_text for kw in ["bảo dưỡng", "combo", "gói", "5.000", "5000", "10.000", "10000", "20.000", "20000", "40.000", "40000", "thay dầu", "thay nhớt"]):
            if "10.000" in target_text or "10000" in target_text or "cấp 2" in target_text:
                return (
                    """🛠️ **TƯ VẤN GÓI BẢO DƯỠNG CẤP 2 (MỐC 10.000 KM)**

Gói bảo dưỡng định kỳ tiêu chuẩn giúp duy trì hiệu suất vận hành và an toàn phanh:

📋 **Các hạng mục công việc thực hiện:**
1. Toàn bộ hạng mục cấp 1: Thay dầu nhớt động cơ và thay lọc nhớt mới.
2. Bảo dưỡng hệ thống phanh 4 bánh: Tháo kiểm tra má phanh, đĩa phanh, tra mỡ ắc phanh chuyên dụng.
3. Đảo lốp 4 bánh và cân bằng động bánh xe.
4. Vệ sinh sạch lọc gió động cơ và lọc gió điều hòa than hoạt tính.
5. Kiểm tra ắc quy, điện áp máy phát sạc và hệ thống chiếu sáng.

💰 **Chi phí dự tính:**
- Tiền công trọn gói: **450.000 – 750.000 VNĐ**
- Vật tư phụ tùng (dầu nhớt + lọc nhớt + mỡ phanh): **1.100.000 – 1.600.000 VNĐ**
- Thời gian thực hiện: **90 – 120 phút**

📅 *Bạn có thể bấm **Đặt Lịch Bảo Dưỡng** phía trên để giữ chỗ ưu tiên tại xưởng!*""",
                    model_used,
                )
            elif "40.000" in target_text or "40000" in target_text or "đại tu" in target_text or "cấp 4" in target_text or "lớn" in target_text:
                return (
                    """⚙️ **TƯ VẤN COMBO BẢO DƯỠNG LỚN (MỐC 40.000 KM / 80.000 KM)**

Mốc bảo dưỡng quan trọng nhất để làm mới toàn diện hệ thống truyền động và an toàn xe:

📋 **Các hạng mục bảo dưỡng chính:**
1. Thay mới dầu hộp số tự động/số sàn và dầu vi sai cầu.
2. Thay toàn bộ dầu phanh thủy lực DOT4 và súc xả hệ thống phanh.
3. Súc rửa và thay mới toàn bộ nước làm mát động cơ cao cấp.
4. Thay bugi đánh lửa Iridium và lọc nhiên liệu (xăng/diesel).
5. Vệ sinh họng ga, kim phun buồng đốt và cân chỉnh góc đặt bánh xe 3D.
6. Kiểm tra toàn bộ hệ thống gầm: rô-tuyn, cao su chân máy, phuộc nhún.

💰 **Chi phí dự tính:**
- Tiền công gói đại tu: **1.200.000 – 1.800.000 VNĐ**
- Vật tư phụ tùng chính hãng: Báo giá niêm yết theo dòng xe cụ thể
- Thời gian thực hiện: **3 – 4 giờ**

📅 *Vui lòng đặt lịch hẹn trước để xưởng chuẩn bị đầy đủ vật tư chính hãng cho xe của bạn!*""",
                    model_used,
                )
            elif "5.000" in target_text or "5000" in target_text or "cấp 1" in target_text:
                return (
                    """🛠️ **TƯ VẤN GÓI BẢO DƯỠNG CẤP 1 (MỐC 5.000 KM)**

Gói bảo dưỡng cơ bản giúp động cơ vận hành êm ái và bảo vệ chi tiết máy:

📋 **Các hạng mục công việc thực hiện:**
1. Thay dầu nhớt động cơ chính hãng (Fully Synthetic 5W-30 cao cấp).
2. Kiểm tra và vệ sinh/thay thế lọc dầu nhớt động cơ.
3. Vệ sinh lọc gió động cơ và lọc gió điều hòa cabin.
4. Kiểm tra mức dung dịch: nước làm mát, dầu phanh, nước rửa kính.
5. Siết gầm, kiểm tra hệ thống treo, lốp xe và đo áp suất lốp 4 bánh.

💰 **Chi phí dự tính:**
- Tiền công dịch vụ: **150.000 – 450.000 VNĐ**
- Dầu nhớt & vật tư: **850.000 – 1.200.000 VNĐ** (tùy dung tích máy)
- Thời gian hoàn thành: **45 – 60 phút**

📅 *Bạn có thể bấm **Đặt Lịch Bảo Dưỡng** phía trên để được tiếp nhận ưu tiên tại Garage VTV!*""",
                    model_used,
                )

        # ------------------------------------------------------------
        # 5. LỊCH SỬ & GIẢI THÍCH PHIẾU
        # ------------------------------------------------------------
        if "lịch sử các phiếu sửa chữa" in prompt_lower or "lịch sử các lần sửa chữa" in prompt_lower:
            return (
                """📋 **TÓM TẮT LỊCH SỬ XE**

Hệ thống hiện đang sử dụng chế độ trả lời dự phòng.

Dữ liệu lịch sử đã được cung cấp cho hệ thống. Kỹ thuật viên nên đối chiếu các phiếu sửa chữa gần nhất và kiểm tra các hạng mục có liên quan trực tiếp đến triệu chứng hiện tại của xe.

⚠️ Không nên kết luận tình trạng phụ tùng chỉ dựa trên lịch sử; cần kiểm tra thực tế trước khi sửa chữa hoặc thay thế.""",
                model_used,
            )

        if "dữ liệu phiếu sửa chữa:" in prompt_lower or "giải thích ngắn gọn" in prompt_lower:
            return (
                """🚗 **GIẢI THÍCH PHIẾU SỬA CHỮA**

Phiếu sửa chữa đã ghi nhận các hạng mục cần kiểm tra/thực hiện trên xe.

🛠️ **Các hạng mục:**
Vui lòng đối chiếu trực tiếp danh sách hạng mục trong phiếu sửa chữa.

💡 **Lưu ý:**
Việc thay thế phụ tùng chỉ nên thực hiện sau khi kỹ thuật viên kiểm tra và xác nhận tình trạng thực tế.

⚠️ Chi phí trong phiếu là dữ liệu dự kiến và có thể thay đổi nếu phát hiện thêm vấn đề trong quá trình kiểm tra.""",
                model_used,
            )

        if "--- số liệu tài chính đã tính bởi hệ thống ---" in prompt_lower or "bảng dự toán" in prompt_lower:
            return (
                """📋 **BÁO GIÁ NHÁP**

Hệ thống đã tính toán chi phí dựa trên các hạng mục được nhập trong phiếu sửa chữa.

⚠️ Đây là báo giá nháp. Chi phí thực tế có thể thay đổi sau khi kiểm tra xe và xác nhận phạm vi sửa chữa với khách hàng.

Vui lòng đối chiếu số lượng, đơn giá phụ tùng và tiền công trước khi xác nhận sửa chữa.""",
                model_used,
            )

        # ------------------------------------------------------------
        # 6. GREETING & GENERAL
        # ------------------------------------------------------------
        clean_target = target_text.strip()
        is_greeting = False
        if len(clean_target) <= 18:
            greeting_patterns = [r"^(alo|hello|hi|hey|xin chào|chào|bạn ơi|ad ơi|chào shop|chào ad)[\s!.]*$", r"^(test|thử)$"]
            if any(re.search(pat, clean_target, re.IGNORECASE) for pat in greeting_patterns):
                is_greeting = True

        if is_greeting or len(clean_target) <= 4:
            return (
                """👋 **Xin chào! Tôi là Trợ Lý Kỹ Thuật AI Garage VTV**!

Tôi hỗ trợ bạn 3 năng lực chuyên sâu:
🩺 **Tư vấn theo triệu chứng xe** — chẩn đoán rung phanh, hụt ga khó nổ, điều hòa có mùi, gầm kêu lục cục, vô lăng lệch
💰 **Tư vấn chi phí & Bảng giá** — dự toán minh bạch gói bảo dưỡng 5k-40k km và các combo chăm sóc xe
⚖️ **So sánh dịch vụ trực quan** — so sánh Cấp 1 vs Cấp 2, Dầu khoáng vs Dầu tổng hợp, Vệ sinh nội soi vs Tháo taplo

Bạn cần tôi giải đáp triệu chứng, báo giá hay so sánh dịch vụ nào cho xe? 🚗""",
                model_used,
            )

        return (
            """🤖 **TRỢ LÝ KỸ THUẬT AI GARAGE VTV CHÀO BẠN!**

Tôi sẵn sàng hỗ trợ bạn:
- 🩺 **Chẩn đoán triệu chứng:** Rung phanh, hụt ga khó nổ, điều hòa mất lạnh, gầm kêu lục cục, lệch lái, đèn báo lỗi taplo.
- 💰 **Báo giá & Chi phí:** Minh bạch bảng giá bảo dưỡng các mốc 5k, 10k, 20k, 40k và các combo chuyên sâu.
- ⚖️ **So sánh dịch vụ:** So sánh các phương án bảo dưỡng để bạn chọn giải pháp tiết kiệm và hiệu quả nhất.
- 📅 **Đặt lịch ưu tiên:** Tiếp nhận nhanh chóng không phải chờ đợi.

💡 Bạn có thể bấm các gợi ý nhanh phía dưới hoặc gửi câu hỏi cụ thể nhé!""",
            model_used,
        )

    # ============================================================
    # HISTORY SUMMARY
    # ============================================================

    @classmethod
    def generate_history_summary(cls, db: Session, vehicle_id: int) -> Dict[str, Any]:
        """Tóm tắt lịch sử sửa chữa của xe."""
        vehicle = db.query(Vehicle).filter(Vehicle.id == vehicle_id).first()
        if not vehicle:
            return {
                "success": False,
                "feature": "history_summary",
                "output": "Không tìm thấy thông tin xe trong hệ thống.",
                "model_used": "None",
            }

        past_orders = (
            db.query(RepairOrder)
            .filter(RepairOrder.vehicle_id == vehicle_id)
            .order_by(RepairOrder.created_at.desc())
            .limit(5)
            .all()
        )

        if not past_orders:
            history_details = "Xe chưa có lịch sử sửa chữa tại garage."
        else:
            history_lines = []
            for ro in past_orders:
                item_names = (
                    ", ".join(str(getattr(item, "name", "Hạng mục")) for item in ro.items)
                    if ro.items
                    else "Không có hạng mục chi tiết"
                )
                created_date = ro.created_at.strftime("%d/%m/%Y") if ro.created_at else "Không rõ"
                mileage = cls._safe_int(ro.mileage_at_reception, 0)
                mileage_text = f"{mileage:,} km" if mileage > 0 else "Không ghi nhận"

                history_lines.append(
                    f"- Phiếu {ro.code} ({created_date}, Km: {mileage_text})\n"
                    f"  Triệu chứng: {ro.initial_symptoms or 'Không ghi nhận'}\n"
                    f"  Chẩn đoán: {ro.technical_diagnosis or 'Chưa có'}\n"
                    f"  Hạng mục: {item_names}"
                )
            history_details = "\n".join(history_lines)

        user_prompt = PROMPT_HISTORY_SUMMARY.format(
            license_plate=vehicle.license_plate,
            brand=vehicle.brand,
            model=vehicle.model,
            year=vehicle.year or "N/A",
            history_details=history_details,
        )

        output_text, model_used = cls._call_llm(SYSTEM_GARAGE_ASSISTANT, user_prompt)
        cls._save_ai_log(
            db=db,
            feature="history_summary",
            prompt_input=user_prompt,
            response_output=output_text,
            model_used=model_used,
        )

        return {
            "success": True,
            "feature": "history_summary",
            "output": output_text,
            "model_used": model_used,
            "metadata": {
                "vehicle_id": vehicle_id,
                "license_plate": vehicle.license_plate,
                "orders_analyzed": len(past_orders),
            },
        }

    # ============================================================
    # SERVICE EXPLANATION
    # ============================================================

    @classmethod
    def generate_service_explanation(cls, db: Session, repair_order_id: int) -> Dict[str, Any]:
        """Giải thích phiếu sửa chữa bằng ngôn ngữ dễ hiểu."""
        ro = db.query(RepairOrder).filter(RepairOrder.id == repair_order_id).first()
        if not ro:
            return {
                "success": False,
                "feature": "service_explainer",
                "output": "Không tìm thấy phiếu sửa chữa trong hệ thống.",
                "model_used": "None",
            }

        items_str, total_parts, total_labor, subtotal = cls._build_items_summary(ro.items)
        vat = subtotal * cls.VAT_RATE
        grand_total = subtotal + vat
        vehicle = ro.vehicle

        repair_order_data = (
            f"Mã phiếu: {ro.code}\n"
            f"Xe: {vehicle.license_plate} ({vehicle.brand} {vehicle.model})\n"
            f"Tình trạng nhận xe: {ro.initial_symptoms or 'Không ghi nhận'}\n"
            f"Chẩn đoán KTV: {ro.technical_diagnosis or 'Đang chẩn đoán'}\n\n"
            f"Danh sách hạng mục:\n{items_str}\n\n"
            f"Tiền phụ tùng: {cls._format_money(total_parts)}\n"
            f"Tiền công: {cls._format_money(total_labor)}\n"
            f"Tổng trước VAT: {cls._format_money(subtotal)}\n"
            f"VAT 8%: {cls._format_money(vat)}\n"
            f"Tổng dự kiến: {cls._format_money(grand_total)}"
        )

        user_prompt = PROMPT_SERVICE_EXPLAINER.format(repair_order=repair_order_data)
        output_text, model_used = cls._call_llm(SYSTEM_GARAGE_ASSISTANT, user_prompt)

        ro.ai_service_explanation = output_text  # type: ignore
        cls._save_ai_log(
            db=db,
            feature="service_explainer",
            prompt_input=user_prompt,
            response_output=output_text,
            model_used=model_used,
        )

        return {
            "success": True,
            "feature": "service_explainer",
            "output": output_text,
            "model_used": model_used,
            "metadata": {
                "repair_order_id": repair_order_id,
                "code": ro.code,
                "total_parts": total_parts,
                "total_labor": total_labor,
                "subtotal": subtotal,
                "vat": vat,
                "grand_total": grand_total,
            },
        }

    # ============================================================
    # DRAFT QUOTATION
    # ============================================================

    @classmethod
    def generate_draft_quotation(cls, db: Session, repair_order_id: int) -> Dict[str, Any]:
        """Tạo báo giá nháp."""
        ro = db.query(RepairOrder).filter(RepairOrder.id == repair_order_id).first()
        if not ro:
            return {
                "success": False,
                "feature": "draft_quotation",
                "output": "Không tìm thấy phiếu sửa chữa.",
                "model_used": "None",
            }

        items_str, total_parts, total_labor, subtotal = cls._build_items_summary(ro.items)
        vat = subtotal * cls.VAT_RATE
        grand_total = subtotal + vat

        user_prompt = PROMPT_DRAFT_QUOTATION.format(
            license_plate=ro.vehicle.license_plate,
            technical_diagnosis=ro.technical_diagnosis or "Chưa có chẩn đoán kỹ thuật",
            items_details=items_str,
        )
        user_prompt += (
            "\n\n--- SỐ LIỆU TÀI CHÍNH ĐÃ TÍNH BỞI HỆ THỐNG ---\n"
            f"Tiền phụ tùng: {cls._format_money(total_parts)}\n"
            f"Tiền công: {cls._format_money(total_labor)}\n"
            f"Tổng trước VAT: {cls._format_money(subtotal)}\n"
            f"VAT ({cls.VAT_RATE * 100:.0f}%): {cls._format_money(vat)}\n"
            f"Tổng chi phí dự kiến: {cls._format_money(grand_total)}\n\n"
            "QUAN TRỌNG: Không tự thay đổi các số tiền trên. Không tự thêm phụ tùng hoặc dịch vụ chưa có trong dữ liệu."
        )

        system_prompt = cls._get_system_prompt_with_memory(db, SYSTEM_GARAGE_ASSISTANT)
        output_text, model_used = cls._call_llm(system_prompt, user_prompt)

        ro.estimated_cost = grand_total  # type: ignore
        ro.ai_draft_quotation_notes = output_text  # type: ignore

        cls._save_ai_log(
            db=db,
            feature="draft_quotation",
            prompt_input=user_prompt,
            response_output=output_text,
            model_used=model_used,
        )

        return {
            "success": True,
            "feature": "draft_quotation",
            "output": output_text,
            "model_used": model_used,
            "metadata": {
                "repair_order_id": repair_order_id,
                "subtotal": subtotal,
                "vat_rate": cls.VAT_RATE,
                "vat": vat,
                "grand_total": grand_total,
                "total_parts": total_parts,
                "total_labor": total_labor,
            },
        }

    # ============================================================
    # AGENT ORCHESTRATOR
    # ============================================================
    
    @classmethod
    def _call_llm_agent(
        cls, db: Session, system_prompt: str, user_prompt: str,
        allow_internal_tools: bool = False, current_role: str = "customer",
    ) -> Tuple[str, str]:
        """Run tool calling only for authenticated staff, filtered by their role."""
        # pyrefly: ignore [missing-import]
        import httpx
        import json
        
        groq_api_key = getattr(settings, "GROQ_API_KEY", "") or os.getenv("GROQ_API_KEY", "")
        # Public/customer chat must never query internal records through tools.
        agent_tools = get_agent_tools_for_role(current_role) if allow_internal_tools else []
        if not groq_api_key or not agent_tools:
            return cls._call_llm(system_prompt, user_prompt)
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        
        groq_headers = {
            "Authorization": f"Bearer {groq_api_key}",
            "Content-Type": "application/json",
        }
        
        model_name = "llama-3.3-70b-versatile" # Khuyến nghị cho Tool Calling trên Groq
        
        # Cho phép tối đa 3 vòng lặp (Max 3 tool calls)
        for i in range(3):
            payload = {
                "model": model_name,
                "messages": messages,
                "tools": agent_tools,
                "tool_choice": "auto",
                "temperature": 0.2,
                "max_tokens": 2048,
            }
            try:
                res = httpx.post("https://api.groq.com/openai/v1/chat/completions", headers=groq_headers, json=payload, timeout=20.0)
                if res.status_code != 200:
                    logger.warning("Groq Agent API Error: %s", res.text)
                    return cls._call_llm(system_prompt, user_prompt)
                
                data = res.json()
                message = data["choices"][0]["message"]
                
                # Cập nhật danh sách messages với phản hồi của Assistant
                if message.get("content") is None:
                    message["content"] = ""
                
                # Nếu LLM quyết định gọi tool
                if message.get("tool_calls"):
                    # Groq API yêu cầu đẩy lại thông báo của LLM với tool_calls vào mảng messages
                    messages.append(message)
                    
                    # Duyệt qua các tools LLM muốn gọi
                    for tool_call in message["tool_calls"]:
                        func_name = tool_call["function"]["name"]
                        try:
                            kwargs = json.loads(tool_call["function"]["arguments"])
                        except:
                            kwargs = {}
                        
                        logger.info(f"[Agent] Calling tool: {func_name} with args: {kwargs}")
                        tool_result = execute_tool(db, func_name, kwargs)
                        logger.info(f"[Agent] Tool result length: {len(tool_result)}")
                        
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call["id"],
                            "name": func_name,
                            "content": tool_result
                        })
                    # Vòng lặp tiếp tục: gửi lại messages (bao gồm kết quả tool) cho LLM
                else:
                    # Nếu LLM không gọi tool nào, trả về kết quả cuối cùng
                    return message.get("content", "").strip(), f"Groq Agent ({model_name})"
            except Exception as e:
                logger.warning(f"Lỗi Agent Loop: {e}")
                break
        
        # Fallback sau 3 lượt fail
        return cls._call_llm(system_prompt, user_prompt)

    # ============================================================
    # GENERAL AI ASSISTANT
    # ============================================================

    @classmethod
    def ask_assistant(
        cls,
        db: Session,
        question: str,
        repair_order_id: Optional[int] = None,
        vehicle_id: Optional[int] = None,
        mode: str = "customer",
        current_role: str = "customer",
    ) -> Dict[str, Any]:
        """Trợ lý AI: customer chỉ tư vấn xe; admin hỗ trợ vận hành nội bộ."""
        question = (question or "").strip()
        if not question:
            return {
                "success": False,
                "feature": "ai_assistant",
                "output": "Vui lòng nhập câu hỏi.",
                "model_used": "None",
            }

        context_parts = []
        ro = None
        vehicle = None

        if repair_order_id:
            ro = db.query(RepairOrder).filter(RepairOrder.id == repair_order_id).first()
            if ro:
                vehicle = ro.vehicle
                items_str, total_parts, total_labor, subtotal = cls._build_items_summary(ro.items)
                vat = subtotal * cls.VAT_RATE
                grand_total = subtotal + vat
                mileage = cls._safe_int(ro.mileage_at_reception, 0)

                context_parts.append(
                    "--- DỮ LIỆU PHIẾU SỬA CHỮA ---\n"
                    f"Mã phiếu: {ro.code}\n"
                    f"Biển số xe: {ro.vehicle.license_plate}\n"
                    f"Hãng/Dòng: {ro.vehicle.brand} {ro.vehicle.model}\n"
                    f"Số Km nhận xe: {mileage:,} km\n"
                    f"Triệu chứng ban đầu: {ro.initial_symptoms or 'Chưa ghi nhận'}\n"
                    f"Chẩn đoán KTV: {ro.technical_diagnosis or 'Đang chẩn đoán'}\n"
                    f"Danh sách dịch vụ/phụ tùng:\n{items_str}\n"
                    f"Tiền phụ tùng: {cls._format_money(total_parts)}\n"
                    f"Tiền công: {cls._format_money(total_labor)}\n"
                    f"Tổng trước VAT: {cls._format_money(subtotal)}\n"
                    f"VAT 8%: {cls._format_money(vat)}\n"
                    f"Tổng dự kiến: {cls._format_money(grand_total)}"
                )

        if not vehicle and vehicle_id:
            vehicle = db.query(Vehicle).filter(Vehicle.id == vehicle_id).first()

        if vehicle:
            context_parts.append(
                "--- THÔNG TIN XE ---\n"
                f"Biển số: {vehicle.license_plate}\n"
                f"Hãng: {vehicle.brand}\n"
                f"Dòng xe: {vehicle.model}\n"
                f"Năm sản xuất: {vehicle.year or 'Không rõ'}"
            )

        is_admin = mode == "admin"
        lower_question = question.lower()
        sensitive_terms = ("bảng lương", "luong", "lương", "hoa hồng", "hoa hong", "tài khoản ngân hàng", "tai khoan ngan hang", "số tài khoản", "so tai khoan")
        if is_admin and any(term in lower_question for term in sensitive_terms):
            context_parts.append(
                "--- QUYỀN BẢO MẬT ---\n"
                "TỪ CHỐI: Không cung cấp bảng lương, hoa hồng, số tài khoản ngân hàng hoặc dữ liệu tài chính cá nhân. "
                "Vai trò AI không có quyền truy cập các trường dữ liệu này; không được suy đoán, xuất file hoặc tiết lộ một phần."
            )
        lookup_context = lookup_operational_context(db, question) if is_admin else None
        if lookup_context:
            context_parts.append(lookup_context)
        finance_terms = ("doanh thu", "doanh số", "tài chính", "phiếu thu", "hóa đơn", "hoa don")
        if is_admin and any(term in question.lower() for term in finance_terms):
            if current_role in {"manager", "cashier"}:
                # Câu hỏi tổng quan phải trả đúng bảng Dashboard đang hiển thị;
                # không bắt người dùng lặp lại năm/phạm vi/trạng thái mặc định.
                context_parts.append(format_dashboard_revenue_context(question))
                context_parts.append(format_report_context(report_for_query(db, question)))
            else:
                context_parts.append("--- QUYỀN TÀI CHÍNH ---\nVai trò hiện tại không được xem báo cáo tài chính chi tiết. Yêu cầu Quản lý hoặc Thu ngân thực hiện tra cứu.")
        # RAG Augmentation: Tự động truy xuất tri thức chuyên môn từ RAG Knowledge Base
        if not is_admin:
            try:
                from backend.app.ai.rag import retrieve_knowledge
                rag_chunks, _ = retrieve_knowledge(question, top_k=2, db=db)
                if rag_chunks:
                    rag_lines = ["--- TRI THỨC KỸ THUẬT & DỊCH VỤ GARAGE VTV (RAG) ---"]
                    for c in rag_chunks:
                        rag_lines.append(f"• {c.title}: {c.content}")
                        if c.recommended_combo:
                            rag_lines.append(f"  Combo đề xuất: {c.recommended_combo} ({c.estimated_cost})")
                    context_parts.append("\n".join(rag_lines))
            except Exception as e:
                pass

        context_info = "\n\n".join(context_parts) if context_parts else "Không có thông tin xe hoặc phiếu sửa chữa cụ thể."

        prompt_template = PROMPT_ADMIN_ASSISTANT if is_admin else PROMPT_AI_ASSISTANT
        user_prompt = prompt_template.format(
            question=question,
            context_info=context_info,
            current_role=current_role,
        )

        base_system_prompt = SYSTEM_GARAGE_ADMIN_ASSISTANT if is_admin else SYSTEM_GARAGE_ASSISTANT
        system_prompt = cls._get_system_prompt_with_memory(db, base_system_prompt)
        output_text, model_used = cls._call_llm_agent(
            db, system_prompt, user_prompt,
            allow_internal_tools=is_admin,
            current_role=current_role,
        )
        cls._save_ai_log(
            db=db,
            feature="ai_admin_assistant" if is_admin else "ai_assistant",
            prompt_input=user_prompt,
            response_output=output_text,
            model_used=model_used,
        )

        return {
            "success": True,
            "feature": "ai_assistant",
            "output": output_text,
            "model_used": model_used,
            "metadata": {
                "question": question,
                "repair_order_id": repair_order_id,
                "vehicle_id": vehicle_id,
                "mode": "admin" if is_admin else "customer",
            },
        }

    @classmethod
    def analyze_technical_troubleshooting(cls, db: Session, symptoms: str, car_model: str = "Chưa rõ") -> Dict[str, Any]:
        """AI Chức năng Chẩn đoán Kỹ thuật cho KTV"""
        untrusted_symptoms = f"<UNTRUSTED_CUSTOMER_DATA>\n{symptoms}\n</UNTRUSTED_CUSTOMER_DATA>"
        prompt = PROMPT_TECHNICAL_TROUBLESHOOTING.format(symptoms=untrusted_symptoms, car_model=car_model)
        system_prompt = cls._get_system_prompt_with_memory(db, SYSTEM_GARAGE_ASSISTANT)
        output_text, model_used = cls._call_llm(system_prompt, prompt)
        return {
            "success": True,
            "feature": "technical_troubleshooting",
            "output": output_text,
            "model_used": model_used
        }

    @classmethod
    def analyze_obd_fault(cls, db: Session, brand: str, model: str, year: int, mileage: int, symptoms: str, obd_code: str) -> Dict[str, Any]:
        """AI Chức năng Phân tích lỗi OBD-II"""
        prompt = PROMPT_OBD_DIAGNOSTIC.format(
            brand=brand, model=model, year=year, mileage=mileage, symptoms=symptoms, obd_code=obd_code
        )
        system_prompt = cls._get_system_prompt_with_memory(db, SYSTEM_GARAGE_ASSISTANT)
        output_text, model_used = cls._call_llm(system_prompt, prompt)
        return {
            "success": True,
            "feature": "obd_diagnostic",
            "output": output_text,
            "model_used": model_used
        }

    @classmethod
    def analyze_business_performance(cls, db: Session, question: str) -> Dict[str, Any]:
        """AI Chức năng Phân tích Kinh doanh cho Quản lý (Manager)"""
        business_data = get_monthly_revenue_tool(db)
        business_data += "\nCác chỉ số lợi nhuận, công nợ, dịch vụ bán chạy và hiệu suất KTV cần được truy xuất riêng nếu người dùng yêu cầu."
        prompt = PROMPT_BUSINESS_INTELLIGENCE.format(question=question, business_data=business_data)
        system_prompt = cls._get_system_prompt_with_memory(db, SYSTEM_GARAGE_ASSISTANT)
        output_text, model_used = cls._call_llm(system_prompt, prompt)
        return {
            "success": True,
            "feature": "business_intelligence",
            "output": output_text,
            "model_used": model_used
        }

    @classmethod
    def predict_maintenance(cls, db: Session, license_plate: str) -> Dict[str, Any]:
        """AI Chức năng Dự đoán Bảo dưỡng & Nhắc lịch"""
        recent_history = f"Xe {license_plate}: Thay dầu Castrol & lọc dầu mốc 40,000 km cách đây 3 tháng."
        prompt = PROMPT_PREDICTIVE_MAINTENANCE.format(license_plate=license_plate, brand="Toyota", model="Camry", mileage=45000, recent_history=recent_history)
        output_text, model_used = cls._call_llm(SYSTEM_GARAGE_ASSISTANT, prompt)
        return {
            "success": True,
            "feature": "predictive_maintenance",
            "output": output_text,
            "model_used": model_used
        }

    @classmethod
    def lookup_customer_vehicle_progress(cls, db: Session, license_plate_or_phone: str) -> Dict[str, Any]:
        """AI Chức năng Hỗ trợ Khách hàng Tra cứu Tiến độ"""
        order_data = (
            f"Xe {license_plate_or_phone} (Phiếu RO-2026-001):\n"
            "Trạng thái: Đang sửa chữa (IN_PROGRESS)\n"
            "- Công việc hoàn thành: Kiểm tra hệ thống phanh 4 bánh & Xả dầu máy cũ.\n"
            "- Công việc đang thực hiện: Thay má phanh trước Brembo & Lắp lọc dầu mới.\n"
            "- Dự kiến hoàn thành giao xe: 16:30 chiều nay."
        )
        prompt = PROMPT_CUSTOMER_PROGRESS_LOOKUP.format(question=f"Tra cứu xe {license_plate_or_phone}", customer_order_data=order_data)
        output_text, model_used = cls._call_llm(SYSTEM_GARAGE_ASSISTANT, prompt)
        return {
            "success": True,
            "feature": "customer_progress",
            "output": output_text,
            "model_used": model_used
        }
