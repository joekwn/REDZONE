"""
REDZONE - check the email (SMTP) settings by sending one test email.

Usage (in the same PowerShell window where the SMTP_ variables are set):
    py scripts\\test_email.py you@example.com

Reads SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM from the environment.
Never prints the password.
"""

from email.message import EmailMessage
import os
import smtplib
import sys


def main():
    if len(sys.argv) != 2:
        sys.exit("Usage: py scripts\\test_email.py you@example.com")
    to = sys.argv[1]
    host = os.environ.get("SMTP_HOST", "").strip()
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("SMTP_FROM") or user

    print(f"SMTP_HOST     = {host or '(not set)'}")
    print(f"SMTP_PORT     = {port}")
    print(f"SMTP_USER     = {user or '(not set)'}")
    print(f"SMTP_PASSWORD = {'set (' + str(len(password)) + ' characters)' if password else '(not set)'}")
    print(f"SMTP_FROM     = {sender or '(not set)'}")
    print(f"REDZONE_BASE_URL = {os.environ.get('REDZONE_BASE_URL', 'http://127.0.0.1:8000 (default)')}")
    if not (host and user and password):
        sys.exit("\nSTOP: SMTP_HOST, SMTP_USER and SMTP_PASSWORD must all be set.")

    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = "REDZONE test email", sender, to
    msg.set_content("If you can read this, REDZONE can send email alerts.\n")
    try:
        smtp = smtplib.SMTP_SSL(host, port, timeout=30) if port == 465 else smtplib.SMTP(host, port, timeout=30)
        with smtp as s:
            if port != 465:
                s.starttls()
            s.login(user, password)
            s.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        sys.exit("\nFAILED: the email server refused the user name or password.\n"
                 "For Brevo: SMTP_USER = the SMTP login shown on Brevo's SMTP page, SMTP_PASSWORD = an SMTP key\n"
                 "(not the API key, not your Brevo password). SMTP_FROM must be a sender verified in Brevo.")
    except Exception as exc:
        sys.exit(f"\nFAILED: {type(exc).__name__}: {exc}")
    print(f"\nOK - test email sent to {to}. Check the inbox (and the spam folder).")


if __name__ == "__main__":
    main()
