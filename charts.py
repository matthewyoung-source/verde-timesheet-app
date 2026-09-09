"""Tiny server-rendered SVG helpers shared by the templates (same marks as
the Verde Outreach portal: thin rings, rounded ends, palette from CSS vars)."""
from markupsafe import Markup


def ring_svg(value, maximum, label, color_var="--brand", size=72, track_var="--ring-track", text_color="var(--text)", track_color=None):
    """Progress ring: value out of maximum with a short label in the middle."""
    r = 26
    circ = 2 * 3.14159 * r
    pct = 0 if not maximum else max(0.0, min(1.0, float(value) / float(maximum)))
    track = track_color or f"var({track_var})"
    return Markup(f'''<svg class="ring" viewBox="0 0 64 64" width="{size}" height="{size}" role="img" aria-label="{label}">
  <circle cx="32" cy="32" r="{r}" fill="none" stroke="{track}" stroke-width="7"/>
  <circle cx="32" cy="32" r="{r}" fill="none" stroke="var({color_var})" stroke-width="7" stroke-linecap="round"
    stroke-dasharray="{circ * pct:.1f} {circ:.1f}" transform="rotate(-90 32 32)"/>
  <text x="32" y="37" font-size="14" font-weight="700" text-anchor="middle" fill="{text_color}">{label}</text>
</svg>''')
