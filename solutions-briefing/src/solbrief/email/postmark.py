"""Minimal Postmark client. M1b uses it only for admin alerts (transactional stream).

Briefings (M5) go out on the Broadcast stream, which adds RFC 8058 one-click List-Unsubscribe.
"""

from __future__ import annotations

import logging

import httpx

from solbrief.config import get_settings

log = logging.getLogger(__name__)
API = "https://api.postmarkapp.com/email"


def send_email(
    to: str, subject: str, text_body: str, *, stream: str | None = None, html_body: str | None = None
) -> str | None:
    """Send one email. Returns the Postmark MessageID, or None if Postmark isn't configured."""
    s = get_settings()
    if not s.postmark_server_token:
        log.warning("POSTMARK_SERVER_TOKEN not set; not sending %r to %s", subject, to)
        return None
    payload = {
        "From": s.postmark_from,
        "To": to,
        "Subject": subject,
        "TextBody": text_body,
        "MessageStream": stream or s.postmark_transactional_stream,
    }
    if html_body:
        payload["HtmlBody"] = html_body
    resp = httpx.post(
        API,
        json=payload,
        headers={"X-Postmark-Server-Token": s.postmark_server_token, "Accept": "application/json"},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json().get("MessageID")
