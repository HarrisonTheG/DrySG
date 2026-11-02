"""Alert and verification email content: plain text plus an HTML version.

The HTML follows email-client rules rather than web rules: table layout, inline styles
only (Gmail and Outlook strip <style> blocks and most modern CSS), no images (they are
blocked by default), and a table-based button so it renders in Outlook. Colours match
the website (slate text, blue brand, emerald/amber/red bands).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
from urllib.parse import urlencode

from drysg.core.models import AlertMessage, Band
from drysg.sessions import SESSION_LIFETIME

SGT = timezone(timedelta(hours=8))
_LIFETIME_HOURS = int(SESSION_LIFETIME.total_seconds() // 3600)
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"

# Website palette (Tailwind slate/blue/emerald/amber/red).
INK = "#0f172a"
MUTED = "#475569"
FAINT = "#64748b"
LINE = "#e2e8f0"
PAGE = "#f1f5f9"
BRAND = "#2563eb"

# (text, background, border) per band, same tones as the site's cards.
BAND_TONES = {
    Band.HANG_NOW: ("#047857", "#d1fae5", "#a7f3d0"),
    Band.WAIT: ("#b45309", "#fef3c7", "#fde68a"),
    Band.DO_NOT_HANG: ("#b91c1c", "#fee2e2", "#fecaca"),
}


@dataclass(frozen=True)
class EmailContent:
    subject: str
    text: str
    html: str


def stop_url(base_url: str, session_id: str) -> str:
    return f"{base_url.rstrip('/')}/stop?session={session_id}"


def verify_url(base_url: str, email: str, token: str) -> str:
    return f"{base_url.rstrip('/')}/verify?{urlencode({'email': email, 'token': token})}"


def _sgt_time(ts: datetime) -> str:
    local = ts.astimezone(SGT)
    hour = local.hour % 12 or 12
    return f"{hour}:{local.minute:02d} {'am' if local.hour < 12 else 'pm'} SGT"


def _band_chip(band: Band, score: int | None = None) -> str:
    fg, bg, border = BAND_TONES[band]
    label = escape(str(band)) if score is None else f"{escape(str(band))} &middot; {score}"
    return (
        f'<span style="display:inline-block;padding:4px 12px;border-radius:999px;'
        f"background:{bg};border:1px solid {border};color:{fg};font-size:13px;"
        f'font-weight:700;white-space:nowrap">{label}</span>'
    )


def _button(href: str, label: str) -> str:
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0">'
        f'<tr><td bgcolor="{INK}" style="border-radius:14px">'
        f'<a href="{escape(href)}" target="_blank" style="display:inline-block;'
        f"padding:14px 22px;font-family:{FONT};font-size:15px;font-weight:700;"
        f'color:#ffffff;text-decoration:none;border-radius:14px">{escape(label)}</a>'
        "</td></tr></table>"
    )


def _layout(*, preheader: str, accent: str, icon: str, body: str, footer: str) -> str:
    """Wrap `body` in the shared DrySG frame: brand header, card with an accent stripe."""
    return (
        "<!doctype html><html><head>"
        '<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="light">'
        '<meta name="supported-color-schemes" content="light">'
        "<title>DrySG</title></head>"
        f'<body style="margin:0;padding:0;background:{PAGE}">'
        # Preview line shown next to the subject in the inbox list, hidden in the body.
        f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:{PAGE}">'
        f"{escape(preheader)}&#8199;&#65279;&#847;&#8199;&#65279;&#847;</div>"
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'bgcolor="{PAGE}" style="background:{PAGE}">'
        '<tr><td align="center" style="padding:28px 16px">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="max-width:560px">'
        # Brand row
        '<tr><td style="padding:0 4px 16px">'
        '<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
        f'<td width="36" height="36" align="center" bgcolor="{BRAND}" '
        'style="width:36px;height:36px;border-radius:10px;font-size:18px;line-height:36px">'
        "&#9925;</td>"
        f'<td style="padding-left:10px;font-family:{FONT}">'
        f'<div style="font-size:17px;font-weight:800;color:{INK};line-height:1.2">DrySG</div>'
        f'<div style="font-size:12px;color:{FAINT};line-height:1.3">'
        "Singapore laundry weather assistant</div></td>"
        "</tr></table></td></tr>"
        # Card
        f'<tr><td bgcolor="#ffffff" style="background:#ffffff;border:1px solid {LINE};'
        'border-radius:20px;overflow:hidden">'
        f'<div style="height:6px;line-height:6px;font-size:0;background:{accent}">&nbsp;</div>'
        f'<div style="padding:28px 28px 30px;font-family:{FONT};color:{INK}">'
        f'<div style="font-size:30px;line-height:1">{icon}</div>'
        f"{body}</div></td></tr>"
        # Footer
        f'<tr><td style="padding:18px 8px 0;font-family:{FONT};font-size:12px;'
        f'line-height:1.6;color:{FAINT};text-align:center">{footer}</td></tr>'
        "</table></td></tr></table></body></html>"
    )


def _heading(text: str) -> str:
    return (
        f'<h1 style="margin:14px 0 8px;font-size:22px;line-height:1.3;font-weight:800;'
        f'color:{INK}">{escape(text)}</h1>'
    )


def _para(html_text: str, *, size: int = 15, color: str = MUTED, margin: str = "0 0 20px") -> str:
    return (
        f'<p style="margin:{margin};font-size:{size}px;line-height:1.6;color:{color}">'
        f"{html_text}</p>"
    )


def _fallback_link(href: str) -> str:
    return _para(
        "Button not working? Paste this link into your browser:<br>"
        f'<a href="{escape(href)}" style="color:{BRAND};word-break:break-all">{escape(href)}</a>',
        size=12,
        color=FAINT,
        margin="22px 0 0",
    )


def render_verification(email: str, token: str, base_url: str, hours_valid: int) -> EmailContent:
    link = verify_url(base_url, email, token)
    subject = "DrySG: confirm your email"
    intro = "Confirm your email so DrySG can alert you when drying conditions get worse."
    text = (
        f"{intro}\n\n"
        f"Confirm: {link}\n\n"
        f"This link expires in {hours_valid} hours. "
        "If you didn't ask for this, you can ignore this email.\n"
    )
    body = (
        _heading("Confirm your email")
        + _para(
            "Confirm your email so DrySG can alert you when drying conditions get worse "
            f"for <b style=\"color:{INK}\">{escape(email)}</b>."
        )
        + _button(link, "Confirm my email")
        + _para(
            f"This link expires in {hours_valid} hours.",
            size=13,
            color=FAINT,
            margin="18px 0 0",
        )
        + _fallback_link(link)
    )
    html = _layout(
        preheader="One click and DrySG can start watching the weather for your laundry.",
        accent=BRAND,
        icon="&#9993;&#65039;",
        body=body,
        footer="If you didn't ask for this, you can ignore this email.",
    )
    return EmailContent(subject=subject, text=text, html=html)


def render_alert(msg: AlertMessage, base_url: str) -> EmailContent:
    if msg.new_veto:
        subject = f"DrySG: rain nearby, bring your laundry in ({msg.area})"
        action = "Rain is detected or forecast near you. Bring your laundry in now."
        headline = "Rain nearby: bring your laundry in"
        lead = action
        accent, icon = BAND_TONES[Band.DO_NOT_HANG][0], "&#9748;"
    elif msg.new_band is Band.DO_NOT_HANG:
        subject = f"DrySG: bring your laundry in ({msg.area})"
        action = "Bring your laundry in now."
        headline = "Bring your laundry in"
        lead = "Conditions are now too poor for laundry to dry well."  # not repeating headline
        accent, icon = BAND_TONES[Band.DO_NOT_HANG][0], "&#129530;"
    else:
        subject = f"DrySG: drying conditions worsened ({msg.area})"
        action = "Drying will be slower than expected. Consider bringing your laundry in."
        headline = "Drying conditions have worsened"
        lead = action
        accent, icon = BAND_TONES[Band.WAIT][0], "&#9729;&#65039;"

    stop = stop_url(base_url, msg.session_id)
    when = _sgt_time(msg.snapshot_ts)
    # The drying score ignores rain (rain is a separate veto), so it can still be high
    # while it rains. "Do Not Hang (score 82/100)" would look contradictory, so rain
    # alerts leave the score out and lead with the reason.
    status = (
        None
        if msg.new_veto
        else f"{msg.new_band} (score {msg.new_score}/100, was {msg.prev_score} {msg.prev_band})"
    )
    status_text = "" if status is None else f"Now: {status}\n"

    text = (
        f"{action}\n\n"
        f"Area: {msg.area}\n"
        f"Reason: {msg.reason}\n"
        f"{status_text}"
        f"Checked: {when}\n\n"
        f"Brought your laundry in? Stop monitoring: {stop}\n"
    )

    row = (
        '<tr><td style="padding:10px 0;border-top:1px solid {line};font-size:13px;'
        'color:{faint};width:96px;vertical-align:top">{label}</td>'
        '<td style="padding:10px 0;border-top:1px solid {line};font-size:14px;'
        'color:{ink};vertical-align:top">{value}</td></tr>'
    )
    rows = [("Area", f"<b>{escape(msg.area)}</b>"), ("Why", escape(msg.reason))]
    if status is not None:
        change = (
            f"{_band_chip(msg.prev_band, msg.prev_score)}"
            f'<span style="color:{FAINT};padding:0 6px">&rarr;</span>'
            f"{_band_chip(msg.new_band, msg.new_score)}"
            f'<div style="padding-top:6px;font-size:12px;color:{FAINT}">'
            "Drying score out of 100</div>"
        )
        rows.append(("Change", change))
    else:
        rows.append(("Now", _band_chip(Band.DO_NOT_HANG)))
    rows.append(("Checked", escape(when)))
    details = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="margin:0 0 24px;font-family:{FONT}">'
        + "".join(
            row.format(line=LINE, faint=FAINT, ink=INK, label=label, value=value)
            for label, value in rows
        )
        + "</table>"
    )

    body = (
        _heading(headline)
        + _para(escape(lead))
        + details
        + _button(stop, "I've brought my laundry in")
        + _para(
            "This stops monitoring, so you won't get more alerts for this load.",
            size=13,
            color=FAINT,
            margin="12px 0 0",
        )
        + _fallback_link(stop)
    )
    html = _layout(
        preheader=f"{msg.area}: {msg.reason}",
        accent=accent,
        icon=icon,
        body=body,
        footer=(
            f"You're getting this because you started monitoring {escape(msg.area)} on DrySG. "
            f"Monitoring ends on its own after {_LIFETIME_HOURS} hours."
        ),
    )
    return EmailContent(subject=subject, text=text, html=html)
