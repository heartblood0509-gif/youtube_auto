"""비밀번호 재설정 이메일 발송"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config import settings


def send_reset_email(to_email: str, reset_link: str):
    """Gmail SMTP로 비밀번호 재설정 이메일 발송"""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "[AI 쇼츠 자동 제작] 비밀번호 재설정"
    msg["From"] = f"AI 쇼츠 자동 제작 <{settings.SMTP_USER}>"
    msg["To"] = to_email

    html = f"""\
    <html>
    <body style="font-family: sans-serif; background: #1a1a2e; color: #e0e0e0; padding: 40px;">
        <div style="max-width: 480px; margin: 0 auto; background: #16213e; border-radius: 12px; padding: 32px;">
            <h2 style="color: #818cf8; margin-top: 0;">비밀번호 재설정</h2>
            <p>아래 버튼을 클릭하여 비밀번호를 재설정하세요.</p>
            <p>이 링크는 <strong>1시간</strong> 후 만료됩니다.</p>
            <a href="{reset_link}"
               style="display: inline-block; background: #818cf8; color: white;
                      padding: 12px 32px; border-radius: 8px; text-decoration: none;
                      font-weight: bold; margin: 16px 0;">
                비밀번호 재설정
            </a>
            <p style="color: #888; font-size: 13px; margin-top: 24px;">
                본인이 요청하지 않았다면 이 이메일을 무시하세요.
            </p>
        </div>
    </body>
    </html>
    """
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT) as server:
        server.starttls()
        server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.send_message(msg)
