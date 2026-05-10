"""LangGraph RAG pipeline with query rewriting, document grading, and HyDE expansion."""
import os
from typing import List, Literal, Optional, TypedDict

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from rag_utils import (
    generate_hypothetical_document,
    retrieve_documents,
    step_back_expand,
)
from tools import emit_rag_step

load_dotenv()

API_KEY = os.getenv("ARK_API_KEY")
MODEL = os.getenv("MODEL", "ep-20250227110822-5lvjg")
BASE_URL = os.getenv("BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "openai")
GRADE_MODEL = os.getenv("GRADE_MODEL", "gpt-4.1")

_grader_model = None
_router_model = None


def _get_grader_model():
    global _grader_model
    if _grader_model is None:
        _grader_model = init_chat_model(
            model=GRADE_MODEL,
            model_provider=MODEL_PROVIDER,
            api_key=API_KEY,
            base_url=BASE_URL,
        )
    return _grader_model


def _get_router_model():
    global _router_model
    if _router_model is None:
        _router_model = init_chat_model(
            model=MODEL,
            model_provider=MODEL_PROVIDER,
            api_key=API_KEY,
            base_url=BASE_URL,
        )
    return _router_model


class GradeDocuments(BaseModel):
    """Binary score for relevance check."""
    binary_score: str = Field(description="'yes' or 'no' — whether the document is relevant to the question")


class RewriteStrategy(BaseModel):
    """Strategy selection for query rewriting."""
    strategy: Literal["step_back", "hyde", "none"] = Field(
        description="Which strategy to use: step_back, hyde, or none"
    )


class RAGState(TypedDict):
    question: str
    generation: str
    docs: List[str]
    context: str
    initial_docs: List[dict]
    expanded_docs: List[dict]
    final_docs: List[dict]
    expansion_type: Optional[str]
    step_back_question: Optional[str]
    step_back_answer: Optional[str]
    hypothetical_doc: Optional[str]
    rerank_applied: bool
    retrieval_stage: str
    rewrite_needed: bool
    rewrite_query: str


GRADE_PROMPT = (
    "You are a grader assessing relevance of a retrieved document to a user question. \n"
    " Here is the retrieved document: \n\n {context} \n\n"
    "Here is the user question: {question} \n"
    "If the document contains keyword(s) or semantic meaning related to the user question, "
    "grade it as relevant. \n"
    "Give a binary score 'yes' or 'no' score to indicate whether the document is relevant to the question."
)


def _format_docs(docs: List[dict]) -> str:
    lines = []
    for i, doc in enumerate(docs):
        text = doc.get("text", "") if isinstance(doc, dict) else str(doc)
        if text:
            lines.append(f"[{i + 1}] {text}")
    return "\n\n".join(lines)


def retrieve_initial(state: RAGState) -> dict:
    emit_rag_step("🔍", "正在进行初步检索...")
    result = retrieve_documents(state["question"], top_k=10)
    initial_docs = result.get("docs", [])
    return {
        "initial_docs": initial_docs,
        "context": _format_docs(initial_docs),
        "retrieval_stage": "initial",
        "expansion_type": None,
    }


def grade_documents_node(state: RAGState) -> dict:
    question = state["question"]
    docs = state.get("initial_docs", [])

    if not docs:
        return {"docs": [], "rewrite_needed": False}

    emit_rag_step("📊", "正在评估文档相关性...")

    model = _get_grader_model()
    grader = model.with_structured_output(GradeDocuments)

    filtered_docs = []
    for doc in docs:
        text = doc.get("text", "") if isinstance(doc, dict) else str(doc)
        if not text:
            continue
        prompt = GRADE_PROMPT.format(context=text[:1000], question=question)
        try:
            result = grader.invoke(prompt)
            if result.binary_score.strip().lower() == "yes":
                filtered_docs.append(doc)
        except Exception:
            filtered_docs.append(doc)

    rewrite_needed = len(filtered_docs) < len(docs) * 0.5 if docs else False
    return {
        "docs": filtered_docs,
        "rewrite_needed": rewrite_needed,
    }


def rewrite_question_node(state: RAGState) -> dict:
    emit_rag_step("🔄", "正在尝试优化查询...")

    model = _get_router_model()
    router = model.with_structured_output(RewriteStrategy)

    try:
        route_result = router.invoke(
            f"Given the user question: {state['question']}\n"
            f"Initial retrieval returned few relevant results. "
            f"Choose a strategy: 'step_back' (broaden the question), "
            f"'hyde' (generate a hypothetical document), or 'none' (keep as-is)."
        )
        strategy = route_result.strategy
    except Exception:
        strategy = "step_back"

    updates = {"expansion_type": strategy}

    if strategy == "step_back":
        result = step_back_expand(state["question"])
        updates["step_back_question"] = result.get("step_back_question")
        updates["step_back_answer"] = result.get("step_back_answer")
        updates["rewrite_query"] = result.get("step_back_question") or state["question"]

    elif strategy == "hyde":
        hypo_doc = generate_hypothetical_document(state["question"])
        updates["hypothetical_doc"] = hypo_doc
        updates["rewrite_query"] = state["question"]

    return updates


def retrieve_expanded(state: RAGState) -> dict:
    query = state.get("rewrite_query") or state["question"]

    if state.get("expansion_type") == "hyde" and state.get("hypothetical_doc"):
        emit_rag_step("🔍", "正在使用 HyDE 增强检索...")
        result = retrieve_documents(query, top_k=10)
        hyde_result = retrieve_documents(state["hypothetical_doc"], top_k=5)
        all_docs = result.get("docs", []) + hyde_result.get("docs", [])
        seen = set()
        deduped = []
        for doc in all_docs:
            doc_id = doc.get("chunk_id", doc.get("id", ""))
            if doc_id not in seen:
                seen.add(doc_id)
                deduped.append(doc)

        context = _format_docs(deduped)
        return {"expanded_docs": deduped, "context": context, "retrieval_stage": "expanded"}
    else:
        emit_rag_step("🔍", "正在使用优化查询重新检索...")
        result = retrieve_documents(query, top_k=10)
        expanded_docs = result.get("docs", [])
        all_docs = state.get("docs", []) + expanded_docs
        seen = set()
        deduped = []
        for doc in all_docs:
            doc_id = doc.get("chunk_id", doc.get("id", ""))
            if doc_id not in seen:
                seen.add(doc_id)
                deduped.append(doc)

        context = _format_docs(deduped)
        return {"expanded_docs": deduped, "context": context, "retrieval_stage": "expanded"}


def build_rag_graph():
    workflow = StateGraph(RAGState)

    workflow.add_node("retrieve_initial", retrieve_initial)
    workflow.add_node("grade_documents", grade_documents_node)
    workflow.add_node("rewrite_question", rewrite_question_node)
    workflow.add_node("retrieve_expanded", retrieve_expanded)

    workflow.set_entry_point("retrieve_initial")
    workflow.add_edge("retrieve_initial", "grade_documents")

    def decide_to_rewrite(state: RAGState) -> str:
        if state.get("rewrite_needed"):
            return "rewrite_question"
        return "end"

    workflow.add_conditional_edges(
        "grade_documents",
        decide_to_rewrite,
        {"rewrite_question": "rewrite_question", "end": END},
    )

    workflow.add_edge("rewrite_question", "retrieve_expanded")
    workflow.add_edge("retrieve_expanded", END)

    return workflow.compile()


rag_graph = build_rag_graph()


def run_rag_graph(question: str) -> dict:
    initial_state: RAGState = {
        "question": question,
        "generation": "",
        "docs": [],
        "context": "",
        "initial_docs": [],
        "expanded_docs": [],
        "final_docs": [],
        "expansion_type": None,
        "step_back_question": None,
        "step_back_answer": None,
        "hypothetical_doc": None,
        "rerank_applied": False,
        "retrieval_stage": "initial",
        "rewrite_needed": False,
        "rewrite_query": question,
    }
    result = rag_graph.invoke(initial_state)
    return result
