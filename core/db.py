import os
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, Float
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from dotenv import load_dotenv

load_dotenv()

# 우선순위: 외부 DB(DATABASE_URL) → 없으면 SQLite(DB_PATH)
DATABASE_URL = os.getenv("DATABASE_URL")
DB_PATH = os.getenv("DB_PATH", "./data/db/app.db")

def _force_psycopg2_and_ssl(url: str) -> str:
    if not url:
        return url
    # 드라이버 접두사 보정
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif url.startswith("postgresql://") and "+psycopg2" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    # sslmode=require 강제
    if "sslmode=" not in url:
        url += ("&" if "?" in url else "?") + "sslmode=require"
    return url

if DATABASE_URL:
    DATABASE_URL = _force_psycopg2_and_ssl(DATABASE_URL)
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_recycle=300,
        echo=False,
    )
else:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    engine = create_engine(
        f"sqlite:///{DB_PATH}",
        connect_args={"check_same_thread": False},
        echo=False,
    )

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

# -------------------------------
# Models
# -------------------------------
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    name = Column(String(100), nullable=False)
    school = Column(String(255), nullable=True)
    password_hash = Column(String(255), nullable=False)
    plan = Column(String(20), default="pending")   # pending / free / pro
    quota_total = Column(Integer, default=10)
    quota_used = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    documents = relationship("Document", back_populates="user")
    questions = relationship("Question", back_populates="user")

class Document(Base):
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    filename = Column(String(255), nullable=False)
    text_preview = Column(Text, nullable=True)
    full_text = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="documents")
    questions = relationship("Question", back_populates="document")

class Question(Base):
    __tablename__ = "questions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=True)
    prompt_text = Column(Text, nullable=True)
    answer_text = Column(Text, nullable=True)
    kind = Column(String(50), default="서술형")
    difficulty = Column(String(10), default="중")
    score = Column(Float, nullable=True)
    meta_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="questions")
    document = relationship("Document", back_populates="questions")

# -------------------------------
# Helpers
# -------------------------------
def init_db():
    Base.metadata.create_all(bind=engine)

def get_session():
    return SessionLocal()

def test_db_connection():
    """연결 미리 점검해서 에러를 표면화(디버깅용)."""
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SELECT 1")
        return True, "OK"
    except Exception as e:
        # 이 메시지는 Streamlit Cloud 로그(Manage app → Logs)에서 그대로 볼 수 있음
        return False, f"{type(e).__name__}: {e}"
