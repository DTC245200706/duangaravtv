file_path = "backend/app/ai/service.py"
with open(file_path, "r", encoding="utf-8") as f:
    code = f.read()

target = '''        context_info = "\\n\\n".join(context_parts) if context_parts else "Không có thông tin xe hoặc phiếu sửa chữa cụ thể."'''

replacement = '''        # RAG Augmentation: Tự động truy xuất tri thức chuyên môn từ RAG Knowledge Base
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
                    context_parts.append("\\n".join(rag_lines))
            except Exception as e:
                pass

        context_info = "\\n\\n".join(context_parts) if context_parts else "Không có thông tin xe hoặc phiếu sửa chữa cụ thể."'''

assert target in code, "target not found"
code = code.replace(target, replacement, 1)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(code)
print("SUCCESS: Added RAG context injection to ask_assistant")
