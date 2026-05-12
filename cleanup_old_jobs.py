"""30일 지난 작업의 DB 상태 업데이트 + R2 파일 삭제

사용법:
  python cleanup_old_jobs.py           # 기본 30일
  python cleanup_old_jobs.py --days 7  # 7일로 변경
"""

import sys
import datetime
from db.database import init_db, SessionLocal
from db.models import Job
from core.time_utils import utc_now_naive


def main():
    days = 30
    if "--days" in sys.argv:
        idx = sys.argv.index("--days")
        if idx + 1 < len(sys.argv):
            days = int(sys.argv[idx + 1])

    init_db()
    db = SessionLocal()

    try:
        cutoff = utc_now_naive() - datetime.timedelta(days=days)
        expired_jobs = (
            db.query(Job)
            .filter(Job.completed_at < cutoff)
            .filter(Job.files_expired_at.is_(None))
            .all()
        )

        if not expired_jobs:
            print(f"{days}일 이상 된 미만료 작업이 없습니다.")
            return

        print(f"{len(expired_jobs)}개 작업 만료 처리 중...")

        for job in expired_jobs:
            job.files_expired_at = utc_now_naive()
            job.video_path = None
            print(f"  - {job.id} (완료: {job.completed_at})")

        db.commit()
        print("완료.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
