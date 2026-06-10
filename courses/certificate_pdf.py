"""
Certificate PDF generator.

Produces a two-column landscape A4 PDF matching the layout in
demo_certificate_template.html:

  Left  (67%) — off-white + diagonal texture, logo, body content, SVG signature
  Right (33%) — grey panel, sunburst watermark, "COURSE CERTIFICATE" heading,
                circular seal with arc text, verify block
"""

import io
import math

from django.conf import settings
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas

# ── Palette (matching the HTML template exactly) ──────────────────────────────
_PAPER      = colors.HexColor('#F9F9F7')
_RIGHT_GREY = colors.HexColor('#C6C9CE')
_NEAR_BLACK = colors.HexColor('#0D0D0D')
_NAVY       = colors.HexColor('#1A2B4A')
_GOLD       = colors.HexColor('#C9A84C')
_GREY_22    = colors.HexColor('#222222')
_GREY_3A    = colors.HexColor('#3a3a3a')
_GREY_44    = colors.HexColor('#444444')
_GREY_55    = colors.HexColor('#555555')
_GREY_66    = colors.HexColor('#666666')
_GREY_6A    = colors.HexColor('#6a6a6a')
_GREY_77    = colors.HexColor('#777777')
_GREY_88    = colors.HexColor('#888888')
_GREY_99    = colors.HexColor('#999999')
_GREY_BB    = colors.HexColor('#bbbbbb')
_WHITE      = colors.HexColor('#FFFFFF')
_CORNER_MK  = colors.HexColor('#b2b2b2')


# ── Drawing helpers ───────────────────────────────────────────────────────────

def _arc_text(c, text, cx, cy, radius, center_angle_deg,
              font, size, fill_color, letter_spacing=2.0, top_arc=True):
    """
    Render text centred at `center_angle_deg` along a circular arc.

    top_arc=True  → character tops face away from centre (top/side of circle).
    top_arc=False → character tops face toward centre (bottom of circle, readable from outside).
    """
    c.setFont(font, size)
    chars = list(text)
    widths = [c.stringWidth(ch, font, size) for ch in chars]
    total_w = sum(widths) + letter_spacing * max(0, len(chars) - 1)
    total_arc = total_w / radius

    center_rad = math.radians(center_angle_deg)
    if top_arc:
        # Traverse right-to-left (decreasing angle) so text reads L→R on top.
        cur = center_rad + total_arc / 2
        step = -1
    else:
        # Traverse left-to-right (increasing angle) so text reads L→R on bottom.
        cur = center_rad - total_arc / 2
        step = +1

    for i, ch in enumerate(chars):
        half_arc = (widths[i] / radius) / 2
        angle = cur + step * half_arc

        x = cx + radius * math.cos(angle)
        y = cy + radius * math.sin(angle)

        c.saveState()
        c.translate(x, y)
        rot = math.degrees(angle) - 90 if top_arc else math.degrees(angle) + 90
        c.rotate(rot)
        c.setFont(font, size)
        c.setFillColor(fill_color)
        c.drawCentredString(0, 0, ch)
        c.restoreState()

        cur += step * (widths[i] / radius + letter_spacing / radius)


def _draw_seal(c, cx, cy):
    """
    Circular institutional seal that mirrors the SVG in demo_certificate_template.html.

    SVG viewBox 200×200, paths at r=74 → scaled so outer edge ≈ 85pt.
    """
    scale    = 85 / 91
    r_dash   = int(91 * scale)   # outer dashed ring  ≈ 85
    r_solid  = int(82 * scale)   # outer solid ring   ≈ 76
    r_arc    = int(74 * scale)   # arc-text radius    ≈ 69
    r_inner  = int(63 * scale)   # inner ring         ≈ 58
    r_sep    = int(58 * scale)   # inner separator    ≈ 54
    r_disc   = int(54 * scale)   # white disc         ≈ 51

    # Outer dashed ring
    c.setStrokeColor(_GREY_6A)
    c.setLineWidth(1.4)
    c.setDash(3.8, 2.4)
    c.circle(cx, cy, r_dash, fill=False, stroke=True)
    c.setDash()

    # Outer solid ring
    c.setLineWidth(0.55)
    c.circle(cx, cy, r_solid, fill=False, stroke=True)

    # Arc text — top: "EDUCATION FOR EVERYONE"
    _arc_text(c, 'EDUCATION FOR EVERYONE', cx, cy, r_arc, 90,
              'Helvetica-Bold', 6.5, _GREY_44, letter_spacing=2.8, top_arc=True)

    # Separator dots at 9 o'clock (180°) and 3 o'clock (0°)
    c.setFillColor(_GREY_6A)
    c.circle(cx - r_arc, cy, 2.0, fill=True, stroke=False)
    c.circle(cx + r_arc, cy, 2.0, fill=True, stroke=False)

    # Arc text — bottom: "COURSE CERTIFICATE"
    _arc_text(c, 'COURSE CERTIFICATE', cx, cy, r_arc, 270,
              'Helvetica-Bold', 6.5, _GREY_44, letter_spacing=2.8, top_arc=False)

    # Inner ring + separator
    c.setStrokeColor(_GREY_6A)
    c.setLineWidth(1.4)
    c.circle(cx, cy, r_inner, fill=False, stroke=True)
    c.setLineWidth(0.4)
    c.setStrokeColor(_GREY_99)
    c.circle(cx, cy, r_sep, fill=False, stroke=True)

    # White centre disc
    c.setFillColor(_WHITE)
    c.circle(cx, cy, r_disc, fill=True, stroke=False)

    # Brand name
    c.setFont('Helvetica-Bold', 12)
    c.setFillColor(_NEAR_BLACK)
    c.drawCentredString(cx, cy + 4,  'career')
    c.drawCentredString(cx, cy - 10, 'college')

    # Small dot + "CERTIFIED" label
    c.setFillColor(_GREY_BB)
    c.circle(cx, cy - 19, 1.2, fill=True, stroke=False)
    c.setFont('Helvetica', 5)
    c.setFillColor(_GREY_99)
    c.drawCentredString(cx, cy - 27, 'CERTIFIED')


def _draw_sunburst(c, cx, cy):
    """12-spoke sunburst + 3 concentric rings — used as a watermark on the right panel."""
    c.saveState()
    c.setStrokeColor(colors.Color(0.07, 0.07, 0.07, alpha=0.13))
    R = 96
    c.setLineWidth(0.7)
    for i in range(12):
        a = math.radians(i * 15)
        c.line(cx + R * math.cos(a), cy + R * math.sin(a),
               cx - R * math.cos(a), cy - R * math.sin(a))
    c.setLineWidth(0.5)
    for frac in (1.0, 0.67, 0.38):
        c.circle(cx, cy, R * frac, fill=False, stroke=True)
    c.restoreState()


def _draw_signature(c, base_x, base_y):
    """
    Approximate the SVG handwriting flourish + baseline stroke from the template.

    base_x, base_y  = bottom-left corner of the 155 × 44 pt drawing area.
    SVG y (top-down) is converted to reportlab y (bottom-up) via (44 - svg_y).
    """
    h = 44

    def r(sx, sy):   # SVG point → reportlab point
        return base_x + sx, base_y + (h - sy)

    # ── Main flourish ─────────────────────────────────────────────────────────
    c.setStrokeColor(colors.HexColor('#2a2a2a'))
    c.setLineWidth(1.6)
    c.setLineCap(1)    # round
    c.setLineJoin(1)   # round

    p = c.beginPath()
    p.moveTo(*r(6, 34))
    p.curveTo(*r(14, 16),  *r(22, 38),  *r(34, 24))
    p.curveTo(*r(43, 13),  *r(48, 32),  *r(60, 20))
    p.curveTo(*r(69, 11),  *r(74, 30),  *r(88, 20))
    p.curveTo(*r(99, 12),  *r(104, 28), *r(118, 21))
    p.curveTo(*r(126, 17), *r(130, 26), *r(138, 23))
    p.curveTo(*r(143, 21), *r(148, 25), *r(150, 24))
    c.drawPath(p, fill=False, stroke=True)

    # ── Baseline stroke (quadratic → cubic) ──────────────────────────────────
    # SVG: M 6,39  Q 78,46  150,36
    sx, sy = r(6, 39)
    qx, qy = r(78, 46)
    ex, ey = r(150, 36)
    cp1x = sx + 2/3 * (qx - sx)
    cp1y = sy + 2/3 * (qy - sy)
    cp2x = ex + 2/3 * (qx - ex)
    cp2y = ey + 2/3 * (qy - ey)

    c.setLineWidth(0.7)
    p2 = c.beginPath()
    p2.moveTo(sx, sy)
    p2.curveTo(cp1x, cp1y, cp2x, cp2y, ex, ey)
    c.drawPath(p2, fill=False, stroke=True)

    # Reset line cap/join to defaults
    c.setLineCap(0)
    c.setLineJoin(0)


def _draw_spaced_text(c, text, x, y, font, size, color, spacing):
    """Draw text with explicit inter-character spacing, left-aligned from x."""
    c.setFont(font, size)
    c.setFillColor(color)
    cur_x = x
    for ch in text:
        c.drawString(cur_x, y, ch)
        cur_x += c.stringWidth(ch, font, size) + spacing


def _spaced_text_width(c, text, font, size, spacing):
    c.setFont(font, size)
    return sum(c.stringWidth(ch, font, size) for ch in text) + spacing * max(0, len(text) - 1)


# ── Main generator ────────────────────────────────────────────────────────────

def generate_certificate_pdf(certificate) -> bytes:
    """
    Render a landscape A4 certificate PDF and return raw bytes.

    Layout mirrors demo_certificate_template.html:
      Left  67% — off-white background, diagonal stripe texture, L-corner marks,
                  logo, issue date, learner name, course title, signature block.
      Right 33% — grey panel (#C6C9CE), sunburst watermark, COURSE/CERTIFICATE
                  heading, circular SVG-style seal, verify URL.
    """
    buffer = io.BytesIO()
    width, height = landscape(A4)     # 841.89 × 595.28 pt
    c = canvas.Canvas(buffer, pagesize=landscape(A4))

    left_w  = width * 0.67            # ≈ 564 pt
    right_w = width - left_w          # ≈ 278 pt
    right_x = left_w

    # ── Column backgrounds ────────────────────────────────────────────────────
    c.setFillColor(_PAPER)
    c.rect(0, 0, left_w, height, fill=True, stroke=False)

    c.setFillColor(_RIGHT_GREY)
    c.rect(right_x, 0, right_w, height, fill=True, stroke=False)

    # ── Diagonal stripe texture on left column ────────────────────────────────
    c.saveState()
    clip = c.beginPath()
    clip.rect(0, 0, left_w, height)
    c.clipPath(clip, stroke=False, fill=False)
    c.setStrokeColor(colors.Color(0, 0, 0, alpha=0.013))
    c.setLineWidth(0.8)
    ang = math.radians(-48)
    dx, dy = math.cos(ang), math.sin(ang)
    nx, ny = -dy, dx                  # perpendicular (normal) to stripe direction
    step = 35
    for i in range(-20, 70):
        ox = i * step * nx
        oy = i * step * ny
        L = 1400
        c.line(ox - L*dx, oy - L*dy, ox + L*dx, oy + L*dy)
    c.restoreState()

    # ── L-shaped corner registration marks ───────────────────────────────────
    cm = 17       # arm length
    off = 11      # distance from page edge
    c.setStrokeColor(_CORNER_MK)
    c.setLineWidth(1.5)
    for px, py, h_sign, v_sign in [
        (off,         height - off, +1, -1),   # top-left
        (width - off, height - off, -1, -1),   # top-right
        (off,         off,          +1, +1),   # bottom-left
        (width - off, off,          -1, +1),   # bottom-right
    ]:
        c.line(px, py, px + h_sign * cm, py)   # horizontal arm
        c.line(px, py, px, py + v_sign * cm)   # vertical arm

    # ═════════════════════════════════════════════════════════════════════════
    # LEFT COLUMN
    # ═════════════════════════════════════════════════════════════════════════
    pad_l = 50
    avail_w = left_w - pad_l - 44      # usable text width

    # ── Logo ──────────────────────────────────────────────────────────────────
    logo_sq = 42
    logo_x  = pad_l
    logo_y  = height - 32 - logo_sq   # bottom of square

    c.setFillColor(_NAVY)
    c.rect(logo_x, logo_y, logo_sq, logo_sq, fill=True, stroke=False)

    c.setFont('Times-Bold', 16)
    c.setFillColor(_GOLD)
    c.drawCentredString(logo_x + logo_sq / 2, logo_y + 12, 'CC')

    wm_x = logo_x + logo_sq + 11
    c.setFont('Helvetica-Bold', 7)
    c.setFillColor(_GREY_99)
    # c.drawString(wm_x, logo_y + logo_sq - 11, 'School of')
    c.setFont('Helvetica-Bold', 17)
    c.setFillColor(_NAVY)
    c.drawString(wm_x, logo_y + 4, 'CAREER COLLEGE')

    # ── Certificate body ──────────────────────────────────────────────────────
    # Vertically centred between logo bottom and signature top.
    # Signature top ≈ 190pt.  Logo bottom ≈ height - 32 - 42 = height - 74.
    # Midpoint ≈ (190 + height - 74) / 2.  Space is ~303pt; layout ~150pt tall.
    body_top = (height - 74 + 190) / 2 + 75    # approximate centre of body

    # Issue date
    issued_date = certificate.issued_at.strftime('%B %d, %Y')
    date_y = body_top
    c.setFont('Helvetica', 10)
    c.setFillColor(_GREY_99)
    c.drawString(pad_l, date_y, issued_date)

    # Learner name — large, bold, uppercase
    name = certificate.learner_name.upper()
    name_sz = 32
    while c.stringWidth(name, 'Helvetica-Bold', name_sz) > avail_w and name_sz > 16:
        name_sz -= 1
    name_y = date_y - 14 - name_sz
    c.setFont('Helvetica-Bold', name_sz)
    c.setFillColor(_NEAR_BLACK)
    c.drawString(pad_l, name_y, name)

    # "has successfully completed"
    comp_y = name_y - 26
    c.setFont('Helvetica', 10)
    c.setFillColor(_GREY_66)
    c.drawString(pad_l, comp_y, 'has successfully completed')

    # Course title (serif, normal weight)
    course_title = certificate.course_title
    title_sz = 15
    while c.stringWidth(course_title, 'Times-Roman', title_sz) > avail_w * 0.90 and title_sz > 10:
        title_sz -= 1
    title_y = comp_y - title_sz - 8
    c.setFont('Times-Roman', title_sz)
    c.setFillColor(_NEAR_BLACK)
    c.drawString(pad_l, title_y, course_title)

    # Attribution
    attr_y = title_y - 18
    c.setFont('Helvetica', 8.5)
    c.setFillColor(_GREY_88)
    c.drawString(pad_l, attr_y,      'an online course authorized by Career College and offered')
    c.drawString(pad_l, attr_y - 12, 'through the Career College learning platform.')

    # ── Signature block ───────────────────────────────────────────────────────
    # sig_svg_bottom = bottom-left of the 155×44 SVG drawing area
    sig_svg_bottom = 148
    # _draw_signature(c, pad_l, sig_svg_bottom)

    # Dotted rule (below SVG, matching .sig-rule)
    rule_y = sig_svg_bottom - 6
    c.setStrokeColor(_GREY_BB)
    c.setLineWidth(1)
    c.setDash(1, 3)
    c.line(pad_l, rule_y, pad_l + 162, rule_y)
    c.setDash()

    # Instructor name & meta
    try:
        instructor_name = certificate.enrollment.course.created_by.full_name
    except Exception:
        instructor_name = 'Career College'
    instructor_title = 'Course Instructor'

    c.setFont('Helvetica-Bold', 10.5)
    c.setFillColor(_GREY_22)
    c.drawString(pad_l, rule_y - 14, instructor_name)

    c.setFont('Helvetica', 9)
    c.setFillColor(_GREY_77)
    c.drawString(pad_l, rule_y - 26, instructor_title)
    c.drawString(pad_l, rule_y - 38, 'Career College')

    # ═════════════════════════════════════════════════════════════════════════
    # RIGHT COLUMN
    # ═════════════════════════════════════════════════════════════════════════
    right_cx = right_x + right_w / 2

    # ── Sunburst watermark ────────────────────────────────────────────────────
    _draw_sunburst(c, right_cx, height / 2)

    # ── "COURSE / CERTIFICATE" heading with 4pt letter-spacing ───────────────
    heading_font = 'Helvetica-Bold'
    heading_sz   = 18
    heading_sp   = 4   # inter-character spacing (pt)

    for line, base_y in [('COURSE', height - 65), ('CERTIFICATE', height - 90)]:
        lw = _spaced_text_width(c, line, heading_font, heading_sz, heading_sp)
        _draw_spaced_text(c, line, right_cx - lw / 2, base_y,
                          heading_font, heading_sz, _NEAR_BLACK, heading_sp)

    # ── Seal ──────────────────────────────────────────────────────────────────
    _draw_seal(c, right_cx, height / 2 + 10)

    # ── Verify block (right-aligned) ──────────────────────────────────────────
    verify_x = right_x + right_w - 16
    verify_y  = 72

    frontend = getattr(settings, 'FRONTEND_URL', 'https://careercollege.com').rstrip('/')
    domain   = frontend.replace('https://', '').replace('http://', '')
    verify_str = f'{domain}/verify/{certificate.certificate_uid}'

    c.setFont('Helvetica-Bold', 7)
    c.setFillColor(_GREY_3A)
    c.drawRightString(verify_x, verify_y, verify_str)

    c.setFont('Helvetica', 6.5)
    c.setFillColor(_GREY_55)
    c.drawRightString(verify_x, verify_y - 11,
                      'Career College has confirmed the identity of this')
    c.drawRightString(verify_x, verify_y - 21,
                      'individual and their participation in the course.')

    c.save()
    return buffer.getvalue()
