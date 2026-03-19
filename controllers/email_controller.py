"""
email_controller.py — Email sending for verification codes and password resets.

SMTP configuration is stored in the application's config.json file.
The admin must configure SMTP settings before email features will work.

Security codes are 6-digit numeric strings, valid for 15 minutes.
"""

import random
import smtplib
import ssl
import string
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional, Tuple

from controllers.config_controller import _read_config, _write_config


# ═══════════════════════════════════════════════════════════════════
# SMTP CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

def get_smtp_config() -> dict:
    """Return the SMTP settings from the config file, or empty dict."""
    cfg = _read_config()
    return cfg.get("smtp", {})


def save_smtp_config(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    use_tls: bool = True,
    sender_email: str,
) -> None:
    """Persist SMTP settings to config.json."""
    cfg = _read_config()
    cfg["smtp"] = {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "use_tls": use_tls,
        "sender_email": sender_email,
    }
    _write_config(cfg)


def is_smtp_configured() -> bool:
    """Return True if SMTP settings have been saved."""
    smtp = get_smtp_config()
    return bool(smtp.get("host") and smtp.get("sender_email"))


def test_smtp_connection() -> Tuple[bool, str]:
    """Try connecting to the SMTP server. Returns (success, message)."""
    smtp = get_smtp_config()
    if not smtp.get("host"):
        return False, "SMTP not configured."

    try:
        if smtp.get("use_tls", True):
            context = ssl.create_default_context()
            with smtplib.SMTP(smtp["host"], smtp.get("port", 587), timeout=10) as server:
                server.starttls(context=context)
                server.login(smtp["username"], smtp["password"])
        else:
            with smtplib.SMTP(smtp["host"], smtp.get("port", 25), timeout=10) as server:
                if smtp.get("username"):
                    server.login(smtp["username"], smtp["password"])
        return True, "Connection successful."
    except Exception as exc:
        return False, f"Connection failed: {exc}"


# ═══════════════════════════════════════════════════════════════════
# CODE GENERATION
# ═══════════════════════════════════════════════════════════════════

def generate_security_code() -> str:
    """Generate a random 6-digit security code."""
    return "".join(random.choices(string.digits, k=6))


# ═══════════════════════════════════════════════════════════════════
# EMAIL SENDING
# ═══════════════════════════════════════════════════════════════════

def send_verification_email(recipient_email: str, code: str) -> Tuple[bool, str]:
    """
    Send an email verification code to the given address.

    Returns (success, message).
    """
    return _send_code_email(
        recipient_email=recipient_email,
        code=code,
        subject="FROG — Email Verification Code",
        heading="Email Verification",
        body_text=(
            "Use the following code to verify your email address.\n"
            "This code expires in 15 minutes."
        ),
    )


def send_password_reset_email(recipient_email: str, code: str) -> Tuple[bool, str]:
    """
    Send a password reset code to the given address.

    Returns (success, message).
    """
    return _send_code_email(
        recipient_email=recipient_email,
        code=code,
        subject="FROG — Password Reset Code",
        heading="Password Reset",
        body_text=(
            "A password reset was requested for your account.\n"
            "Use the following code to reset your password.\n"
            "This code expires in 15 minutes.\n\n"
            "If you did not request this, you can ignore this email."
        ),
    )


def _send_code_email(
    *,
    recipient_email: str,
    code: str,
    subject: str,
    heading: str,
    body_text: str,
) -> Tuple[bool, str]:
    """Internal helper to send a code email via SMTP."""
    smtp = get_smtp_config()
    if not smtp.get("host"):
        return False, "SMTP is not configured. Ask your administrator to set up email settings."

    sender = smtp["sender_email"]

    # Build a simple HTML email.
    html_body = f"""\
<html>
<body style="font-family: Arial, sans-serif; padding: 20px;">
  <h2>{heading}</h2>
  <p>{body_text.replace(chr(10), '<br>')}</p>
  <div style="background: #f4f4f4; padding: 16px; border-radius: 8px;
              font-size: 28px; font-weight: bold; letter-spacing: 8px;
              text-align: center; margin: 20px 0;">
    {code}
  </div>
  <p style="color: #888; font-size: 12px;">
    This is an automated message from FROG (Requirements Manager).
  </p>
</body>
</html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient_email

    # Plain text fallback.
    plain = f"{heading}\n\n{body_text}\n\nYour code: {code}\n"
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        if smtp.get("use_tls", True):
            context = ssl.create_default_context()
            with smtplib.SMTP(smtp["host"], smtp.get("port", 587), timeout=15) as server:
                server.starttls(context=context)
                server.login(smtp["username"], smtp["password"])
                server.sendmail(sender, recipient_email, msg.as_string())
        else:
            with smtplib.SMTP(smtp["host"], smtp.get("port", 25), timeout=15) as server:
                if smtp.get("username"):
                    server.login(smtp["username"], smtp["password"])
                server.sendmail(sender, recipient_email, msg.as_string())
        return True, "Email sent successfully."
    except Exception as exc:
        return False, f"Failed to send email: {exc}"
