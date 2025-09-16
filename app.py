import os, json, uuid, requests
import streamlit as st
from dotenv import load_dotenv
from streamlit_oauth import OAuth2Component

from core.db import init_db, get_session, Document, Question, User
from core.exam import (
    extract_text_from_pdfs,
    generate_questions,
    get_model_answer_and_keys,
    score_answer,
    best_source_page,
    ask_chatbot
)

# --------------------------------
# 초기 설정
# --------------------------------
load_dotenv()
st.set_page_config(page_title="AI 시험문제 생성기", page_icon="📘", layout="wide")
init_db()

# --------------------------------
# 세션 상태값
# --------------------------------
def route_set(r): st.session_state["route"] = r
for key, default in {
    "route": "login",
    "answers": {},
    "current_batch_id": None,
    "batch_context_pages": [],
    "current_q_ids": [],
    "chat_history": [],
    "pending_signup": None,
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# --------------------------------
# 상단 네비게이션
# --------------------------------
def render_navbar():
    if "user" not in st.session_state or not st.session_state["user"]:
        return
    col1, col2, col3, col4 = st.columns([5,1,1,1])
    with col2:
        if st.button("홈"):
            route_set("landing"); st.rerun()
    with col3:
        if st.button("이용내역"):
            route_set("history"); st.rerun()
    with col4:
        if st.button("로그아웃"):
            st.session_state.clear(); st.rerun()

# --------------------------------
# 로그인/회원가입
# --------------------------------
def login_view():
    st.title("AI 시험문제 생성기")

    # ✅ 구글 로그인
    st.subheader("구글 로그인")
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    redirect_uri = os.getenv("OAUTH_REDIRECT_URI", "http://localhost:8501")

    if client_id and client_secret:
        oauth = OAuth2Component(
            client_id=client_id,
            client_secret=client_secret,
            authorize_endpoint="https://accounts.google.com/o/oauth2/auth",
            token_endpoint="https://oauth2.googleapis.com/token",
        )
        result = oauth.authorize_button(
            name="구글 계정으로 로그인",
            scope="openid email profile",
            key="google_oauth",
            redirect_uri=redirect_uri
        )
        if result and "token" in result:
            access_token = result["token"]["access_token"]
            resp = requests.get(
                "https://www.googleapis.com/oauth2/v3/userinfo",
                headers={"Authorization": f"Bearer {access_token}"}
            )
            user_info = resp.json()
            email, name = user_info.get("email"), user_info.get("name", "사용자")

            with get_session() as db:
                u = db.query(User).filter(User.email == email).first()
                if not u:
                    if email == "admin@exam.com":
                        u = User(email=email, name=name,
                                 password_hash="", plan="pro",
                                 quota_total=9999, quota_used=0)
                    else:
                        u = User(email=email, name=name,
                                 password_hash="", plan="free",
                                 quota_total=10, quota_used=0)
                    db.add(u); db.commit(); db.refresh(u)

                st.session_state["user"] = {
                    "id": u.id, "email": u.email, "name": u.name,
                    "school": u.school, "plan": u.plan,
                    "quota_used": u.quota_used, "quota_total": u.quota_total
                }
                route_set("admin" if u.email == "admin@exam.com" else "landing")
                st.rerun()
    else:
        st.info("구글 OAuth Client ID/Secret이 .env에 설정되지 않았습니다.")

    st.markdown("---")

    # ✅ 일반 로그인
    st.subheader("일반 로그인")
    with st.form("login_form"):
        email = st.text_input("이메일")
        pw = st.text_input("비밀번호", type="password")
        ok = st.form_submit_button("로그인")

    if ok:
        with get_session() as db:
            u = db.query(User).filter(User.email == email).first()
            if u and u.password_hash == pw:
                if u.email == "admin@exam.com":
                    u.plan = "pro"; u.quota_total = 9999
                    db.commit()
                if u.plan == "pending":
                    st.error("관리자 승인 대기중입니다.")
                else:
                    st.session_state["user"] = {
                        "id": u.id, "email": u.email, "name": u.name,
                        "school": u.school, "plan": u.plan,
                        "quota_used": u.quota_used, "quota_total": u.quota_total
                    }
                    route_set("admin" if u.email == "admin@exam.com" else "landing")
                    st.rerun()
            else:
                st.error("로그인 실패")

    st.markdown("---")

    # ✅ 일반 회원가입
    st.subheader("일반 회원가입")
    with st.form("signup_form"):
        name = st.text_input("이름")
        school = st.text_input("학교명")
        email2 = st.text_input("이메일")
        pw1 = st.text_input("비밀번호", type="password")
        pw2 = st.text_input("비밀번호 확인", type="password")
        ok2 = st.form_submit_button("가입하기")

    if ok2:
        if pw1 != pw2:
            st.error("비밀번호가 일치하지 않습니다.")
        else:
            with get_session() as db:
                exists = db.query(User).filter(User.email == email2).first()
                if exists:
                    st.error("이미 가입된 이메일입니다.")
                else:
                    if email2 == "admin@exam.com":
                        u = User(email=email2, name=name, school=school,
                                 password_hash=pw1, plan="pro",
                                 quota_total=9999, quota_used=0)
                        msg = "관리자 계정으로 가입 완료!"
                    else:
                        u = User(email=email2, name=name, school=school,
                                 password_hash=pw1, plan="pending",
                                 quota_total=10, quota_used=0)
                        msg = "회원가입 완료! 관리자 승인 후 이용 가능합니다."
                    db.add(u); db.commit()
                    st.success(msg)

# --------------------------------
# 랜딩
# --------------------------------
def landing_view():
    st.header("나만의 문제를 만들어보세요")
    with get_session() as db:
        u = db.query(User).get(st.session_state["user"]["id"])
        st.info(f"요금제: {u.plan.upper()} | 사용량: {u.quota_used}/{u.quota_total if u.plan=='free' else '무제한'}")
    if st.button("시작하기"): route_set("upload"); st.rerun()

# --------------------------------
# PDF 업로드 & 문제 생성
# --------------------------------
# --------------------------------
# PDF 업로드 & 문제 생성
# --------------------------------
def upload_view():
    st.header("PDF 업로드")
    with get_session() as db:
        u = db.query(User).get(st.session_state["user"]["id"])
        if u.plan=="free" and u.quota_used>=u.quota_total:
            st.error("무료 체험을 모두 사용했습니다."); return

    files = st.file_uploader("PDF 업로드", type=["pdf"], accept_multiple_files=True)
    diff = st.selectbox("난이도", ["하","중","상"], 1)
    num_q = st.slider("문항 수", 3, 20, 8)
    q_type = st.selectbox("문제 유형", ["서술형","객관식","OX퀴즈"])
    style = st.text_input("출제 스타일 (선택)")

    if st.button("문제 생성"):
        if not files: st.error("PDF를 올려주세요"); return

        with st.spinner("문제 생성 중..."):
            pages = extract_text_from_pdfs(files)
            st.session_state["batch_context_pages"] = pages
            full_text = "\n\n".join([p["text"] for p in pages])[:15000]

            with get_session() as db:
                doc = Document(
                    user_id=st.session_state["user"]["id"],
                    filename=", ".join([f.name for f in files]),
                    text_preview=full_text[:800],
                    full_text=full_text
                )
                db.add(doc); db.commit(); db.refresh(doc)

                # ✅ 문제 생성
                qs = generate_questions(full_text, num_q, diff, style, [], q_type)

                # ✅ 배치 ID 부여
                batch_id = str(uuid.uuid4())[:8]
                st.session_state["current_batch_id"] = batch_id

                # ✅ 각 문제에 대해 '모범답안/키워드/출처'를 미리 생성해서 meta_json에 저장
                q_ids = []
                for qtext in qs:
                    try:
                        mk = get_model_answer_and_keys(qtext, pages[:10], diff)
                    except Exception:
                        mk = {"model_answer": "", "key_points": []}

                    try:
                        src = best_source_page(qtext, mk.get("model_answer",""), pages)
                    except Exception:
                        src = None

                    meta = {
                        "batch_id": batch_id,
                        "model_answer": mk.get("model_answer",""),
                        "key_points": mk.get("key_points", []),
                        "source": src
                    }

                    row = Question(
                        user_id=doc.user_id,
                        document_id=doc.id,
                        prompt_text=qtext,
                        difficulty=diff,
                        kind=q_type,
                        meta_json=json.dumps(meta, ensure_ascii=False)
                    )
                    db.add(row); db.commit(); db.refresh(row)
                    q_ids.append(row.id)

                # ✅ 무료 플랜 사용량 증가
                if u.plan=="free":
                    u.quota_used += len(q_ids)
                    db.commit()

            st.session_state["current_q_ids"] = q_ids
            route_set("quiz"); st.rerun()

# --------------------------------
# 문제 풀이 + 챗봇
# --------------------------------
def quiz_view():
    st.header("문제 풀이")
    with get_session() as db:
        qrows = db.query(Question).filter(
            Question.id.in_(st.session_state["current_q_ids"])
        ).all()

    # 입력 UI
    for idx, q in enumerate(qrows, 1):
        st.subheader(f"Q{idx}. {q.prompt_text}")
        st.session_state["answers"][q.id] = st.text_area(
            f"답안 {idx}",
            value=st.session_state["answers"].get(q.id,""),
            key=f"ans_{q.id}"
        )

    if st.button("제출하기"):
        with st.spinner("채점 중..."):
            with get_session() as db:
                for q in qrows:
                    meta = json.loads(q.meta_json or "{}")

                    # 메타가 혹시 비어있으면 안전하게 생성
                    if not meta.get("model_answer") or "key_points" not in meta:
                        try:
                            mk = get_model_answer_and_keys(q.prompt_text, st.session_state["batch_context_pages"][:10], q.difficulty)
                        except Exception:
                            mk = {"model_answer": "", "key_points": []}
                        try:
                            src = best_source_page(q.prompt_text, mk.get("model_answer",""), st.session_state["batch_context_pages"])
                        except Exception:
                            src = None
                        meta.update({
                            "model_answer": mk.get("model_answer",""),
                            "key_points": mk.get("key_points", []),
                            "source": src
                        })

                    ans = (st.session_state["answers"].get(q.id,"") or "").strip()

                    # 빈 답안은 0점 처리
                    if ans:
                        try:
                            sc = score_answer(ans, meta.get("model_answer",""), meta.get("key_points", []))
                            score_val = sc.get("total", 0)
                        except Exception:
                            score_val = 0
                    else:
                        score_val = 0

                    q.answer_text = ans
                    q.score = score_val
                    q.meta_json = json.dumps(meta, ensure_ascii=False)
                    db.commit()

        route_set("results"); st.rerun()

# --------------------------------
# 결과
# --------------------------------
def results_view():
    st.header("채점 결과")

    with get_session() as db:
        qrows = db.query(Question).filter(
            Question.id.in_(st.session_state["current_q_ids"])
        ).all()

    pdf_stats = {}

    for idx, q in enumerate(qrows, 1):
        meta = json.loads(q.meta_json or "{}")
        model_answer = meta.get("model_answer","")
        key_points = meta.get("key_points", [])
        src = meta.get("source") or {}

        # 점수 None 방지
        score_val = q.score if q.score is not None else 0

        st.subheader(f"문제 {idx}")
        st.write(f"**문제:** {q.prompt_text}")
        st.write(f"**내 답:** {q.answer_text or '(미작성)'}")
        st.write(f"**점수:** {score_val}/10")
        st.write(f"**모범답안:** {model_answer or '(정보 없음)'}")
        st.write(f"**주요 키워드:** {', '.join(key_points) if key_points else '(없음)'}")

        if src:
            st.write(f"**출처:** {src.get('filename','')} p.{src.get('page','')}")
            fname = src.get("filename", "알 수 없음")
            pdf_stats.setdefault(fname, {"correct":0, "total":0})
            pdf_stats[fname]["total"] += 1
            if score_val >= 7:
                pdf_stats[fname]["correct"] += 1
        else:
            st.write("**출처:** (정보 없음)")

        st.markdown("---")

    # ✅ 전체 피드백
    st.subheader("📌 전체 피드백")
    overall_text = "\n".join([
        f"문제: {q.prompt_text}\n내 답: {q.answer_text or '(미작성)'}\n점수: {(q.score if q.score is not None else 0)}/10\n모범답안: {json.loads(q.meta_json or '{}').get('model_answer','')}"
        for q in qrows
    ])
    feedback_prompt = f"""
    아래는 한 학생의 시험 답안과 채점 결과야. 학생의 강점과 약점을 분석해서
    1) 전반적인 피드백
    2) 부족한 영역
    3) 앞으로 공부하면 좋을 부분
    을 간단하게 정리해줘.

    {overall_text}
    """
    feedback = ask_chatbot("전체 피드백 작성", feedback_prompt)
    st.write(feedback)

    # ✅ PDF별 분석
    st.subheader("📊 PDF별 정답 통계")
    for fname, stat in pdf_stats.items():
        correct = stat["correct"]; total = stat["total"]
        st.write(f"- {fname}: {correct}/{total} 문제 정답 (정답률 {correct/total*100:.1f}%)")
        if total and correct/total < 0.6:
            st.warning(f"👉 {fname} 내용을 더 복습하는 게 좋아요.")

    if st.button("이용내역 보기"):
        route_set("history"); st.rerun()

# --------------------------------
# 이용내역 (개인)
# --------------------------------
def history_view():
    st.header("📖 나의 이용내역 (PDF별)")

    user_id = st.session_state["user"]["id"]
    with get_session() as db:
        # 내가 업로드한 문서들 (최근 업로드 순)
        docs = db.query(Document)\
                 .filter(Document.user_id == user_id)\
                 .order_by(Document.created_at.desc())\
                 .all()

    if not docs:
        st.info("아직 기록이 없습니다.")
        return

    for doc in docs:
        # 이 문서에 속한 모든 문제 가져오기 (생성 순)
        with get_session() as db:
            qrows = db.query(Question)\
                      .filter(Question.document_id == doc.id)\
                      .order_by(Question.created_at.asc())\
                      .all()

        if not qrows:
            # 문제 없으면 문서만 남아있을 수 있으니 삭제 버튼만 제공
            with st.expander(f"📄 {doc.filename} — (문항 없음)"):
                if st.button("이 PDF 전체 결과 삭제", key=f"del_doc_empty_{doc.id}"):
                    with get_session() as db:
                        # 문서 삭제
                        d = db.query(Document).get(doc.id)
                        if d: db.delete(d)
                        db.commit()
                    st.success("삭제되었습니다."); st.rerun()
            continue

        # 세트(batch_id)로 묶기
        batches = {}
        for q in qrows:
            meta = json.loads(q.meta_json or "{}")
            bid = meta.get("batch_id", "unknown")
            batches.setdefault(bid, []).append(q)

        # 문서 레벨 요약
        all_scores = [(q.score if q.score is not None else 0) for q in qrows]
        avg_doc = sum(all_scores) / len(all_scores) if all_scores else 0.0
        title = f"📄 {doc.filename} — {len(qrows)}문항 · 평균 {avg_doc:.1f}/10"

        with st.expander(title, expanded=False):
            # 문서 전체 삭제 버튼
            colA, colB = st.columns([1,4])
            with colA:
                if st.button("이 PDF 전체 결과 삭제", key=f"del_doc_{doc.id}"):
                    with get_session() as db:
                        # 해당 문서의 모든 문제 삭제
                        db.query(Question)\
                          .filter(Question.document_id == doc.id)\
                          .delete(synchronize_session=False)
                        # 문서 삭제
                        d = db.query(Document).get(doc.id)
                        if d: db.delete(d)
                        db.commit()
                    st.success("삭제되었습니다."); st.rerun()
            with colB:
                st.caption(f"업로드일: {getattr(doc, 'created_at', '')}")

            st.markdown("---")

            # 세트별(배치)로 상세 표시
            for bid, qs in batches.items():
                batch_scores = [(qq.score if qq.score is not None else 0) for qq in qs]
                avg_batch = sum(batch_scores) / len(batch_scores) if batch_scores else 0.0
                with st.expander(f"🗂 세트 {bid} — {len(qs)}문항 · 평균 {avg_batch:.1f}/10", expanded=False):
                    # 세트 삭제 버튼
                    if st.button("이 세트 삭제", key=f"del_batch_{doc.id}_{bid}"):
                        ids = [qq.id for qq in qs]
                        with get_session() as db:
                            db.query(Question)\
                              .filter(Question.id.in_(ids))\
                              .delete(synchronize_session=False)
                            db.commit()
                        st.success("세트가 삭제되었습니다."); st.rerun()

                    # 문제들 나열
                    for idx, q in enumerate(qs, 1):
                        meta = json.loads(q.meta_json or "{}")
                        model_answer = meta.get("model_answer", "")
                        key_points = meta.get("key_points", [])
                        src = meta.get("source") or {}

                        score_val = q.score if q.score is not None else 0

                        st.markdown(f"**문제 {idx}.** {q.prompt_text}")
                        st.markdown(f"- **내 답:** {q.answer_text or '(미작성)'}")
                        st.markdown(f"- **점수:** {score_val}/10")
                        st.markdown(f"- **모범답안:** {model_answer or '(정보 없음)'}")
                        st.markdown(f"- **주요 키워드:** {', '.join(key_points) if key_points else '(없음)'}")
                        if src:
                            st.markdown(f"- **출처:** {src.get('filename','')} p.{src.get('page','')}")
                        else:
                            st.markdown(f"- **출처:** (정보 없음)")
                        st.markdown("---")
# --------------------------------
# 관리자 페이지
# --------------------------------
def admin_view():
    st.header("관리자 페이지")

    if st.button("새로고침"): st.rerun()
    with get_session() as db:
        users = db.query(User).order_by(User.created_at.desc()).all()
    if not users:
        st.info("등록된 사용자가 없습니다."); return

    for u in users:
        col1, col2, col3, col4, col5 = st.columns([3,2,2,2,2])
        with col1:
            st.write(f"{u.name} · {u.email} · {u.school or '학교 미입력'}")
        with col2: st.write(f"플랜: {u.plan}")
        with col3: st.write(f"사용량: {u.quota_used}/{u.quota_total if u.plan=='free' else '무제한'}")

        with col4:
            if u.email != "admin@exam.com":
                if u.plan == "free":
                    if st.button("FREE → PRO", key=f"to_pro_{u.id}"):
                        with get_session() as db2:
                            uu = db2.query(User).filter(User.id==u.id).first()
                            uu.plan="pro"; db2.commit()
                        st.rerun()
                elif u.plan == "pro":
                    if st.button("PRO → FREE", key=f"to_free_{u.id}"):
                        with get_session() as db2:
                            uu = db2.query(User).filter(User.id==u.id).first()
                            uu.plan="free"; uu.quota_total=10; db2.commit()
                        st.rerun()
        with col5:
            if st.button("상세보기", key=f"detail_{u.id}"):
                st.session_state[f"show_detail_{u.id}"] = not st.session_state.get(f"show_detail_{u.id}", False)

        if st.session_state.get(f"show_detail_{u.id}", False):
            with st.expander(f"📌 {u.email} 상세정보", expanded=True):
                st.write(f"- 이름: {u.name}")
                st.write(f"- 이메일: {u.email}")
                st.write(f"- 학교: {u.school or '학교 미입력'}")
                st.write(f"- 플랜: {u.plan}")
                st.write(f"- 사용량: {u.quota_used}/{u.quota_total if u.plan=='free' else '무제한'}")
                st.write(f"- 비밀번호: {u.password_hash or '구글 로그인 계정'}")

# --------------------------------
# Router
# --------------------------------
def router():
    if "user" not in st.session_state or not st.session_state["user"]: login_view()
    else:
        render_navbar()
        route=st.session_state.get("route","landing")
        if st.session_state["user"]["email"]=="admin@exam.com": admin_view()
        else:
            if route=="landing": landing_view()
            elif route=="upload": upload_view()
            elif route=="quiz": quiz_view()
            elif route=="results": results_view()
            elif route=="history": history_view()

if __name__=="__main__":
    router()