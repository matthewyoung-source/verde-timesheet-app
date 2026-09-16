"""Shared look for the Verde morning brief emails.

One file, no dependencies beyond the standard library, dropped identically
into the outbound portal, the timesheet app and the CRM so all three briefs
arrive looking like they came from the same company.

Email rules: tables and inline CSS only. No flexbox, no JavaScript, no SVG,
no web fonts that matter. Charts are coloured table cells, which render
everywhere including Outlook. Every builder returns (text, html) so the
plain-text twin stays readable and the Cowork brief job can still parse it.
"""

GREEN = "#0f2f24"
BRAND = "#1f7a5a"
BRAND_SOFT = "#e7f1ec"
AMBER = "#f2a93b"
AMBER_SOFT = "#fdf1dc"
AMBER_TEXT = "#8a5c07"
CREAM = "#f6f5f1"
INK = "#1b2420"
MUTED = "#5f6b66"
FAINT = "#8b9691"
RULE = "#dde3df"
RED = "#b4413c"
FONT = "'Plus Jakarta Sans','Segoe UI',Helvetica,Arial,sans-serif"

TONES = {"": (RULE, INK), "good": (BRAND, BRAND), "warn": (AMBER, AMBER_TEXT), "bad": (RED, RED)}


def esc(t):
    return (str(t if t is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def money(v):
    try:
        return "${:,.0f}".format(float(v))
    except (TypeError, ValueError):
        return ""


# ---------- blocks ----------

def section(title, hint=""):
    h = ('<div style="font-size:12.5px;color:%s;margin-top:3px;line-height:1.45">%s</div>'
         % (MUTED, esc(hint))) if hint else ""
    return ('<tr><td style="padding:26px 24px 10px">'
            '<div style="font-size:11px;letter-spacing:.09em;text-transform:uppercase;'
            'color:%s;font-weight:700">%s</div>%s</td></tr>' % (BRAND, esc(title), h))


def kpis(items):
    """items: list of (label, value, note, tone). Four per row reads best."""
    if not items:
        return ""
    cells = []
    for label, value, note, tone in items:
        bar, colour = TONES.get(tone, TONES[""])
        cells.append(
            '<td width="25%%" style="padding:0 6px;vertical-align:top">'
            '<table width="100%%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">'
            '<tr><td style="background:%s;border-top:3px solid %s;border-radius:0 0 10px 10px;'
            'padding:12px 12px 13px">'
            '<div style="font-size:11px;color:%s;text-transform:uppercase;letter-spacing:.05em;'
            'font-weight:700">%s</div>'
            '<div style="font-size:25px;color:%s;font-weight:800;line-height:1.15;margin-top:5px">%s</div>'
            '%s</td></tr></table></td>'
            % (CREAM, bar, MUTED, esc(label), colour, esc(value),
               ('<div style="font-size:11.5px;color:%s;margin-top:3px;line-height:1.35">%s</div>'
                % (MUTED, esc(note))) if note else ""))
    return ('<tr><td style="padding:2px 18px"><table width="100%" cellpadding="0" cellspacing="0" '
            'style="border-collapse:collapse"><tr>' + "".join(cells) + "</tr></table></td></tr>")


def columns(labels, series, height=104):
    """A column chart. series: list of (name, colour, [values]). One or two."""
    values = [v for _, _, vals in series for v in vals]
    top = max(values) if values and max(values) else 1
    cols = []
    for i, label in enumerate(labels):
        stack = []
        for _, colour, vals in series:
            v = vals[i] if i < len(vals) else 0
            h = max(2, int(round(height * (v / top)))) if v else 2
            stack.append('<td style="vertical-align:bottom;padding:0 1px">'
                         '<div style="height:%dpx;background:%s;border-radius:3px 3px 0 0;'
                         'font-size:0;line-height:0">&nbsp;</div></td>' % (h, colour if v else RULE))
        cols.append(
            '<td style="vertical-align:bottom;padding:0 2px">'
            '<table width="100%%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">'
            '<tr>%s</tr></table>'
            '<div style="font-size:9.5px;color:%s;text-align:center;padding-top:5px;'
            'white-space:nowrap">%s</div></td>' % ("".join(stack), FAINT, esc(label)))
    key = " &nbsp; ".join(
        '<span style="color:%s">&#9632;</span> <span style="color:%s">%s</span>'
        % (colour, MUTED, esc(name)) for name, colour, _ in series) if len(series) > 1 else ""
    legend = ('<div style="font-size:11.5px;padding:10px 0 0">%s</div>' % key) if key else ""
    return ('<tr><td style="padding:6px 24px 0">'
            '<table width="100%%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">'
            '<tr>%s</tr></table>%s</td></tr>' % ("".join(cols), legend))


def bars(items, suffix=""):
    """items: list of (label, display, fraction, note, tone); fraction is 0 to 1."""
    if not items:
        return ""
    out = ['<tr><td style="padding:4px 24px"><table width="100%" cellpadding="0" cellspacing="0" '
           'style="border-collapse:collapse">']
    for label, value, fraction, note, tone in items:
        colour = TONES.get(tone, TONES[""])[0]
        if tone == "":
            colour = BRAND
        try:
            pct = max(1, min(100, int(round(100 * float(fraction)))))
        except (TypeError, ValueError):
            pct = 1
        out.append(
            '<tr><td style="padding:9px 0;border-bottom:1px solid %s">'
            '<table width="100%%" cellpadding="0" cellspacing="0"><tr>'
            '<td style="font-size:13.5px;color:%s;font-weight:600">%s</td>'
            '<td align="right" style="font-size:13.5px;color:%s;font-weight:700;white-space:nowrap">%s%s</td>'
            '</tr></table>'
            '<table width="100%%" cellpadding="0" cellspacing="0" style="margin-top:6px">'
            '<tr><td width="%d%%" style="background:%s;height:7px;border-radius:4px;font-size:0;'
            'line-height:0">&nbsp;</td><td style="background:%s;height:7px;border-radius:4px;'
            'font-size:0;line-height:0">&nbsp;</td></tr></table>'
            '%s</td></tr>'
            % (RULE, INK, esc(label), TONES.get(tone, TONES[""])[1], esc(value), esc(suffix),
               pct, colour, CREAM,
               ('<div style="font-size:12px;color:%s;margin-top:5px;line-height:1.4">%s</div>'
                % (AMBER_TEXT if tone == "warn" else MUTED, esc(note))) if note else ""))
    out.append("</table></td></tr>")
    return "".join(out)


def rows(items):
    """items: list of (left, right, note, tone) for list-style sections."""
    if not items:
        return ""
    out = ['<tr><td style="padding:2px 24px"><table width="100%" cellpadding="0" cellspacing="0" '
           'style="border-collapse:collapse">']
    for left, right, note, tone in items:
        bar, colour = TONES.get(tone, TONES[""])
        out.append(
            '<tr><td style="border-left:3px solid %s;padding:9px 0 9px 12px;border-bottom:1px solid %s">'
            '<div style="font-size:13.5px;color:%s;font-weight:600;line-height:1.35">%s</div>%s</td>'
            '<td align="right" style="padding:9px 0 9px 10px;border-bottom:1px solid %s;'
            'white-space:nowrap;vertical-align:top;font-size:13px;color:%s;font-weight:700">%s</td></tr>'
            % (bar, RULE, INK, esc(left),
               ('<div style="font-size:12.5px;color:%s;margin-top:3px;line-height:1.45">%s</div>'
                % (MUTED, esc(note))) if note else "",
               RULE, colour, esc(right)))
    out.append("</table></td></tr>")
    return "".join(out)


def card(title, subtitle, quoted="", sent=""):
    """A reply worth acting on: who, what they wrote, what we sent them."""
    blocks = ""
    if quoted:
        blocks += ('<div style="background:%s;border-radius:8px;padding:11px 13px;margin-top:9px;'
                   'font-size:13px;color:%s;line-height:1.5">%s</div>' % (BRAND_SOFT, INK, esc(quoted)))
    if sent:
        blocks += ('<div style="font-size:12px;color:%s;margin-top:8px;line-height:1.5">'
                   '<b style="color:%s">We sent:</b> %s</div>' % (MUTED, MUTED, esc(sent)))
    return ('<tr><td style="padding:8px 24px 0">'
            '<table width="100%%" cellpadding="0" cellspacing="0" style="border-collapse:collapse">'
            '<tr><td style="border:1px solid %s;border-left:3px solid %s;border-radius:10px;padding:13px 15px">'
            '<div style="font-size:14.5px;color:%s;font-weight:700">%s</div>'
            '<div style="font-size:12.5px;color:%s;margin-top:2px">%s</div>%s'
            '</td></tr></table></td></tr>'
            % (RULE, AMBER, INK, esc(title), MUTED, esc(subtitle), blocks))


def pills(items):
    """items: list of (text, tone)."""
    if not items:
        return ""
    chips = []
    for text, tone in items:
        bg, fg = {"good": (BRAND_SOFT, BRAND), "warn": (AMBER_SOFT, AMBER_TEXT),
                  "bad": ("#fbe9e8", RED)}.get(tone, (CREAM, MUTED))
        chips.append('<span style="display:inline-block;background:%s;color:%s;font-size:12px;'
                     'font-weight:600;padding:5px 10px;border-radius:20px;margin:0 6px 6px 0">%s</span>'
                     % (bg, fg, esc(text)))
    return '<tr><td style="padding:4px 24px 0">%s</td></tr>' % "".join(chips)


def empty(text):
    return ('<tr><td style="padding:2px 24px"><div style="font-size:13.5px;color:%s">%s</div>'
            "</td></tr>" % (MUTED, esc(text)))


def page(title, date_label, headline, blocks, footer=""):
    head = ('<tr><td style="background:%s;padding:21px 24px">'
            '<table width="100%%" cellpadding="0" cellspacing="0"><tr>'
            '<td><div style="font-size:16.5px;color:#ffffff;font-weight:800;letter-spacing:-.2px">'
            'Verde Solutions</div>'
            '<div style="font-size:12.5px;color:#9fc4b4;margin-top:3px">%s</div></td>'
            '<td align="right" style="vertical-align:top">'
            '<div style="font-size:12.5px;color:#9fc4b4">%s</div></td>'
            "</tr></table></td></tr>" % (GREEN, esc(title), esc(date_label)))
    lead = ('<tr><td style="padding:19px 24px 0">'
            '<div style="font-size:15.5px;color:%s;font-weight:700;line-height:1.45">%s</div>'
            "</td></tr>" % (INK, esc(headline))) if headline else ""
    foot = ('<tr><td style="padding:26px 24px 24px">'
            '<div style="border-top:1px solid %s;padding-top:14px;font-size:11.5px;color:%s;'
            'line-height:1.6">%s</div></td></tr>' % (RULE, MUTED, esc(footer))) if footer else ""
    return ('<!doctype html><html><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>%s</title></head>"
            '<body style="margin:0;padding:0;background:%s">'
            '<table width="100%%" cellpadding="0" cellspacing="0" style="background:%s;padding:22px 10px">'
            '<tr><td align="center">'
            '<table width="640" cellpadding="0" cellspacing="0" style="max-width:640px;width:100%%;'
            'background:#ffffff;border:1px solid %s;border-radius:14px;overflow:hidden;font-family:%s">'
            "%s%s%s%s</table></td></tr></table></body></html>"
            % (esc(title), CREAM, CREAM, RULE, FONT, head, lead, "".join(blocks), foot))
