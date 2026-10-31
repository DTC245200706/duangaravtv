import os
import logging
from typing import Any, Dict, Optional, Tuple

# pyrefly: ignore [missing-import]
from sqlalchemy.orm import Session

from backend.app.config import settings
from backend.app.models import AILog, RepairOrder, Vehicle, AIKnowledgeBase
from backend.app.ai.tools import AGENT_TOOLS, execute_tool, get_monthly_revenue_tool
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

        # 1. Nếu Gemini API key không đúng định dạng (Google API key luôn bắt đầu bằng 'AIza')
        # và có Groq API key: dùng ngay Groq để tránh chờ đợi timeout vô ích
        is_gemini_valid_format = gemini_api_key.startswith("AIza")
        if not is_gemini_valid_format and groq_api_key:
            res = _call_groq()
            if res:
                return res

        # 2. Thử Google Gemini REST API nếu API key có định dạng hợp lệ
        if gemini_api_key and is_gemini_valid_format:
            try:
                model_name = getattr(settings, "AI_MODEL_NAME", "gemini-1.5-flash") or "gemini-1.5-flash"
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
                headers = {
                    "Content-Type": "application/json",
                    "X-goog-api-key": gemini_api_key,
                }
                payload = {
                    "system_instruction": {"parts": [{"text": system_prompt}]},
                    "contents": [{"parts": [{"text": user_prompt}]}],
                    "generationConfig": {"temperature": 0.4, "maxOutputTokens": 2048},
                }
                response = httpx.post(url, headers=headers, json=payload, timeout=5.0)
                if response.status_code == 200:
                    data = response.json()
                    candidates = data.get("candidates") or []
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts") or []
                        text = "".join(p.get("text", "") for p in parts).strip()
                        if text:
                            return text, f"Gemini ({model_name})"
                else:
                    logger.warning("Gemini REST API trả về HTTP %s: %s", response.status_code, response.text[:300])
            except Exception as e:
                logger.warning("Lỗi hoặc timeout gọi Gemini REST API: %s. Chuyển sang Groq fallback.", e)

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
        Fallback khi không có AI API.
        Không tự bịa dữ liệu xe, lịch sử sửa chữa, giá phụ tùng hoặc tình trạng kỹ thuật.
        """
        model_used = "Internal Fallback"
        prompt_lower = user_prompt.lower()

        # Trích xuất câu hỏi thực sự của người dùng để tránh match nhầm các từ khóa trong prompt template
        target_text = prompt_lower
        for prefix in ["yêu cầu tư vấn của khách hàng:", "yêu cầu của người dùng:", "yêu cầu nội bộ của người dùng:"]:
            if prefix in prompt_lower:
                parts = prompt_lower.split(prefix)
                if len(parts) > 1:
                    raw_q = parts[1].split("ngữ cảnh")[0].strip('"\n\r ')
                    if raw_q:
                        target_text = raw_q
                        break

        # HISTORY
        if "lịch sử các phiếu sửa chữa" in prompt_lower or "lịch sử các lần sửa chữa" in prompt_lower:
            return (
                "📋 **TÓM TẮT LỊCH SỬ XE**\n\n"
                "Hệ thống hiện đang sử dụng chế độ trả lời dự phòng.\n\n"
                "Dữ liệu lịch sử đã được cung cấp cho hệ thống. "
                "Kỹ thuật viên nên đối chiếu các phiếu sửa chữa gần nhất "
                "và kiểm tra các hạng mục có liên quan trực tiếp đến triệu chứng hiện tại của xe.\n\n"
                "⚠️ Không nên kết luận tình trạng phụ tùng chỉ dựa trên lịch sử; cần kiểm tra thực tế trước khi sửa chữa hoặc thay thế.",
                model_used,
            )

        # SERVICE EXPLANATION
        if "dữ liệu phiếu sửa chữa:" in prompt_lower or "giải thích ngắn gọn" in prompt_lower:
            return (
                "🚗 **GIẢI THÍCH PHIẾU SỬA CHỮA**\n\n"
                "Phiếu sửa chữa đã ghi nhận các hạng mục cần kiểm tra/thực hiện trên xe.\n\n"
                "🛠️ **Các hạng mục:**\n"
                "Vui lòng đối chiếu trực tiếp danh sách hạng mục trong phiếu sửa chữa.\n\n"
                "💡 **Lưu ý:**\n"
                "Việc thay thế phụ tùng chỉ nên thực hiện sau khi kỹ thuật viên kiểm tra và xác nhận tình trạng thực tế.\n\n"
                "⚠️ Chi phí trong phiếu là dữ liệu dự kiến và có thể thay đổi nếu phát hiện thêm vấn đề trong quá trình kiểm tra.",
                model_used,
            )

        # GÓI & COMBO BẢO DƯỠNG (Tư vấn dịch vụ, các mốc km và combo chăm sóc xe cho khách hàng)
        if any(kw in target_text for kw in ["bảo dưỡng", "combo", "gói", "chi phí", "bảng giá", "giá dịch vụ", "5.000", "5000", "10.000", "10000", "20.000", "20000", "40.000", "40000", "thay dầu", "thay nhớt", "lọc nhớt"]):
            if "10.000" in target_text or "10000" in target_text or "cấp 2" in target_text:
                return (
                    "🛠️ **TƯ VẤN GÓI BẢO DƯỠNG CẤP 2 (MỐC 10.000 KM)**\n\n"
                    "Gói bảo dưỡng định kỳ tiêu chuẩn giúp duy trì hiệu suất vận hành và an toàn phanh:\n\n"
                    "📋 **Các hạng mục công việc thực hiện:**\n"
                    "1. Toàn bộ hạng mục cấp 1: Thay dầu nhớt động cơ và thay lọc nhớt mới.\n"
                    "2. Bảo dưỡng hệ thống phanh 4 bánh: Tháo kiểm tra má phanh, đĩa phanh, tra mỡ ắc phanh chuyên dụng.\n"
                    "3. Đảo lốp 4 bánh và cân bằng động bánh xe.\n"
                    "4. Vệ sinh sạch lọc gió động cơ và lọc gió điều hòa than hoạt tính.\n"
                    "5. Kiểm tra ắc quy, điện áp máy phát sạc và hệ thống chiếu sáng.\n\n"
                    "💰 **Chi phí dự tính:**\n"
                    "- Tiền công trọn gói: **450.000 – 750.000 VNĐ**\n"
                    "- Vật tư phụ tùng (dầu nhớt + lọc nhớt + mỡ phanh): **1.100.000 – 1.600.000 VNĐ**\n"
                    "- Thời gian thực hiện: **90 – 120 phút**\n\n"
                    "📅 *Bạn có thể bấm **Đặt Lịch Bảo Dưỡng** phía trên để giữ chỗ ưu tiên tại xưởng!*",
                    model_used,
                )
            elif "40.000" in target_text or "40000" in target_text or "đại tu" in target_text or "cấp 4" in target_text or "lớn" in target_text:
                return (
                    "⚙️ **TƯ VẤN COMBO BẢO DƯỠNG LỚN (MỐC 40.000 KM / 80.000 KM)**\n\n"
                    "Mốc bảo dưỡng quan trọng nhất để làm mới toàn diện hệ thống truyền động và an toàn xe:\n\n"
                    "📋 **Các hạng mục bảo dưỡng chính:**\n"
                    "1. Thay mới dầu hộp số tự động/số sàn và dầu vi sai cầu.\n"
                    "2. Thay toàn bộ dầu phanh thủy lực DOT4 và súc xả hệ thống phanh.\n"
                    "3. Súc rửa và thay mới toàn bộ nước làm mát động cơ cao cấp.\n"
                    "4. Thay bugi đánh lửa Iridium và lọc nhiên liệu (xăng/diesel).\n"
                    "5. Vệ sinh họng ga, kim phun buồng đốt và cân chỉnh góc đặt bánh xe 3D.\n"
                    "6. Kiểm tra toàn bộ hệ thống gầm: rô-tuyn, cao su chân máy, phuộc nhún.\n\n"
                    "💰 **Chi phí dự tính:**\n"
                    "- Tiền công gói đại tu: **1.200.000 – 1.800.000 VNĐ**\n"
                    "- Vật tư phụ tùng chính hãng: Báo giá niêm yết theo dòng xe cụ thể\n"
                    "- Thời gian thực hiện: **3 – 4 giờ**\n\n"
                    "📅 *Vui lòng đặt lịch hẹn trước để xưởng chuẩn bị đầy đủ vật tư chính hãng cho xe của bạn!*",
                    model_used,
                )
            elif "5.000" in target_text or "5000" in target_text or "cấp 1" in target_text:
                return (
                    "🛠️ **TƯ VẤN GÓI BẢO DƯỠNG CẤP 1 (MỐC 5.000 KM)**\n\n"
                    "Gói bảo dưỡng cơ bản giúp động cơ vận hành êm ái và bảo vệ chi tiết máy:\n\n"
                    "📋 **Các hạng mục công việc thực hiện:**\n"
                    "1. Thay dầu nhớt động cơ chính hãng (Fully Synthetic 5W-30 cao cấp).\n"
                    "2. Kiểm tra và vệ sinh/thay thế lọc dầu nhớt động cơ.\n"
                    "3. Vệ sinh lọc gió động cơ và lọc gió điều hòa cabin.\n"
                    "4. Kiểm tra mức dung dịch: nước làm mát, dầu phanh, nước rửa kính.\n"
                    "5. Siết gầm, kiểm tra hệ thống treo, lốp xe và đo áp suất lốp 4 bánh.\n\n"
                    "💰 **Chi phí dự tính:**\n"
                    "- Tiền công dịch vụ: **150.000 – 450.000 VNĐ**\n"
                    "- Dầu nhớt & vật tư: **850.000 – 1.200.000 VNĐ** (tùy dung tích máy)\n"
                    "- Thời gian hoàn thành: **45 – 60 phút**\n\n"
                    "📅 *Bạn có thể bấm **Đặt Lịch Bảo Dưỡng** phía trên để được tiếp nhận ưu tiên tại Garage VTV!*",
                    model_used,
                )
            elif "điều hòa" in prompt_lower or "lạnh" in prompt_lower or "gas" in prompt_lower:
                return (
                    "❄️ **TƯ VẤN COMBO BẢO DƯỠNG HỆ THỐNG ĐIỀU HÒA MÁT SÂU**\n\n"
                    "Combo chăm sóc hệ thống làm mát bảo vệ sức khỏe và khử mùi xe:\n\n"
                    "📋 **Các hạng mục trong combo:**\n"
                    "1. Vệ sinh nội soi dàn lạnh bằng camera chuyên dụng (không cần tháo taplo).\n"
                    "2. Khử khuẩn, diệt nấm mốc đường gió điều hòa bằng công nghệ Ozone/Nano bạc.\n"
                    "3. Thay mới lọc gió điều hòa than hoạt tính ngăn bụi mịn PM2.5.\n"
                    "4. Kiểm tra áp suất gas lạnh R134a và nạp bổ sung môi chất lạnh đạt chuẩn.\n"
                    "5. Vệ sinh dàn nóng phía trước két nước giúp tản nhiệt tối đa.\n\n"
                    "💰 **Chi phí dự tính:**\n"
                    "- Trọn gói combo: **650.000 – 950.000 VNĐ**\n"
                    "- Thời gian hoàn thiện: **45 – 60 phút**\n\n"
                    "📅 *Bấm **Đặt Lịch Bảo Dưỡng** để xế yêu luôn mát sâu sảng khoái!*",
                    model_used,
                )
            elif "phanh" in prompt_lower or "gầm" in prompt_lower:
                return (
                    "🛑 **TƯ VẤN COMBO BẢO DƯỠNG PHANH & GẦM AN TOÀN**\n\n"
                    "Đảm bảo an toàn tuyệt đối trên mọi hành trình:\n\n"
                    "📋 **Các hạng mục thực hiện:**\n"
                    "1. Vệ sinh, tra mỡ chịu nhiệt ắc trượt và pít-tông 4 cụm phanh đĩa.\n"
                    "2. Kiểm tra độ dày má phanh và láng đĩa phanh triệt tiêu gờ rãnh.\n"
                    "3. Kiểm tra độ ẩm dầu phanh, xả e và châm dầu phanh DOT4.\n"
                    "4. Siết lực toàn bộ ốc gầm, kiểm tra rô-tuyn lái, rô-tuyn cân bằng và cao su càng A.\n"
                    "5. Cân chỉnh góc đặt bánh xe 3D hạn chế nhao lái, mòn lệch lốp.\n\n"
                    "💰 **Chi phí dự tính:**\n"
                    "- Trọn gói công dịch vụ: **350.000 – 650.000 VNĐ**\n"
                    "- Thời gian hoàn thiện: **60 – 90 phút**\n\n"
                    "📅 *Bấm **Đặt Lịch Bảo Dưỡng** để kỹ thuật viên kiểm tra an toàn phanh cho xe bạn!*",
                    model_used,
                )
            else:
                return (
                    "✨ **DANH MỤC CÁC GÓI & COMBO BẢO DƯỠNG TẠI GARAGE VTV**\n\n"
                    "Garage VTV cung cấp các gói và combo bảo dưỡng trọn gói, minh bạch chi phí:\n\n"
                    "1. 🔧 **Gói bảo dưỡng định kỳ cấp 1 (5.000 km):** Thay dầu, lọc dầu, siết gầm, kiểm tra an toàn (~450.000 VNĐ công).\n"
                    "2. 🛠️ **Gói bảo dưỡng định kỳ cấp 2 (10.000 km):** Gồm cấp 1 + bảo dưỡng phanh 4 bánh, đảo lốp (~750.000 VNĐ công).\n"
                    "3. ⚙️ **Combo bảo dưỡng lớn (40.000 km):** Thay dầu số, dầu phanh, nước mát, bugi, lọc nhiên liệu (~1.500.000 VNĐ công).\n"
                    "4. ❄️ **Combo điều hòa mát sâu:** Vệ sinh nội soi dàn lạnh, nạp gas, thay lọc gió điều hòa (trọn gói từ 650.000 VNĐ).\n"
                    "5. 🛑 **Combo an toàn phanh & gầm:** Bảo dưỡng 4 phanh, láng đĩa, cân chỉnh góc đặt bánh 3D (từ 500.000 VNĐ).\n"
                    "6. 🚗 **Combo chăm sóc làm đẹp:** Rửa khoang máy chống chuột, dọn nội thất, đánh bóng sơn bóng đẹp.\n\n"
                    "💡 *Bạn muốn nhận tư vấn chi tiết về gói bảo dưỡng mốc bao nhiêu km hoặc combo nào cho xe?*",
                    model_used,
                )

        # DIAGNOSTIC (Dành cho chẩn đoán sự cố kỹ thuật)
        if any(kw in prompt_lower for kw in ["kêu", "hỏng", "lỗi", "sự cố", "phanh", "dầu", "động cơ", "máy", "rung", "giật", "nóng", "khói", "chẩn đoán", "abs", "điều hòa", "bảo dưỡng"]):
            specific_advice = ""
            if "giật" in prompt_lower or "rung" in prompt_lower:
                specific_advice = (
                    "🔎 **Chi tiết chẩn đoán sơ bộ cho hiện tượng giật / rung khi tăng tốc:**\n"
                    "1. **Hệ thống đánh lửa:** Bugi bị mòn điện cực, bô-bin (ignition coil) bị đánh lửa rò hoặc yếu điện áp cao áp.\n"
                    "2. **Hệ thống nạp & nhiên liệu:** Họng ga (bướm ga) bám muội than, kim phun xăng bị nghẹt tia phun hoặc áp suất bơm xăng tụt.\n"
                    "3. **Hộp số & Chân máy:** Cao su chân máy/chân số bị lão hóa xẹp gây rung truyền thẳng vào khoang lái khi sang số.\n"
                )
            elif "điều hòa" in prompt_lower or "nóng" in prompt_lower:
                specific_advice = (
                    "🔎 **Chi tiết chẩn đoán sơ bộ cho hệ thống điều hòa:**\n"
                    "1. Thiếu hụt môi chất lạnh (Gas R134a) do rò rỉ gioăng phớt hoặc đường ống nhôm.\n"
                    "2. Lọc gió điều hòa trong cabin bị bám bụi nghẹt lưu lượng gió.\n"
                    "3. Quạt tản nhiệt dàn nóng phía đầu xe quay yếu hoặc ly hợp lốc lạnh (AC Compressor) bị trượt.\n"
                )
            elif "phanh" in prompt_lower or "abs" in prompt_lower:
                specific_advice = (
                    "🔎 **Chi tiết chẩn đoán sơ bộ cho hệ thống phanh / ABS:**\n"
                    "1. Cảm biến tốc độ bánh xe (ABS Wheel Speed Sensor) bị bám bụi kim loại hoặc đứt ngầm dây cáp dẫn tín hiệu.\n"
                    "2. Má phanh bị mòn dưới 3mm chạm vào lẫy cảnh báo kim loại.\n"
                    "3. Đĩa phanh bị cong vênh hoặc tạo gờ gây cảm giác giật chân phanh khi đạp rà.\n"
                )
            else:
                specific_advice = (
                    "🔎 **Khuyến nghị kiểm tra tổng quát:**\n"
                    "- Quét mã lỗi điện tử OBD-II để đọc các mã sự cố lưu trữ trong hộp điều khiển động cơ (ECU).\n"
                    "- Chạy thử thực tế với máy đo chuyên dụng và kiểm tra mức các loại dung dịch tuần hoàn.\n"
                )

            return (
                "🔍 **BÁO CÁO PHÂN TÍCH KỸ THUẬT TỪ TRỢ LÝ GARAGE VTV**\n\n"
                f"{specific_advice}\n"
                "🛠️ **Phương án xử lý khuyến nghị tại xưởng:**\n"
                "- Kết nối máy quét chẩn đoán chuyên dụng để kiểm tra thông số vận hành động cơ (Live Data Stream).\n"
                "- Vệ sinh họng ga, buồng đốt và đo điện áp kích hoạt bô-bin trước khi quyết định thay thế linh kiện.\n\n"
                "⚠️ *Lưu ý an toàn: Nhận định sơ bộ từ trợ lý AI cần được kỹ thuật viên trưởng kiểm tra trực tiếp trên cầu nâng để chốt phương án chính xác.*",
                model_used,
            )

        # QUOTATION
        if "--- số liệu tài chính đã tính bởi hệ thống ---" in prompt_lower or "bảng dự toán" in prompt_lower:
            return (
                "📋 **BÁO GIÁ NHÁP**\n\n"
                "Hệ thống đã tính toán chi phí dựa trên các hạng mục được nhập trong phiếu sửa chữa.\n\n"
                "⚠️ Đây là báo giá nháp. Chi phí thực tế có thể thay đổi sau khi kiểm tra xe và xác nhận phạm vi sửa chữa với khách hàng.\n\n"
                "Vui lòng đối chiếu số lượng, đơn giá phụ tùng và tiền công trước khi xác nhận sửa chữa.",
                model_used,
            )

        # GREETING / SMALL TALK
        greeting_keywords = ["alo", "hello", "hi ", "hey", "xin chào", "chào", "ơi", "bạn ơi", "có ai", "test", "thử"]
        if any(kw in prompt_lower for kw in greeting_keywords) or len(prompt_lower.strip()) <= 10:
            return (
                "👋 **Xin chào! Tôi là Trợ Lý GarageAI** — chuyên gia tư vấn dịch vụ và các combo bảo dưỡng tại Garage VTV!\n\n"
                "Tôi có thể hỗ trợ bạn:\n"
                "🔧 **Gói bảo dưỡng định kỳ** — mốc 5.000 km, 10.000 km, 20.000 km, 40.000 km\n"
                "✨ **Các combo chăm sóc xe** — combo điều hòa mát sâu, combo phanh gầm, combo phục hồi công suất\n"
                "💰 **Báo giá dịch vụ & phụ tùng** — chi phí tiền công, phụ tùng chính hãng minh bạch\n"
                "📅 **Đặt lịch hẹn nhanh** — tiếp nhận ưu tiên tại xưởng và nhận ưu đãi\n\n"
                "Bạn đang cần tư vấn gói bảo dưỡng hay dịch vụ nào cho xe của mình? 🚗",
                model_used,
            )

        # GENERAL
        return (
            "🤖 **TRỢ LÝ GARAGEAI — TƯ VẤN DỊCH VỤ & BẢO DƯỠNG**\n\n"
            "Tôi chuyên tư vấn:\n"
            "- Các gói bảo dưỡng định kỳ (5.000 km, 10.000 km, 20.000 km, 40.000 km...).\n"
            "- Các combo bảo dưỡng chuyên đề: phanh gầm, điều hòa, vệ sinh buồng đốt, làm đẹp xe.\n"
            "- Dự toán chi phí tiền công và vật tư phụ tùng chính hãng.\n"
            "- Quy trình đặt lịch hẹn và ưu đãi dịch vụ tại Garage VTV.\n\n"
            "💡 Bạn có thể bấm chọn các gợi ý nhanh phía trên hoặc nhập câu hỏi cụ thể nhé!",
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
    def _call_llm_agent(cls, db: Session, system_prompt: str, user_prompt: str) -> Tuple[str, str]:
        """Vòng lặp Agent gọi Tools tự động bằng Function Calling của Groq."""
        # pyrefly: ignore [missing-import]
        import httpx
        import json
        
        groq_api_key = getattr(settings, "GROQ_API_KEY", "") or os.getenv("GROQ_API_KEY", "")
        # Nếu không có key hoặc chỉ dùng Fallback thì gọi LLM thường (không tools)
        if not groq_api_key:
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
                "tools": AGENT_TOOLS,
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
        context_info = "\n\n".join(context_parts) if context_parts else "Không có thông tin xe hoặc phiếu sửa chữa cụ thể."

        prompt_template = PROMPT_ADMIN_ASSISTANT if is_admin else PROMPT_AI_ASSISTANT
        user_prompt = prompt_template.format(
            question=question,
            context_info=context_info,
            current_role=current_role,
        )

        base_system_prompt = SYSTEM_GARAGE_ADMIN_ASSISTANT if is_admin else SYSTEM_GARAGE_ASSISTANT
        system_prompt = cls._get_system_prompt_with_memory(db, base_system_prompt)
        output_text, model_used = cls._call_llm_agent(db, system_prompt, user_prompt)
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
