"""Alert email content. Plain text + minimal inline-styled HTML for email clients."""

from dataclasses import dataclass
from html import escape

from drysg.core.models import AlertMessage, Band


@dataclass(frozen=True)
class EmailContent:
    subject: str
    text: str
    html: str


def stop_url(base_url: str, session_id: str) -> str:
    return f"{base_url.rstrip('/')}/stop?session={session_id}"


def render_alert(msg: AlertMessage, base_url: str) -> EmailContent:
    if msg.new_band is Band.DO_NOT_HANG:
        subject = f"DrySG: bring your laundry in ({msg.area})"
        action = "Bring your laundry in now."
    else:
        subject = f"DrySG: drying conditions worsened ({msg.area})"
        action = "Drying will be slower than expected. Consider bringing your laundry in."

    stop = stop_url(base_url, msg.session_id)
    status = f"{msg.new_band} (score {msg.new_score}/100, was {msg.prev_score} {msg.prev_band})"

    text = (
        f"{action}\n\n"
        f"Area: {msg.area}\n"
        f"Reason: {msg.reason}\n"
        f"Now: {status}\n\n"
        f"Brought your laundry in? Stop monitoring: {stop}\n"
    )
    html = (
        '<div style="font-family:Arial,sans-serif;max-width:520px">'
        f"<h2 style=\"margin:0 0 12px\">{escape(action)}</h2>"
        f"<p><b>Area:</b> {escape(msg.area)}<br>"
        f"<b>Reason:</b> {escape(msg.reason)}<br>"
        f"<b>Now:</b> {escape(status)}</p>"
        f'<p><a href="{escape(stop)}">I\'ve brought my laundry in - stop monitoring</a></p>'
        "</div>"
    )
    return EmailContent(subject=subject, text=text, html=html)
