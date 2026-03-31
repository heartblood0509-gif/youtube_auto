"""관리자 계정 생성 스크립트

사용법:
  python create_admin.py admin@example.com 닉네임 비밀번호
  python create_admin.py admin@example.com 닉네임          (비밀번호 직접 입력)
"""

import sys
import getpass
from db.database import init_db, SessionLocal
from db.models import User
from core.security import hash_password
import uuid


def main():
    if len(sys.argv) < 3:
        print("사용법: python create_admin.py <이메일> <닉네임> [비밀번호]")
        sys.exit(1)

    email = sys.argv[1]
    nickname = sys.argv[2]
    password = sys.argv[3] if len(sys.argv) > 3 else getpass.getpass("비밀번호: ")

    if len(password) < 8:
        print("오류: 비밀번호는 8자 이상이어야 합니다")
        sys.exit(1)

    init_db()
    db = SessionLocal()

    try:
        existing = db.query(User).filter(User.email == email).first()
        if existing:
            if existing.role == "admin":
                print(f"이미 관리자 계정입니다: {email}")
                return
            existing.role = "admin"
            if not existing.hashed_password:
                existing.hashed_password = hash_password(password)
            db.commit()
            print(f"기존 계정을 관리자로 승격: {email}")
        else:
            user = User(
                id=uuid.uuid4().hex,
                email=email,
                nickname=nickname,
                hashed_password=hash_password(password),
                role="admin",
                provider="email",
            )
            db.add(user)
            db.commit()
            print(f"관리자 계정 생성 완료: {email}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
