# core/db.py
import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, String, Text, DateTime,
    ForeignKey, Float
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from dotenv import load_dotenv

load_dotenv()

# -------------------------------
# DB 설정
#  - 로컬: SQLite (DB_PATH)
#  - 배포: Postgres (DATABASE_URL) -> 예: postgresql+psycopg2://user:pass@host:5432/db
# -------------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DB_PATH = os.getenv("DB_PATH", "./data/db/app.db")

if DATABASE_URL:
    # 외부(Postgres 등)
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_recycle=1800,
        future=True,
    )
else:
    # 로컬(SQLite)
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    engine = create_engine(
        f"sqlite:///{DB_PATH}",
        connect_args={"check_same_thread": False},
        future=True,
    )

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()

# -------------------------------
# User 모델 (회원)
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

    # 유저 삭제 시 문서/문항도 함께 삭제되도록 cascade 설정
    documents = relationship(
        "Document",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    questions = relationship(
        "Question",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

# -------------------------------
# Document 모델
# -------------------------------
class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    filename = Column(String(255), nullable=False)
    text_preview = Column(Text, nullable=True)
    full_text = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="documents")
    questions = relationship(
        "Question",
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

# -------------------------------
# Question 모델
# -------------------------------
class Question(Base):
    __tablename__ = "questions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    document_id = Column(Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=True)

    prompt_text = Column(Text, nullable=True)
    answer_text = Column(Text, nullable=True)
    kind = Column(String(50), default="서술형")
    difficulty = Column(String(10), default="중")
    score = Column(Float, nullable=True)
    meta_json = Column(Text, nullable=True)  # batch_id / model_answer / key_points / source 저장

    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="questions")
    document = relationship("Document", back_populates="questions")

# -------------------------------
# DB 초기화 / 세션
# -------------------------------
def init_db():
    Base.metadata.create_all(bind=engine)

def get_session():
    # SQLAlchemy Session은 context manager를 지원하므로
    # `with get_session() as db:` 형태로 사용 가능
    return SessionLocal()
