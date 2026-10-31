"""Cơ sở tri thức kỹ thuật ô tô & Dịch vụ Garage VTV phục vụ hệ thống RAG (Retrieval-Augmented Generation).

Chứa các tri thức chuyên sâu về chẩn đoán lỗi, quy trình kỹ thuật, đơn giá phụ tùng,
so sánh giải pháp và cẩm nang vận hành xe ô tô.
"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class KnowledgeChunk:
    chunk_id: str
    category: str  # air_conditioner, brakes, engine, suspension, steering, maintenance, comparison, policy
    title: str
    keywords: List[str]
    content: str
    action_type: str = "consultation"  # consultation, quotation, comparison, booking
    recommended_combo: Optional[str] = None
    estimated_cost: Optional[str] = None
    time_estimate: Optional[str] = None

GARAGE_KNOWLEDGE_BASE: List[KnowledgeChunk] = [
    # --------------------------------------------------------------------------
    # CHỦ ĐỀ 1: ĐIỀU HÒA Ô TÔ (AIR CONDITIONING)
    # --------------------------------------------------------------------------
    KnowledgeChunk(
        chunk_id="AC-001",
        category="air_conditioner",
        title="Điều hòa có mùi ẩm mốc khi mới bật nhưng vẫn lạnh",
        keywords=[
            "điều hòa", "mùi ẩm", "mùi chua", "mùi hôi", "vẫn lạnh", "mới bật",
            "tháo taplo", "nạp gas", "nấm mốc", "giàn lạnh", "lọc gió", "nước ngưng"
        ],
        content="""• Hiện trạng & Nguyên nhân: Khi mới bật điều hòa phả ra mùi chua ẩm mốc trong 1-2 phút đầu nhưng sau đó vẫn mát lạnh. Nguyên nhân do dàn lạnh (Evaporator) đọng hơi nước sau khi tắt máy, tạo môi trường cho nấm mốc, vi khuẩn và bụi bẩn sinh sôi bám trên các cánh tản nhiệt nhôm. Ngoài ra lọc gió cabin ẩm mốc hoặc đường thoát nước ngưng bị nghẹt cũng làm tích tụ mùi chua.
• Có cần nạp gas không? HOÀN TOÀN KHÔNG CẦN. Vì xe vẫn lạnh sâu bình thường chứng tỏ hệ thống lốc lạnh (Compressor) và áp suất môi chất lạnh (Gas R134a) vẫn đạt chuẩn, đường ống kín tuyệt đối không bị xì hở.
• Có cần tháo taplo không? TUYỆT ĐỐI CHƯA CẦN THÁO TAPLO. Tháo hạ cả bảng điều khiển taplo rất tốn kém (2.500.000 – 4.000.000 VNĐ), mất 1–2 ngày làm việc và nguy cơ gãy lẫy nhựa, phát sinh tiếng kêu cọt kẹt khi chạy sau này. Phương án tháo taplo chỉ dùng khi giàn lạnh bị mục nát xì gas cần thay thế lõi mới.
• Quy trình kiểm tra & xử lý tối ưu:
  1. Kiểm tra và thay thế lọc gió điều hòa than hoạt tính (Cabin Filter) — chi phí 150.000 – 250.000 VNĐ, thay trong 5 phút.
  2. Kiểm tra thông tắc đường ống xả nước ngưng dưới sàn xe.
  3. Vệ sinh nội soi dàn lạnh camera HD + Xịt dung dịch vi sinh diệt khuẩn Nano Bạc — làm sạch 95% nấm mốc mà KHÔNG CẦN THÁO TAPLO (thời gian 45 phút, chi phí trọn gói 650.000 – 850.000 VNĐ).
• Mẹo sử dụng phòng ngừa: Trước khi kết thúc hành trình 2–3 phút, hãy tắt nút A/C và để quạt gió thổi mạnh để sấy khô dàn lạnh trước khi đỗ xe.""",
        action_type="consultation",
        recommended_combo="Combo Điều Hòa Mát Sâu & Khử Khuẩn Nano Bạc",
        estimated_cost="650.000 – 850.000 VNĐ",
        time_estimate="45 phút"
    ),
    KnowledgeChunk(
        chunk_id="AC-002",
        category="air_conditioner",
        title="Điều hòa mất lạnh, không mát, gió yếu hoặc chỉ mát khi xe chạy",
        keywords=[
            "điều hòa", "mất lạnh", "không mát", "nóng cabin", "gió yếu", "lốc lạnh",
            "xì gas", "dàn nóng", "r134a", "van tiết lưu", "quạt gió"
        ],
        content="""• Hiện trạng & Nguyên nhân:
  1. Không mát cả khi đỗ lẫn khi chạy: Thường do xì hết gas lạnh (hở gioăng ti van nạp, thủng dàn nóng/dàn lạnh, hỏng phớt lốc).
  2. Chỉ mát khi xe chạy trên đường, đỗ một chỗ mất mát: Do dàn nóng phía đầu xe bị đất cát bám kín không giải nhiệt được, hoặc quạt giải nhiệt két nước/dàn nóng bị yếu/chết than.
  3. Gió thổi rất yếu dù quạt bật nấc lớn: Do lọc gió điều hòa bị tắc nghẹt bụi bẩn hoặc mô-tơ quạt lồng sóc bị kẹt rác lá cây.
• Phương án xử lý tại Garage VTV:
  - Cắm đồng hồ đo áp suất cao áp/hạ áp kiểm tra lượng gas và sức nén của lốc lạnh.
  - Soi đèn huỳnh quang UV kiểm tra điểm rò rỉ gas.
  - Vệ sinh xịt rửa giàn nóng bằng hóa chất chuyên dụng.
  - Nạp gas chuẩn R134a tinh khiết bằng máy thu hồi nạp gas tự động TEXA (Ý).""",
        action_type="consultation",
        recommended_combo="Combo Bảo Dưỡng Hệ Thống Lạnh Toàn Diện",
        estimated_cost="450.000 – 1.250.000 VNĐ",
        time_estimate="60 phút"
    ),
    KnowledgeChunk(
        chunk_id="AC-003",
        category="comparison",
        title="So sánh Vệ sinh nội soi dàn lạnh vs Tháo rã toàn bộ taplo",
        keywords=[
            "so sánh", "nội soi", "tháo taplo", "hạ taplo", "dàn lạnh", "tiết kiệm",
            "gãy ngàm", "camera hd", "vệ sinh điều hòa"
        ],
        content="""• So sánh chi tiết hai phương pháp làm sạch dàn lạnh:
  - Phương pháp Vệ sinh nội soi camera HD: Luồn ống camera siêu nhỏ và đầu phun áp lực đa hướng trực tiếp vào hộp giàn lạnh qua họng quạt gió; làm sạch 90–95% cặn mốc bẩn; Thời gian thi công 45 – 60 phút; Giữ zin 100% kết cấu taplo, dây điện và túi khí; Chi phí trọn gói 650.000 – 950.000 VNĐ.
  - Phương pháp Tháo hạ toàn bộ taplo: Phải tháo dỡ vô lăng, cụm túi khí, đồng hồ taplo, dây điện và khung nhôm; Thời gian thi công 1 – 2 ngày; Rất dễ gãy ngàm nhựa và nguy cơ phát tiếng kêu cọt kẹt khi chạy sau này; Chi phí 2.500.000 – 4.000.000 VNĐ.
• Lời khuyên KTV: Nên chọn giải pháp Vệ sinh nội soi định kỳ hàng năm để tiết kiệm 70% chi phí và an toàn tuyệt đối cho xe. Chỉ hạ taplo khi giàn lạnh bị bục thủng xì gas cần hàn hoặc thay mới.""",
        action_type="comparison",
        recommended_combo="Vệ sinh nội soi dàn lạnh camera HD",
        estimated_cost="650.000 – 950.000 VNĐ",
        time_estimate="45 – 60 phút"
    ),

    # --------------------------------------------------------------------------
    # CHỦ ĐỀ 2: HỆ THỐNG PHANH (BRAKES)
    # --------------------------------------------------------------------------
    KnowledgeChunk(
        chunk_id="BR-001",
        category="brakes",
        title="Rung giật vô lăng hoặc rung bàn đạp phanh khi rà phanh ở tốc độ cao",
        keywords=[
            "phanh", "rung phanh", "giật", "vô lăng", "láng đĩa", "đĩa phanh",
            "cong vênh", "má phanh", "calip", "ắc trượt"
        ],
        content="""• Nguyên nhân: Đĩa phanh bị biến dạng nhiệt (cong vênh) hoặc bị mòn gờ không đồng đều. Hiện tượng này thường xảy ra khi xe rà phanh liên tục trên đèo dốc hoặc rửa xe xịt nước lạnh vào đĩa phanh khi đĩa đang quá nhiệt. Ngoài ra ắc trượt cùm phanh bị kẹt mỡ cũng làm má phanh tì lệch gây rung giật.
• Mức độ an toàn: NGUY HIỂM — Làm quãng đường phanh dài hơn 15-20% và giảm khả năng kiểm soát xe khi phanh gấp.
• Phương án xử lý tại Garage VTV:
  - Dùng đồng hồ so điện tử đo độ đảo mặt đĩa phanh (Run-out tolerance < 0.05 mm).
  - Láng đĩa phanh vi tính trực tiếp hoặc trên máy láng chuyên dụng nếu độ dày còn trên mức an toàn (Min Thickness).
  - Vệ sinh cùm phanh và tra mỡ chịu nhiệt cao cấp ắc trượt calip.""",
        action_type="consultation",
        recommended_combo="Combo Láng Đĩa Phanh Vi Tính & Bảo Dưỡng 4 Bánh",
        estimated_cost="350.000 – 500.000 VNĐ/cặp đĩa",
        time_estimate="45 – 60 phút"
    ),
    KnowledgeChunk(
        chunk_id="BR-002",
        category="brakes",
        title="Tiếng kêu rít ken két hoặc cọ xát kim loại khi đạp phanh",
        keywords=[
            "kêu rít", "ken két", "cọ xát", "má phanh", "mòn má phanh", "đĩa phanh", "xước đĩa"
        ],
        content="""• Nguyên nhân:
  1. Má phanh đã mòn chạm cữ cảnh báo kim loại (Wear Indicator) cạ vào đĩa phanh tạo tiếng rít báo hiệu phải thay má gấp.
  2. Má phanh bị dính cát sỏi nhỏ hoặc bề mặt má bị chai lì (Glazed Brake Pads) do quá nhiệt.
  3. Má phanh kém chất lượng chứa nhiều vụn sắt to gây xước đĩa.
• Phương án xử lý: Tháo 4 bánh đo độ dày má phanh. Nếu má phanh còn < 3mm cần thay má mới (Má phanh Ceramic chống ồn, chống bụi than). Xịt rửa cặn phanh và láng phẳng vết xước trên mặt đĩa.""",
        action_type="consultation",
        recommended_combo="Thay má phanh Ceramic & Bảo dưỡng cùm phanh",
        estimated_cost="850.000 – 1.650.000 VNĐ/cặp",
        time_estimate="45 phút"
    ),
    KnowledgeChunk(
        chunk_id="BR-003",
        category="comparison",
        title="So sánh Láng đĩa phanh vi tính vs Thay mới đĩa phanh chính hãng",
        keywords=[
            "so sánh", "láng đĩa", "thay đĩa", "đĩa phanh", "tiết kiệm", "độ dày"
        ],
        content="""• So sánh giải pháp xử lý đĩa phanh:
  - Láng đĩa phanh vi tính: Dùng dao tiện CNC gọt phẳng lớp gờ xước và triệt tiêu độ vênh; Áp dụng khi đĩa còn dày hơn mức tối thiểu (Min Thickness dập nổi trên đĩa); Chi phí chỉ 350.000 – 500.000 VNĐ/cặp (Tiết kiệm 75% chi phí).
  - Thay mới đĩa phanh: Áp dụng khi đĩa bị mòn quá giới hạn, nứt nẻ rạn chân chim hoặc bị đảo quá nặng không thể tiện; Chi phí 1.800.000 – 4.500.000 VNĐ/cặp đĩa chính hãng.
• Lời khuyên KTV: Kỹ thuật viên sẽ dùng thước kẹp panme đo độ dày trước. Nếu còn an toàn thì chỉ cần láng đĩa để tối ưu chi phí cho chủ xe.""",
        action_type="comparison",
        recommended_combo="Láng đĩa phanh CNC",
        estimated_cost="350.000 – 500.000 VNĐ/cặp",
        time_estimate="45 phút"
    ),

    # --------------------------------------------------------------------------
    # CHỦ ĐỀ 3: ĐỘNG CƠ & NHIÊN LIỆU (ENGINE & FUEL)
    # --------------------------------------------------------------------------
    KnowledgeChunk(
        chunk_id="ENG-001",
        category="engine",
        title="Động cơ hụt ga, khó nổ, máy rung giật khi tăng tốc hoặc dừng đèn đỏ",
        keywords=[
            "hụt ga", "khó nổ", "rung giật", "máy yếu", "bugi", "bô bin",
            "họng ga", "kim phun", "buồng đốt", "chân máy", "lọc xăng"
        ],
        content="""• Nguyên nhân:
  1. Hệ thống đánh lửa: Bugi mòn điện cực, khe hở đánh lửa rộng hoặc bô-bin đánh lửa (Ignition Coil) bị rò điện, yếu tia lửa.
  2. Hệ thống nạp khí & nhiên liệu: Họng ga (Throttle Body) bám muội carbon làm kẹt bướm ga; Kim phun xăng nghẹt tia tơi xốp; Lọc xăng quá bẩn làm tụt áp suất bơm xăng.
  3. Chân máy/chân số cao su bị xẹp vỡ: Truyền rung động trực tiếp vào khung cabin khi về số D dừng đèn đỏ.
• Phương án xử lý tại Garage VTV:
  - Cắm máy quét lỗi OBD-II kiểm tra mã bỏ lửa Misfire (P0300..P0304) và dữ liệu Long Term Fuel Trim.
  - Gói Combo Phục Hồi Công Suất: Vệ sinh họng ga, súc rửa kim phun bằng sóng siêu âm, bắn trấu làm sạch xupap hoặc vệ sinh buồng đốt khí Hydro.""",
        action_type="consultation",
        recommended_combo="Combo Phục Hồi Công Suất & Vệ Sinh Họng Ga Kim Phun",
        estimated_cost="750.000 – 1.250.000 VNĐ",
        time_estimate="60 – 90 phút"
    ),
    KnowledgeChunk(
        chunk_id="ENG-002",
        category="engine",
        title="Đèn báo lỗi động cơ Check Engine (cá vàng) bật sáng trên taplo",
        keywords=[
            "check engine", "báo lỗi", "cá vàng", "đèn lỗi", "cảm biến oxy", "maf", "obd-ii", "catalytic"
        ],
        content="""• Nguyên nhân phổ biến: Cảm biến oxy (Oxygen Sensor) bẩn/hỏng; Cảm biến lưu lượng gió (MAF/MAP) bẩn; Bỏ lửa xi-lanh; Nắp bình xăng vặn lỏng hoặc hở van EVAP; Tắc bầu lọc khí xả Catalytic Converter.
• Mức độ khẩn cấp:
  - Đèn sáng đứng yên: Xe vẫn chạy được tạm thời, nhưng cần mang xe vào gara kiểm tra sớm trong vòng 1-2 ngày để tránh tốn xăng và hại máy.
  - Đèn NHẤP NHÁY liên tục: NGUY CẤP — Đang bị bỏ lửa nặng, cần giảm tốc độ và tấp lề gọi cứu hộ hoặc chạy chậm vào gara ngay lập tức để tránh cháy hỏng bầu lọc khí thải.
• Phương án xử lý: Miễn phí cắm máy chẩn đoán chuyên dụng quét đọc mã lỗi DTC và xóa lỗi sau khi xử lý triệt để.""",
        action_type="consultation",
        recommended_combo="Chẩn đoán mã lỗi OBD-II chuyên sâu",
        estimated_cost="Miễn phí quét lỗi khi làm dịch vụ",
        time_estimate="20 phút"
    ),

    # --------------------------------------------------------------------------
    # CHỦ ĐỀ 4: GẦM & HỆ THỐNG LÁI (SUSPENSION & STEERING)
    # --------------------------------------------------------------------------
    KnowledgeChunk(
        chunk_id="SUS-001",
        category="suspension",
        title="Tiếng kêu lục cục dưới gầm khi đi qua gờ giảm tốc hoặc đường xấu",
        keywords=[
            "lục cục", "kêu gầm", "gờ giảm tốc", "rô tuyn", "càng a", "giảm xóc",
            "phuộc nhún", "bát bèo", "thanh cân bằng", "thước lái"
        ],
        content="""• Nguyên nhân:
  1. Rô-tuyn thanh cân bằng (Stabilizer Link) bị rơ khớp cầu: Nguyên nhân hàng đầu gây tiếng kêu 'cục cục' khô khốc khi một bên bánh leo gờ nhấp nhô.
  2. Cao su càng A hoặc cao su gầm bị chai nứt vỡ: Khiến các thanh giằng kim loại va chạm trực tiếp vào sắt xi.
  3. Phuộc nhún (giảm xóc) bị xì dầu, chết giảm chấn hoặc tăm-bông bát bèo trên đầu phuộc bị vỡ nát.
  4. Rơ bạc thước lái.
• Phương án xử lý tại Garage VTV: Nâng xe kiểm tra trực tiếp độ rơ từng khớp cầu bằng đòn bẩy kỹ thuật; Siết lực bulong toàn bộ gầm theo chuẩn Nm của nhà sản xuất; Thay thế rô-tuyn hoặc ép lại cao su càng A bằng máy ép thủy lực chuyên dụng.""",
        action_type="consultation",
        recommended_combo="Combo Kiểm Tra An Toàn Gầm & Siết Lực Khung Sườn",
        estimated_cost="250.000 – 850.000 VNĐ",
        time_estimate="45 phút"
    ),
    KnowledgeChunk(
        chunk_id="SUS-002",
        category="steering",
        title="Vô lăng bị lệch tâm, xe nhao lái sang một bên, lốp mòn không đều",
        keywords=[
            "lệch lái", "nhao lái", "vô lăng", "mòn lốp", "cân chỉnh góc lái", "hunter", "camber", "toe"
        ],
        content="""• Nguyên nhân: Sai lệch góc đặt bánh xe (Góc Chụm - Toe, Góc Nghiêng - Camber, Caster) do sụp ổ gà mạnh hoặc leo lề đường; Áp suất lốp không đều giữa hai bên; Lốp xe bị mòn vẹt mép tạo lực kéo lệch.
• Hậu quả: Xe mất ổn định ở tốc độ cao, người lái liên tục phải ghì vô lăng gây mỏi tay, lốp xe nhanh mòn hỏng phải thay sớm.
• Phương án xử lý: Cân chỉnh góc đặt bánh xe 3D vi tính bằng máy Hunter Hoa Kỳ: Đo đạc 3D chính xác từng phút độ, căn chuẩn vô lăng thẳng tắp tuyệt đối.""",
        action_type="consultation",
        recommended_combo="Cân chỉnh góc đặt bánh xe 3D điện tử Hunter",
        estimated_cost="350.000 – 500.000 VNĐ",
        time_estimate="30 – 45 phút"
    ),

    # --------------------------------------------------------------------------
    # CHỦ ĐỀ 5: BẢNG GIÁ & BẢO DƯỠNG ĐỊNH KỲ (MAINTENANCE & PRICING)
    # --------------------------------------------------------------------------
    KnowledgeChunk(
        chunk_id="MNT-001",
        category="maintenance",
        title="Bảng giá các mốc bảo dưỡng định kỳ tiêu chuẩn (Cấp 1 đến Cấp 4)",
        keywords=[
            "bảo dưỡng định kỳ", "bảng giá", "chi phí", "cấp 1", "cấp 2", "cấp 3", "cấp 4",
            "5.000", "10.000", "20.000", "40.000", "thay dầu"
        ],
        content="""• Bảng giá bảo dưỡng định kỳ minh bạch tại Garage VTV:
  1. Gói Cấp 1 (5.000 km / 3 tháng): Thay dầu nhớt động cơ, kiểm tra 20 hạng mục an toàn, vệ sinh lọc gió, châm nước làm mát/nước rửa kính. Chi phí: 650.000 – 1.150.000 VNĐ.
  2. Gói Cấp 2 (10.000 km / 6 tháng): Hạng mục Cấp 1 + Thay lọc nhớt mới, tháo 4 bánh vệ sinh bảo dưỡng phanh, tra mỡ calip, đảo lốp. Chi phí: 1.250.000 – 1.850.000 VNĐ.
  3. Gói Cấp 3 (20.000 km / 12 tháng): Hạng mục Cấp 2 + Thay lọc gió động cơ, thay lọc gió điều hòa cabin, vệ sinh họng ga bướm ga, cân bằng động bánh xe. Chi phí: 2.100.000 – 3.200.000 VNĐ.
  4. Gói Cấp 4 (40.000 km / 24 tháng — Đại tu bảo dưỡng lớn): Hạng mục Cấp 3 + Thay toàn bộ dầu phanh, dầu trợ lực lái, dầu hộp số, nước làm mát động cơ, thay lọc xăng, thay bugi Iridium, bảo dưỡng củ đề máy phát. Chi phí: 4.500.000 – 7.500.000 VNĐ.
• Cam kết minh bạch: Mọi báo giá đều ghi rõ công thợ + vật tư, xuất hóa đơn VAT đầy đủ, bàn giao lại phụ tùng cũ thay ra.""",
        action_type="quotation",
        recommended_combo="Bảo dưỡng định kỳ theo mốc ODO xe",
        estimated_cost="650.000 – 7.500.000 VNĐ",
        time_estimate="60 – 180 phút"
    ),
    KnowledgeChunk(
        chunk_id="MNT-002",
        category="comparison",
        title="So sánh Dầu nhớt khoáng vs Dầu tổng hợp toàn phần (Fully Synthetic)",
        keywords=[
            "so sánh", "dầu nhớt", "dầu khoáng", "tổng hợp", "fully synthetic", "5w-30", "0w-20", "chu kỳ"
        ],
        content="""• So sánh dầu nhớt khoáng vs Dầu tổng hợp:
  - Dầu khoáng (Mineral): Chiết xuất trực tiếp từ dầu mỏ; Chu kỳ thay ngắn (5.000 km hoặc 3-4 tháng); Nhanh bị biến chất ở nhiệt độ cao; Giá 350.000 – 550.000 VNĐ/can 4L.
  - Dầu tổng hợp toàn phần (Fully Synthetic): Phân tử nhân tạo đồng nhất cao cấp; Chu kỳ chạy bền bỉ 10.000 – 15.000 km (hoặc 12 tháng); Giải nhiệt xuất sắc, chống đóng cặn bùn, bảo vệ trục Turbo tăng áp; Giúp máy êm bốc và tiết kiệm 2–3% xăng; Giá 850.000 – 1.350.000 VNĐ/can 4L (Castrol Magnatec / Motul / Mobil 1).
• Lời khuyên KTV: Xe đời mới, xe chạy đô thị tắc đường hoặc xe Turbo nên chọn dầu Tổng Hợp Toàn Phần để động cơ luôn bền bỉ và tiết kiệm thời gian đi bảo dưỡng.""",
        action_type="comparison",
        recommended_combo="Thay dầu tổng hợp toàn phần 10.000 km",
        estimated_cost="850.000 – 1.350.000 VNĐ",
        time_estimate="30 phút"
    )
]
