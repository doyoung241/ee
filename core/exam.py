import os, re, json
from typing import List, Dict, Any, Tuple
import fitz  # PyMuPDF
from dotenv import load_dotenv
from rapidfuzz import fuzz
from openai import OpenAI

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# -------------------------------
# OpenAI Client
# -------------------------------
def _client(api_key: str = None) -> OpenAI:
    key = api_key or OPENAI_API_KEY
    if not key:
        raise RuntimeError("OPENAI_API_KEY가 설정되지 않았습니다. .env 또는 계정 설정에서 API 키를 등록하세요.")
    return OpenAI(api_key=key)

# -------------------------------
# PDF 텍스트 추출
# -------------------------------
def extract_text_from_pdfs(files) -> List[Dict[str, Any]]:
    texts = []
    for f in files:
        data = f.read()
        doc = fitz.open(stream=data, filetype="pdf")
        for page_num, page in enumerate(doc, start=1):
            texts.append({
                "filename": getattr(f, "name", "upload.pdf"),
                "page": page_num,
                "text": page.get_text().strip()
            })
        doc.close()
    return texts

# -------------------------------
# 문제 생성 (난이도 + 유형 + 스타일 + 이전 문제 제외)
# -------------------------------
def generate_questions(full_text: str, num_questions=8, difficulty="중",
                       style="", prev_questions=None, q_type="서술형") -> List[str]:
    difficulty_prompts = {
        "하": "기초 개념 확인과 간단한 사실 질문을 섞어서",
        "중": "이해/적용 중심의 중간 난이도로",
        "상": "분석/종합 중심의 심화 난이도로",
    }
    style_text = f" 교수자(출제) 스타일: {style}" if style else ""

    # 이전 문제 제외
    exclude_text = ""
    if prev_questions:
        exclude_text = f"\n단, 아래 문제들은 이미 사용되었으므로 절대 반복하지 마:\n{json.dumps(prev_questions, ensure_ascii=False, indent=2)}\n"

    # 문제 유형 지시어
    if q_type == "객관식":
        type_text = "- 반드시 객관식 문제로 만들고 각 문제에 선택지 4개를 포함해줘."
    elif q_type == "OX퀴즈":
        type_text = "- 반드시 OX 문제 형식으로 만들어줘."
    else:
        type_text = "- 반드시 서술형 문제 형식으로 만들어줘."

    prompt = f"""
아래 자료를 바탕으로 {difficulty_prompts.get(difficulty,'중간 난이도')}
한국어 {q_type} 예상문제 {num_questions}개를 만들어줘.
{type_text}
- 질문만 출력 (정답 금지), 번호 1.~N. 형식.
난이도: {difficulty}{style_text}
{exclude_text}

자료:
\"\"\" 
{full_text[:12000]} 
\"\"\" 
""".strip()

    client = _client()
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5,
    )
    text = resp.choices[0].message.content
    qs = re.findall(r'^\s*\d+\.\s*(.+)', text, flags=re.M)
    if not qs:
        qs = [line.strip("-• ").strip() for line in text.splitlines() if line.strip()]
    return qs[:num_questions]

# -------------------------------
# 채점 관련 함수
# -------------------------------
def _norm(s: str) -> str:
    return (s or "").lower().strip()

def best_source_page(question: str, model_answer: str, context_pages: List[Dict[str, Any]]) -> Dict[str, Any]:
    best = {"filename": None, "page": None, "score": -1}
    target = (question or "") + " " + (model_answer or "")
    for p in context_pages:
        sc = fuzz.partial_ratio(_norm(target), _norm(p["text"]))
        if sc > best["score"]:
            best = {"filename": p["filename"], "page": p["page"], "score": sc}
    return best

def get_model_answer_and_keys(question: str, context_pages: List[Dict[str, Any]],
                              difficulty: str, api_key: str = None) -> Dict[str, Any]:
    previews = [{"file": t["filename"], "page": t["page"], "text": t["text"][:300]} for t in context_pages[:6]]
    system = "너는 대학 시험 채점 조교야. JSON만 반환해. 설명, 서문, 마크다운 금지."
    user = f"""
문제:
{question}

난이도: {difficulty}

다음은 교재 일부 페이지 요약(최대 6개):
{json.dumps(previews, ensure_ascii=False, indent=2)}

JSON만 반환:
{{
  "model_answer": "간결하고 정확한 모범답안",
  "key_points": ["핵심 키워드1", "핵심 포인트2", "... 최대 8개"]
}}
""".strip()
    client = _client(api_key)
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        temperature=0.2,
    )
    raw = resp.choices[0].message.content
    try:
        data = json.loads(raw)
        if not isinstance(data.get("key_points", []), list):
            data["key_points"] = []
        return {
            "model_answer": data.get("model_answer",""),
            "key_points": [k for k in data.get("key_points", []) if isinstance(k,str)][:8]
        }
    except Exception:
        return {"model_answer": "", "key_points": []}

# -------------------------------
# 점수 계산
# -------------------------------
def tokenize(s: str):
    import re
    return re.findall(r"[가-힣A-Za-z0-9]+", s or "")

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def contains_any(text: str, keys: List[str]) -> Tuple[int, List[str]]:
    t = _norm(text)
    matched = []
    for k in keys:
        kk = _norm(k)
        if kk and kk in t:
            matched.append(k)
    return len(matched), matched

def score_answer(student_answer: str, model_answer: str, key_points: List[str]) -> Dict[str, Any]:
    a = (student_answer or "").strip()
    if not a or len(a) < 4:
        return {"total": 0, "coverage": 0.0, "similarity": 0.0,
                "length_score": 0.0, "matched_keys": []}
    cov_cnt, matched = contains_any(a, key_points)
    coverage = cov_cnt / max(1, len(key_points))
    sim = fuzz.partial_ratio(_norm(a), _norm(model_answer)) / 100.0
    length_score = clamp(len(tokenize(a)) / 60.0, 0.0, 1.0)
    total = int(round(10 * (0.6*coverage + 0.3*sim + 0.1*length_score)))
    return {
        "total": total,
        "coverage": round(coverage,3),
        "similarity": round(sim,3),
        "length_score": round(length_score,3),
        "matched_keys": matched
    }

# -------------------------------
# PDF별 학습 리포트
# -------------------------------
def build_weakness_by_pdf(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    from collections import defaultdict
    m = defaultdict(list)
    for r in rows:
        if r.get("filename"):
            m[r["filename"]].append(r.get("score", 0.0))
    stats = []
    for f, arr in m.items():
        stats.append({"pdf": f, "avg": sum(arr)/len(arr), "count": len(arr)})
    return sorted(stats, key=lambda x: x["avg"])

# -------------------------------
# 챗봇 지원 함수
# -------------------------------
def ask_chatbot(question: str, context: str = "") -> str:
    """
    사용자가 모르는 문제나 단어를 질문할 수 있는 챗봇
    context: PDF 요약 같은 추가 문맥 (선택)
    """
    client = _client()
    prompt = f"""
너는 학생들의 질문에 답하는 학습 조교야.
학생의 질문에 대해 간단하고 명확하게 설명해줘.

질문: {question}

참고 문맥:
{context}
"""
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.4,
    )
    return resp.choices[0].message.content.strip()