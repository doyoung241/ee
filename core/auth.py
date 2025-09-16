import bcrypt
from sqlalchemy.orm import Session
from .db import User

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except Exception:
        return False

def create_user(db: Session, email: str, name: str, password: str, school: str = None):
    exists = db.query(User).filter(User.email == email).first()
    if exists:
        raise ValueError("이미 등록된 이메일입니다.")
    user = User(
        email=email,
        name=name,
        school=school,              # ✅ 학교명 저장
        password_hash=hash_password(password)
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

def authenticate_user(db: Session, email: str, password: str):
    user = db.query(User).filter(User.email == email).first()
    if not user:
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user