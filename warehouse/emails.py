"""HTML email layout shared by alert and account emails.

Email clients ignore most CSS, so everything is inline styles on tables. Colours match the app:
stone background, clay accent, red only for the low-battery state.
"""
from html import escape

INK = "#1C1917"
MUTED = "#57534E"
LINE = "#E7E5E4"
GROUND = "#F5F5F4"
CLAY = "#B45309"
RED = "#B91C1C"
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def button(label, url):
    return (
        f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:24px 0 8px">'
        f'<tr><td style="background:{CLAY};border-radius:6px">'
        f'<a href="{escape(url, quote=True)}" style="display:inline-block;padding:12px 22px;color:#FFFFFF;'
        f'font:600 15px {FONT};text-decoration:none">{escape(label)}</a></td></tr></table>'
    )


def battery_meter(percent, threshold):
    pct = max(0, min(100, int(percent)))
    colour = RED if pct < threshold else "#15803D"
    return (
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="margin:20px 0 6px;border:1px solid {LINE};border-radius:8px">'
        f'<tr><td style="padding:16px 18px">'
        f'<div style="font:500 12px {FONT};color:{MUTED};text-transform:uppercase;letter-spacing:.06em">Battery</div>'
        f'<div style="font:700 36px {FONT};color:{colour};margin:4px 0 10px">{pct}%</div>'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        f'style="background:{LINE};border-radius:4px;height:10px"><tr>'
        f'<td width="{max(pct, 1)}%" style="background:{colour};border-radius:4px;height:10px;font-size:0;line-height:0">&nbsp;</td>'
        f'<td style="font-size:0;line-height:0">&nbsp;</td></tr></table>'
        f'<div style="font:400 12px {FONT};color:{MUTED};margin-top:8px">Alert threshold {threshold}%</div>'
        f'</td></tr></table>'
    )


def layout(*, preheader, eyebrow, title, body_html, footer, tag=None):
    tag_html = (
        f'<span style="display:inline-block;margin-left:8px;padding:2px 8px;border-radius:4px;background:#FEF3C7;'
        f'color:#92400E;font:600 11px {FONT};vertical-align:middle">{escape(tag)}</span>'
        if tag else ""
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><title>{escape(title)}</title></head>
<body style="margin:0;padding:0;background:{GROUND}">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{GROUND}">
<tr><td align="center" style="padding:32px 16px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px">
<tr><td style="padding:0 4px 16px;font:600 16px {FONT};color:{INK}">
<span style="display:inline-block;width:24px;height:24px;border-radius:6px;background:{CLAY};vertical-align:middle;margin-right:8px"></span>
<span style="vertical-align:middle">Warehouse Console</span></td></tr>
<tr><td style="background:#FFFFFF;border:1px solid {LINE};border-top:4px solid {CLAY};border-radius:10px;padding:28px 28px 24px">
<div style="font:600 12px {FONT};color:{CLAY};text-transform:uppercase;letter-spacing:.08em">{escape(eyebrow)}{tag_html}</div>
<h1 style="margin:8px 0 0;font:700 22px/1.3 {FONT};color:{INK}">{escape(title)}</h1>
<div style="font:400 15px/1.6 {FONT};color:{INK}">{body_html}</div>
</td></tr>
<tr><td style="padding:16px 4px 0;font:400 12px/1.5 {FONT};color:{MUTED}">{footer}</td></tr>
</table></td></tr></table></body></html>"""


def p(text, muted=False):
    return f'<p style="margin:14px 0 0;color:{MUTED if muted else INK}">{text}</p>'


def low_battery_email(identifier, battery, threshold, test=False):
    ident = escape(identifier)
    body = (
        p(f"Shuttle <strong>{ident}</strong> is below the {threshold}% threshold. Send it to its dock to charge "
          "before the next load.")
        + battery_meter(battery, threshold)
        + p("You get one alert each time the battery drops below the threshold. Charging back above it re-arms the alert.", muted=True)
    )
    footer = (
        "This was a test alert sent on request from the Shuttle page." if test
        else "Sent automatically by Warehouse Console because this address receives low-battery alerts."
    )
    return layout(
        preheader=f"Shuttle {identifier} battery is at {battery}%, below {threshold}%.",
        eyebrow="Shuttle alert",
        title=f"Low battery on {identifier}",
        body_html=body,
        footer=footer,
        tag="Test" if test else None,
    )


def password_reset_email(username, link, minutes=60):
    body = (
        p(f"Hi {escape(username)}, someone asked to reset the password for your Warehouse Console account.")
        + button("Choose a new password", link)
        + p(f"The link works once and expires in {minutes} minutes. Resetting signs you out on every device.", muted=True)
        + p(f'Button not working? Paste this into your browser:<br><a href="{escape(link, quote=True)}" '
            f'style="color:{CLAY};word-break:break-all">{escape(link)}</a>', muted=True)
    )
    return layout(
        preheader="Use this link within an hour to choose a new password.",
        eyebrow="Account",
        title="Reset your password",
        body_html=body,
        footer="Didn't ask for this? Ignore this email. Your password stays the same.",
    )
