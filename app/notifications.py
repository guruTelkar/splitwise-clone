"""Email and SMS notification service.

Uses Resend for email (free: 100/day, 3000/mo).
Uses Twilio for SMS (free trial: $15 credit).
Falls back to console logging if no API keys are configured.
"""

import logging

import httpx

from .config import settings

logger = logging.getLogger(__name__)


def send_email(to: str, subject: str, html_body: str) -> bool:
    """Send an email via Resend. Returns True on success."""
    api_key = settings.resend_api_key
    if not api_key:
        logger.info(f"[EMAIL DISABLED] To: {to} | Subject: {subject}")
        print(f"[EMAIL] To: {to} | Subject: {subject} | Body: {html_body}")
        return False
    try:
        resp = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": settings.email_from,
                "to": [to],
                "subject": subject,
                "html": html_body,
            },
            timeout=15,
        )
        if resp.status_code < 300:
            logger.info(f"Email sent to {to}: {resp.json()}")
            return True
        else:
            logger.error(f"Resend error {resp.status_code}: {resp.text}")
            return False
    except Exception as e:
        logger.error(f"Email send failed: {e}")
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


def notify_expense_added(group_name: str, expense_desc: str, members: list[dict]) -> None:
    """Notify group members about a new expense (email + in-app)."""
    for m in members:
        send_email(
            m["email"],
            f"New expense in {group_name}",
            f"<p><strong>{expense_desc}</strong> was added to <em>{group_name}</em>.</p>",
        )


def notify_group_created(group_name: str, members: list[dict], creator_name: str) -> None:
    """Notify all members about group creation."""
    for m in members:
        send_email(
            m["email"],
            f"You've been added to {group_name}",
            f"<p><strong>{creator_name}</strong> created the group <em>{group_name}</em> and added you.</p>",
        )


def notify_friend_added(friend_email: str, added_by_name: str) -> None:
    """Notify a user they've been added as a friend."""
    send_email(
        friend_email,
        f"{added_by_name} added you as a friend",
        f"<p><strong>{added_by_name}</strong> added you as a friend on Splitwise Clone.</p>",
    )


def notify_payment_recorded(from_name: str, to_name: str, amount: str, from_email: str, to_email: str) -> None:
    """Notify both parties about a settlement."""
    send_email(
        from_email,
        f"Payment recorded to {to_name}",
        f"<p>You recorded a payment of <strong>{amount}</strong> to {to_name}.</p>",
    )
    send_email(
        to_email,
        f"Payment received from {from_name}",
        f"<p>{from_name} recorded a payment of <strong>{amount}</strong> to you.</p>",
    )


def notify_expense_updated(group_name: str, expense_desc: str, members: list[dict]) -> None:
    for m in members:
        send_email(m["email"], f"Expense updated in {group_name}", f"<p><strong>{expense_desc}</strong> was updated in <em>{group_name}</em>.</p>")


def notify_expense_deleted(group_name: str, expense_desc: str, members: list[dict]) -> None:
    for m in members:
        send_email(m["email"], f"Expense removed from {group_name}", f"<p><strong>{expense_desc}</strong> was deleted from <em>{group_name}</em>.</p>")


def notify_comment_added(expense_desc: str, commenter_name: str, members: list[dict]) -> None:
    for m in members:
        send_email(m["email"], f"New comment on {expense_desc}", f"<p><strong>{commenter_name}</strong> commented on <em>{expense_desc}</em>.</p>")


def notify_member_joined(group_name: str, joiner_name: str, members: list[dict]) -> None:
    for m in members:
        send_email(m["email"], f"New member in {group_name}", f"<p><strong>{joiner_name}</strong> joined <em>{group_name}</em>.</p>")


def notify_member_added(group_name: str, added_user_name: str, members: list[dict]) -> None:
    for m in members:
        send_email(m["email"], f"You were added to {group_name}", f"<p>You were added to <em>{group_name}</em> by {added_user_name}.</p>")


def notify_packing_item_added(group_name: str, item_name: str, members: list[dict]) -> None:
    for m in members:
        send_email(m["email"], f"Packing item added in {group_name}", f"<p><strong>{item_name}</strong> was added to the packing list for <em>{group_name}</em>.</p>")


def notify_packing_item_toggled(group_name: str, item_name: str, is_checked: bool, toggled_by_name: str, members: list[dict]) -> None:
    status = "completed" if is_checked else "unchecked"
    for m in members:
        send_email(m["email"], f"Packing item {status} in {group_name}", f"<p><strong>{toggled_by_name}</strong> marked <strong>{item_name}</strong> as {status} in <em>{group_name}</em>.</p>")
