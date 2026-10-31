"""Gói RAG (Retrieval-Augmented Generation) cho Hệ thống AI Garage VTV.
"""
from backend.app.ai.rag.knowledge_base import KnowledgeChunk, GARAGE_KNOWLEDGE_BASE
from backend.app.ai.rag.retriever import retrieve_knowledge
from backend.app.ai.rag.generator import build_rag_augmented_prompt, synthesize_rag_local_response

__all__ = [
    "KnowledgeChunk",
    "GARAGE_KNOWLEDGE_BASE",
    "retrieve_knowledge",
    "build_rag_augmented_prompt",
    "synthesize_rag_local_response",
]
