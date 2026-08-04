"""Email and SMS notification service.

Uses the Gmail REST API over HTTPS for email (works on Render free tier,
which blocks SMTP ports 25/465/587).
Falls back to Gmail SMTP when OAuth credentials are not configured.
Uses Twilio for SMS (free trial: $15 credit).
"""

import base64
import logging
import smtplib
import socket
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

from .config import settings

logger = logging.getLogger(__name__)

_ACCESS_TOKEN_CACHE: dict[str, str] = {}


def _gmail_access_token() -> str:
    """Return a fresh OAuth2 access token for the Gmail API (cached)."""
    if _ACCESS_TOKEN_CACHE.get("token"):
        return _ACCESS_TOKEN_CACHE["token"]
    resp = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": settings.gmail_refresh_token,
            "client_id": settings.gmail_client_id,
            "client_secret": settings.gmail_client_secret,
        },
        timeout=20,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"Gmail token refresh failed: {resp.status_code} {resp.text}")
    _ACCESS_TOKEN_CACHE["token"] = resp.json()["access_token"]
    return _ACCESS_TOKEN_CACHE["token"]


def _send_via_gmail_api(to: str, subject: str, html_body: str) -> None:
    """Send an email through the Gmail REST API over HTTPS."""
    msg = MIMEText(html_body, "html", "utf-8")
    msg["From"] = settings.email_from
    msg["To"] = to
    msg["Subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    resp = httpx.post(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        headers={"Authorization": f"Bearer {_gmail_access_token()}"},
        json={"raw": raw},
        timeout=20,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"Gmail API send failed: {resp.status_code} {resp.text}")


def _connect_smtp_socket(host: str, port: int) -> socket.socket:
    """Connect to the SMTP server, preferring IPv4.

    Free Render instances have no IPv6 route, but smtplib's socket lookup
    returns IPv6 addresses first, producing '[Errno 101] Network is
    unreachable'. Trying IPv4 first avoids that.
    """
    last_err: Exception | None = None
    for family in (socket.AF_INET, socket.AF_INET6):
        try:
            addrinfos = socket.getaddrinfo(host, port, family, socket.SOCK_STREAM)
        except socket.gaierror as e:
            last_err = e
            continue
        for addr in addrinfos:
            sock = socket.socket(family, addr[1])
            sock.settimeout(20)
            try:
                sock.connect(addr[4])
                return sock
            except OSError as e:
                last_err = e
                sock.close()
    raise OSError(f"Could not connect to SMTP {host}:{port} ({last_err})")


def _send_via_smtp_587(user: str, password: str, to: str, subject: str, html_body: str) -> None:
    """STARTTLS over port 587, forcing an IPv4 socket."""
    msg = _build_message(to, subject, html_body)
    server = smtplib.SMTP(timeout=20)
    server._get_socket = lambda host, port, timeout: _connect_smtp_socket(  # noqa: SLF001
        host, port
    )
    try:
        server.connect(settings.smtp_host, settings.smtp_port)
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(user, password)
        server.sendmail(settings.email_from, [to], msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:
            pass


def _send_via_smtp_465(user: str, password: str, to: str, subject: str, html_body: str) -> None:
    """SSL over port 465 (fallback if 587 is blocked)."""
    msg = _build_message(to, subject, html_body)
    server = smtplib.SMTP_SSL(settings.smtp_host, 465, timeout=20)
    try:
        server.login(user, password)
        server.sendmail(settings.email_from, [to], msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:
            pass


def _build_message(to: str, subject: str, html_body: str) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["From"] = settings.email_from
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    return msg


def send_email(to: str, subject: str, html_body: str) -> bool:
    """Send an email. Returns True on success.

    Prefers the Gmail REST API (HTTPS, works on Render free tier) and falls
    back to Gmail SMTP when OAuth credentials are not configured.
    """
    if settings.gmail_refresh_token:
        try:
            _send_via_gmail_api(to, subject, html_body)
            logger.info(f"Email sent to {to} (Gmail API)")
            return True
        except Exception as e:
            logger.error(f"Gmail API email send failed: {e}")
            return False

    user = settings.smtp_user
    password = settings.smtp_password
    if not user or not password:
        logger.info(f"[EMAIL DISABLED] To: {to} | Subject: {subject}")
        print(f"[EMAIL] To: {to} | Subject: {subject} | Body: {html_body}")
        return False
    try:
        _send_via_smtp_587(user, password, to, subject, html_body)
        logger.info(f"Email sent to {to} (587 STARTTLS)")
        return True
    except Exception as e587:
        try:
            _send_via_smtp_465(user, password, to, subject, html_body)
            logger.info(f"Email sent to {to} (465 SSL fallback)")
            return True
        except Exception as e465:
            logger.error(f"Email send failed (587: {e587}) (465: {e465})")
            return False


def send_sms(to: str, body: str) -> bool:
    """Send an SMS via Twilio. Returns True on success."""
    sid = settings.twilio_account_sid
    token = settings.twilio_auth_token
    from_num = settings.twilio_from_number
    if not sid or not token or not from_num:
        logger.info(f"[SMS DISABLED] To: {to} | Body: {body}")
        print(f"[SMS] To: {to} | Body: {body}")
        return False
    try:
        resp = httpx.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            auth=(sid, token),
            data={"To": to, "From": from_num, "Body": body},
            timeout=15,
        )
        if resp.status_code < 300:
            logger.info(f"SMS sent to {to}: {resp.json().get('sid')}")
            return True
        else:
            logger.error(f"Twilio error {resp.status_code}: {resp.text}")
            return False
    except Exception as e:
        logger.error(f"SMS send failed: {e}")
        return False


def send_otp_email(to: str, code: str, purpose: str = "verification") -> bool:
    """Send an OTP code via email."""
    labels = {
        "register_email": "email verification",
        "register_mobile": "mobile verification",
        "forgot_password": "password reset",
        "forgot_userid": "user ID recovery",
    }
    label = labels.get(purpose, purpose)
    subject = f"Your Splitwise Clone verification code"
    html = f"""
    <div style="font-family: sans-serif; max-width: 480px; margin: 0 auto;">
        <h2 style="color: #00BFA5;">Splitwise Clone</h2>
        <p>Your <strong>{label}</strong> code is:</p>
        <div style="font-size: 32px; font-weight: bold; letter-spacing: 8px;
                    color: #333; background: #f5f5f5; padding: 16px;
                    text-align: center; border-radius: 8px;">
            {code}
        </div>
        <p style="color: #666; margin-top: 16px;">
            This code expires in 10 minutes. If you didn't request this,
            please ignore this email.
        </p>
    </div>
    """
    return send_email(to, subject, html)


def send_otp_sms(to: str, code: str, purpose: str = "verification") -> bool:
    """Send an OTP code via SMS."""
    labels = {
        "register_email": "email verification",
        "register_mobile": "mobile verification",
        "forgot_password": "password reset",
        "forgot_userid": "user ID recovery",
    }
    label = labels.get(purpose, purpose)
    body = f"[Splitwise Clone] Your {label} code is: {code}. Expires in 10 minutes."
    return send_sms(to, body)


def _money(cents: int, currency: str) -> str:
    return f"{cents / 100:,.2f} {currency}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _html_body(heading: str, rows: list[tuple[str, str]], note: str = "") -> str:
    """Build a compact HTML email body with a key/value info table."""
    rows_html = "".join(
        f"<tr>"
        f'<td style="padding:6px 12px;color:#666;font-size:13px;white-space:nowrap;">{k}</td>'
        f'<td style="padding:6px 12px;font-weight:600;color:#1A1C1E;text-align:right;">{v}</td>'
        f"</tr>"
        for k, v in rows
    )
    note_html = f'<p style="color:#999;font-size:12px;margin-top:14px;">{note}</p>' if note else ""
    return (
        '<div style="font-family:-apple-system,\'Segoe UI\',Roboto,Helvetica,Arial,sans-serif;'
        'max-width:520px;margin:0 auto;border:1px solid #e6e6e6;border-radius:12px;overflow:hidden;">'
        '<div style="background:#00BFA5;padding:16px 20px;">'
        '<div style="font-size:17px;font-weight:700;color:#ffffff;">Splitwise Clone</div>'
        "</div>"
        '<div style="padding:20px 20px 12px;">'
        f'<h2 style="margin:0;font-size:16px;color:#1A1C1E;">{heading}</h2>'
        f'<table style="width:100%;border-collapse:collapse;margin-top:8px;">{rows_html}</table>'
        f"{note_html}</div>"
        '<div style="background:#f7f7f7;padding:12px 20px;font-size:12px;color:#999;">'
        "You are receiving this because you use the Splitwise Clone app.</div></div>"
    )


def notify_expense_added(
    group_name: str,
    expense_desc: str,
    amount_cents: int,
    currency: str,
    category: str,
    added_by_name: str,
    expense_date: str,
    members: list[dict],
) -> None:
    """Notify group members about a new expense (email + in-app)."""
    for m in members:
        send_email(
            m["email"],
            f"New expense in {group_name}: {expense_desc}",
            _html_body(
                f"New expense in {group_name}",
                [
                    ("Description", expense_desc),
                    ("Amount", _money(amount_cents, currency)),
                    ("Category", category),
                    ("Added by", added_by_name),
                    ("Date", expense_date),
                ],
                note="Open the app to see your share and settle up.",
            ),
        )


def notify_group_created(group_name: str, members: list[dict], creator_name: str) -> None:
    """Notify all members about group creation."""
    for m in members:
        send_email(
            m["email"],
            f"You've been added to {group_name}",
            _html_body(
                f"New group: {group_name}",
                [
                    ("Group", group_name),
                    ("Created by", creator_name),
                    ("Group members", f"{len(members)} (including you)"),
                ],
                note="Start splitting expenses with this group in the app.",
            ),
        )


def notify_friend_added(friend_email: str, added_by_name: str) -> None:
    """Notify a user they've been added as a friend."""
    send_email(
        friend_email,
        f"{added_by_name} added you as a friend",
        _html_body(
            "You have a new friend",
            [("Added by", added_by_name), ("Status", "Connected")],
            note="You can now split expenses with each other.",
        ),
    )


def notify_payment_recorded(from_name: str, to_name: str, amount_cents: int, currency: str, from_email: str, to_email: str) -> None:
    """Notify both parties about a settlement."""
    amount = _money(amount_cents, currency)
    now = _utc_now()
    send_email(
        from_email,
        f"Payment recorded to {to_name}",
        _html_body(
            "Payment recorded",
            [("You paid", f"{amount} to {to_name}"), ("Date", now), ("Status", "Settlement recorded")],
            note="The balance in your app has been updated.",
        ),
    )
    send_email(
        to_email,
        f"Payment received from {from_name}",
        _html_body(
            "Payment received",
            [("Received", f"{amount} from {from_name}"), ("Date", now), ("Status", "Settlement recorded")],
            note="The balance in your app has been updated.",
        ),
    )


def notify_expense_updated(
    group_name: str,
    expense_desc: str,
    amount_cents: int,
    currency: str,
    updated_by_name: str,
    members: list[dict],
) -> None:
    for m in members:
        send_email(
            m["email"],
            f"Expense updated in {group_name}: {expense_desc}",
            _html_body(
                f"Expense updated in {group_name}",
                [
                    ("Description", expense_desc),
                    ("Amount", _money(amount_cents, currency)),
                    ("Updated by", updated_by_name),
                ],
                note="Open the app to review the updated expense.",
            ),
        )


def notify_expense_deleted(
    group_name: str,
    expense_desc: str,
    amount_cents: int,
    currency: str,
    deleted_by_name: str,
    members: list[dict],
) -> None:
    for m in members:
        send_email(
            m["email"],
            f"Expense removed from {group_name}",
            _html_body(
                f"Expense removed from {group_name}",
                [
                    ("Description", expense_desc),
                    ("Amount", _money(amount_cents, currency)),
                    ("Removed by", deleted_by_name),
                ],
                note="This expense is no longer part of the group balances.",
            ),
        )


def notify_comment_added(
    expense_desc: str,
    amount_cents: int,
    currency: str,
    commenter_name: str,
    comment_text: str,
    members: list[dict],
) -> None:
    for m in members:
        send_email(
            m["email"],
            f"New comment on {expense_desc}",
            _html_body(
                f"New comment",
                [
                    ("Expense", expense_desc),
                    ("Amount", _money(amount_cents, currency)),
                    ("Comment by", commenter_name),
                    ("Comment", comment_text),
                ],
                note="Tap the comment in the app to reply.",
            ),
        )


def notify_member_joined(group_name: str, joiner_name: str, members: list[dict]) -> None:
    for m in members:
        send_email(
            m["email"],
            f"New member in {group_name}",
            _html_body(
                f"New member in {group_name}",
                [
                    ("Group", group_name),
                    ("New member", joiner_name),
                    ("Joined", _utc_now()),
                ],
            ),
        )


def notify_member_added(group_name: str, added_user_name: str, members: list[dict]) -> None:
    for m in members:
        send_email(
            m["email"],
            f"You were added to {group_name}",
            _html_body(
                f"New group membership",
                [("Group", group_name), ("Added by", added_user_name), ("Date", _utc_now())],
                note="Open the app to see the group.",
            ),
        )


def notify_packing_item_added(group_name: str, item_name: str, members: list[dict]) -> None:
    for m in members:
        if len(members) == 1:
            heading = "Packing item assigned to you"
            rows = [
                ("Item", item_name),
                ("Assigned to", m.get("name", "You")),
                ("Group", group_name),
            ]
        else:
            heading = f"Packing item added in {group_name}"
            rows = [
                ("Item", item_name),
                ("Group", group_name),
                ("For", "Everyone in the group"),
            ]
        send_email(
            m["email"],
            f"Packing item added in {group_name}",
            _html_body(heading, rows, note="Check the packing list in the app."),
        )


def notify_packing_item_toggled(group_name: str, item_name: str, is_checked: bool, toggled_by_name: str, members: list[dict]) -> None:
    status = "completed" if is_checked else "pending"
    label = "Completed" if is_checked else "Pending"
    for m in members:
        send_email(
            m["email"],
            f"Packing item {status} in {group_name}",
            _html_body(
                f"Packing item {status}",
                [
                    ("Item", item_name),
                    ("Status", label),
                    ("Changed by", toggled_by_name),
                    ("Group", group_name),
                ],
                note="Check the packing list in the app.",
            ),
        )
