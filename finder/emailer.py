"""Digest and alert emails, sent from Gmail over SMTP with an app password.

If credentials are missing the email is written to data/last_email.html instead,
so the content can be previewed in a dry run.
"""
import html
import os
import smtplib
from collections import defaultdict
from email.message import EmailMessage
from email.utils import formataddr

from . import config, store


def _row(l):
    flags = l.get("flags") or []
    bits = []
    if l.get("no_fee"):
        bits.append("No fee")
    if "alcove-or-junior" in flags:
        bits.append("Alcove / Jr 1BR")
    if "sublet" in flags:
        bits.append("Sublet")
    if "room-or-share" in flags:
        bits.append("Room or share?")
    if "area-inferred" in flags:
        bits.append("area inferred")
    if l.get("price_history"):
        last = l["price_history"][-1]
        if last["to"] < last["from"]:
            bits.append(f"was ${last['from']:,}")
    extra = ("<span style='color:#6b6b73;font-size:12px'> · " + html.escape(", ".join(bits)) + "</span>") if bits else ""
    addr = f"<div style='color:#6b6b73;font-size:12px'>{html.escape(l['address'])}</div>" if l.get("address") else ""
    return (
        "<tr><td style='padding:10px 0;border-bottom:1px solid #e5e1d8;vertical-align:top;width:84px'>"
        f"<b style='font-size:17px'>${l['price']:,}</b></td>"
        "<td style='padding:10px 0 10px 10px;border-bottom:1px solid #e5e1d8;vertical-align:top'>"
        f"<a href='{html.escape(l['url'])}' style='color:#0f5f5c;font-weight:600;text-decoration:none'>{html.escape(l['title'])}</a>{extra}"
        f"{addr}<div style='color:#6b6b73;font-size:12px'>{html.escape(l.get('source_label') or l['source'])}"
        + (" · also on " + ", ".join(f"<a href='{html.escape(a['url'])}' style='color:#0f5f5c'>{html.escape(a['source_label'])}</a>" for a in l["also"]) if l.get("also") else "")
        + "</div></td></tr>"
    )


def _wrap(title, intro, body_html, footer_note=""):
    site = config.SITE_URL
    link = f"<p style='margin:18px 0'><a href='{html.escape(site)}' style='background:#0f5f5c;color:#fff;padding:10px 16px;border-radius:8px;text-decoration:none;font-weight:600'>Open the live board</a></p>" if site else ""
    return f"""<!doctype html><html><body style="margin:0;background:#f6f4ef;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#1b1b1f">
<div style="max-width:640px;margin:0 auto;padding:24px 16px">
<h1 style="font-size:22px;margin:0 0 6px">{html.escape(title)}</h1>
<p style="color:#6b6b73;margin:0 0 16px">{intro}</p>
<div style="background:#fff;border:1px solid #e5e1d8;border-radius:12px;padding:6px 16px">{body_html}</div>
{link}
<p style="color:#6b6b73;font-size:12px">{footer_note} Prices and availability come from each platform and can change quickly. Always check the listing itself before getting in touch.</p>
</div></body></html>"""


def _grouped(listings):
    groups = defaultdict(list)
    for l in listings:
        groups[l.get("neighborhood") or "Unknown"].append(l)
    out = []
    for n in sorted(groups):
        rows = sorted(groups[n], key=lambda l: l["price"])
        out.append(f"<h3 style='font-size:15px;margin:16px 0 4px'>{html.escape(n)} <span style='color:#6b6b73;font-weight:400'>({len(rows)})</span></h3>")
        out.append("<table style='width:100%;border-collapse:collapse'>" + "".join(_row(l) for l in rows) + "</table>")
    return "".join(out)


def build_digest(new_listings, drops, total_active, source_status, since_label):
    parts = []
    if new_listings:
        parts.append(_grouped(new_listings))
    else:
        parts.append("<p style='padding:14px 0'>No new studios appeared in that window. The board still has everything that is live.</p>")
    if drops:
        parts.append("<h2 style='font-size:17px;margin:22px 0 4px'>Price drops</h2>")
        parts.append("<table style='width:100%;border-collapse:collapse'>" + "".join(_row(l) for l in sorted(drops, key=lambda l: l["price"])) + "</table>")
    blocked = [s["label"] for s in source_status if s["status"] == "blocked"]
    note = f"Sources blocked this run: {html.escape(', '.join(blocked))}." if blocked else ""
    intro = (f"{len(new_listings)} new listing{'s' if len(new_listings) != 1 else ''} since {since_label}. "
             f"{total_active} studios live at or under ${config.MAX_PRICE:,} in total.")
    return _wrap("NYC studio digest", intro, "".join(parts), note)


def build_alert(listings):
    intro = (f"{len(listings)} new studio{'s' if len(listings) != 1 else ''} at or under ${config.ALERT_PRICE:,} just appeared. "
             "These go fast, so worth a look now.")
    return _wrap("Under-budget studio alert", intro, _grouped(listings))


def send(subject, html_body, text_fallback=None):
    """Send via Gmail SMTP. Returns True if sent, False if dry-run."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["To"] = config.RECIPIENT
    if config.CC:
        msg["Cc"] = ", ".join(config.CC)
    msg.set_content(text_fallback or "This email is best viewed in HTML.")
    msg.add_alternative(html_body, subtype="html")

    if not (config.GMAIL_ADDRESS and config.GMAIL_APP_PASSWORD):
        path = os.path.join(config.DATA_DIR, "last_email.html")
        os.makedirs(config.DATA_DIR, exist_ok=True)
        with open(path, "w") as f:
            f.write(f"<!-- Subject: {subject} -->\n" + html_body)
        print(f"[email] dry run, no Gmail credentials. Wrote {path}")
        return False

    msg["From"] = formataddr(("NYC Studio Finder", config.GMAIL_ADDRESS))
    recipients = [config.RECIPIENT] + config.CC
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
        s.send_message(msg, to_addrs=recipients)
    print(f"[email] sent '{subject}' to {recipients}")
    return True
