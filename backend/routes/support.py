import os
import smtplib
import asyncio
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/support")

SUPPORT_EMAIL = "reversepicksx@gmail.com"


class ContactRequest(BaseModel):
    name: str = ""
    email: str = ""
    message: str


def _send_email_sync(name: str, sender_email: str, message: str):
    smtp_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not smtp_password:
        raise RuntimeError("Email service not configured.")

    subject = f"[ReversePicks Support] Message from {name or sender_email or 'User'}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SUPPORT_EMAIL
    msg["To"] = SUPPORT_EMAIL
    msg["Reply-To"] = sender_email if sender_email else SUPPORT_EMAIL

    plain = (
        f"Name: {name or 'Not provided'}\n"
        f"Email: {sender_email or 'Not provided'}\n\n"
        f"Message:\n{message}"
    )

    html = f"""
    <div style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:24px">
      <h2 style="color:#39FF14;margin-bottom:4px">ReversePicks Support</h2>
      <p style="color:#888;font-size:13px;margin-top:0">New message from the app</p>
      <hr style="border-color:#333;margin:16px 0">
      <table style="width:100%;border-collapse:collapse">
        <tr>
          <td style="color:#aaa;font-size:13px;padding:4px 0;width:80px">Name</td>
          <td style="color:#fff;font-size:14px;padding:4px 0">{name or 'Not provided'}</td>
        </tr>
        <tr>
          <td style="color:#aaa;font-size:13px;padding:4px 0">Email</td>
          <td style="color:#fff;font-size:14px;padding:4px 0">{sender_email or 'Not provided'}</td>
        </tr>
      </table>
      <hr style="border-color:#333;margin:16px 0">
      <p style="color:#aaa;font-size:12px;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px">Message</p>
      <p style="color:#fff;font-size:15px;line-height:1.6;white-space:pre-wrap">{message}</p>
    </div>
    """

    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(SUPPORT_EMAIL, smtp_password)
        server.sendmail(SUPPORT_EMAIL, SUPPORT_EMAIL, msg.as_string())


@router.post("/contact")
async def contact_support(req: ContactRequest):
    if not req.message or not req.message.strip():
        return {"success": False, "error": "Message cannot be empty."}
    try:
        await asyncio.get_event_loop().run_in_executor(
            None,
            _send_email_sync,
            req.name.strip(),
            req.email.strip(),
            req.message.strip(),
        )
        return {"success": True}
    except RuntimeError as e:
        return {"success": False, "error": str(e)}
    except smtplib.SMTPAuthenticationError:
        return {"success": False, "error": "Email service authentication failed. Please try again later."}
    except Exception as e:
        print(f"[SUPPORT EMAIL] Error: {e}")
        return {"success": False, "error": "Failed to send message. Please try again."}
