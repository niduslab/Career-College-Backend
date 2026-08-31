"""
Certificate PDF generator.

Landscape A4, centred and symmetric:

  Header  — brand mark, wordmark, "CERTIFICATE OF COMPLETION", ornament rule
  Body    — award statement, learner name in script, course title, metadata strip
  Footer  — instructor signature · seal · authorized signature
  Strip   — verification URL and QR code

Every value is read from the certificate's frozen snapshot, never the live
course or profile rows, so re-rendering an old certificate reproduces the
original exactly. The palette mirrors the frontend's brand tokens
(src/app/globals.css) — keep the two in sync.
"""

import io
import logging
import math
import os

import reportlab
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from courses.services.certificate_service import build_verification_url

logger = logging.getLogger(__name__)

# Register a Unicode-capable bold font for learner name rendering.
# VeraBd ships with every ReportLab installation and covers Latin Extended,
# Greek, and Cyrillic — far wider than the built-in Type1 Helvetica (Latin-1
# only). For CJK / Arabic coverage, drop a Noto Sans TTF into the project,
# register it here, and point _UNICODE_BOLD at the registered name.
_REPORTLAB_FONTS = os.path.join(os.path.dirname(reportlab.__file__), 'fonts')

_UNICODE_BOLD = 'Helvetica-Bold'  # fallback if VeraBd is missing
try:
    _vera_bd = os.path.join(_REPORTLAB_FONTS, 'VeraBd.ttf')
    if os.path.isfile(_vera_bd):
        pdfmetrics.registerFont(TTFont('VeraBd', _vera_bd))
        _UNICODE_BOLD = 'VeraBd'
except Exception:
    pass

# Bundled application art — read from the package directory, not through
# default_storage (these are shipped assets, not user-uploaded media).
_ASSETS = os.path.join(os.path.dirname(__file__), 'assets')
_LOGO_PATH = os.path.join(_ASSETS, 'career-college-logo.webp')

# Script face for the learner name. Great Vibes, SIL OFL (see OFL-GreatVibes.txt
# beside the font). Latin only — a name outside that range falls back to
# _UNICODE_BOLD, which covers Latin Extended / Greek / Cyrillic.
_SCRIPT_FONT = None
try:
    _gv = os.path.join(_ASSETS, 'GreatVibes-Regular.ttf')
    if os.path.isfile(_gv):
        pdfmetrics.registerFont(TTFont('GreatVibes', _gv))
        _SCRIPT_FONT = 'GreatVibes'
except Exception:
    pass

# ── Palette ───────────────────────────────────────────────────────────────────
# Mirrors the brand tokens in the frontend's src/app/globals.css so the PDF, the
# web verify page and the dashboard read as one system. Keep the two in sync:
# these are --primary-* and --gray-* by another name.
_PRIMARY_700 = colors.HexColor('#6f15ec')   # --primary-700, brand purple
_PRIMARY_900 = colors.HexColor('#4d10a2')   # --primary-900, deep purple
_PRIMARY_950 = colors.HexColor('#2e076e')   # --primary-950, darkest purple
_PRIMARY_400 = colors.HexColor('#a37fff')   # --primary-400, light accent
_PRIMARY_100 = colors.HexColor('#ece7ff')   # --primary-100, tint

_PAPER      = colors.HexColor('#FCFBFF')    # near-white with a purple cast
_NEAR_BLACK = colors.HexColor('#100d14')    # --text-title
_GREY_22    = colors.HexColor('#101828')    # --gray-900
_GREY_3A    = colors.HexColor('#1e2939')    # --gray-800
_GREY_44    = colors.HexColor('#364153')    # --gray-700
_GREY_55    = colors.HexColor('#4e4758')    # --text-paragraph
_GREY_66    = colors.HexColor('#4a5565')    # --gray-600
_GREY_6A    = colors.HexColor('#6a7282')    # --gray-500
_GREY_77    = colors.HexColor('#6a7282')    # --gray-500
_GREY_88    = colors.HexColor('#99a1af')    # --gray-400
_GREY_99    = colors.HexColor('#99a1af')    # --gray-400
_GREY_BB    = colors.HexColor('#d1d5dc')    # --gray-300
_WHITE      = colors.HexColor('#ffffff')    # --text-white

# Legacy aliases kept so the drawing helpers read naturally; both now resolve to
# brand purple rather than the old navy/gold pair.
_NAVY       = _PRIMARY_950
_GOLD       = _PRIMARY_700
_RIGHT_GREY = colors.HexColor('#e5e7eb')    # --gray-200
_CORNER_MK  = _PRIMARY_400


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


def _draw_seal(c, cx, cy, outer=85):
    """
    Circular institutional seal that mirrors the SVG in demo_certificate_template.html.

    SVG viewBox 200×200, paths at r=74 → scaled so the outer edge lands on `outer`.
    """
    scale    = outer / 91
    r_dash   = int(91 * scale)   # outer dashed ring  ≈ 85
    r_solid  = int(82 * scale)   # outer solid ring   ≈ 76
    r_arc    = int(74 * scale)   # arc-text radius    ≈ 69
    r_inner  = int(63 * scale)   # inner ring         ≈ 58
    r_sep    = int(58 * scale)   # inner separator    ≈ 54
    r_disc   = int(54 * scale)   # white disc         ≈ 51

    # Outer dashed ring
    c.setStrokeColor(_PRIMARY_700)
    c.setLineWidth(1.4)
    c.setDash(3.8, 2.4)
    c.circle(cx, cy, r_dash, fill=False, stroke=True)
    c.setDash()

    # Outer solid ring
    c.setLineWidth(0.55)
    c.circle(cx, cy, r_solid, fill=False, stroke=True)

    # Arc text — top: "EDUCATION FOR EVERYONE"
    _arc_text(c, 'EDUCATION FOR EVERYONE', cx, cy, r_arc, 90,
              'Helvetica-Bold', 6.5 * scale, _PRIMARY_950, letter_spacing=2.8 * scale, top_arc=True)

    # Separator dots at 9 o'clock (180°) and 3 o'clock (0°)
    c.setFillColor(_PRIMARY_700)
    c.circle(cx - r_arc, cy, 2.0, fill=True, stroke=False)
    c.circle(cx + r_arc, cy, 2.0, fill=True, stroke=False)

    # Arc text — bottom: "COURSE CERTIFICATE"
    _arc_text(c, 'COURSE CERTIFICATE', cx, cy, r_arc, 270,
              'Helvetica-Bold', 6.5 * scale, _PRIMARY_950, letter_spacing=2.8 * scale, top_arc=False)

    # Inner ring + separator
    c.setStrokeColor(_PRIMARY_700)
    c.setLineWidth(1.4)
    c.circle(cx, cy, r_inner, fill=False, stroke=True)
    c.setLineWidth(0.4)
    c.setStrokeColor(_PRIMARY_400)
    c.circle(cx, cy, r_sep, fill=False, stroke=True)

    # White centre disc
    c.setFillColor(_WHITE)
    c.circle(cx, cy, r_disc, fill=True, stroke=False)

    # Brand name — sized off `scale` so the seal stays legible when resized.
    c.setFont('Helvetica-Bold', 12 * scale)
    c.setFillColor(_PRIMARY_950)
    c.drawCentredString(cx, cy + 4 * scale,  'career')
    c.drawCentredString(cx, cy - 10 * scale, 'college')

    # Small dot + "CERTIFIED" label
    c.setFillColor(_GREY_BB)
    c.circle(cx, cy - 19 * scale, 1.2 * scale, fill=True, stroke=False)
    c.setFont('Helvetica', 5 * scale)
    c.setFillColor(_GREY_99)
    c.drawCentredString(cx, cy - 27 * scale, 'CERTIFIED')


def _draw_signature_image(c, image_field, base_x, base_y, max_w=155, max_h=44):
    """Draw a stored signature image, scaled to fit and bottom-left anchored.

    Reads through the FieldFile so it works on S3 as well as local disk — never
    .path(). Returns True when something was drawn; the caller falls back to the
    hand-drawn flourish otherwise, so a missing or corrupt image never breaks
    the PDF.
    """
    if not image_field:
        return False
    try:
        image_field.open('rb')
        try:
            data = image_field.read()
        finally:
            image_field.close()

        reader = ImageReader(io.BytesIO(data))
        iw, ih = reader.getSize()
        if not iw or not ih:
            return False

        scale = min(max_w / iw, max_h / ih)
        w, h = iw * scale, ih * scale
        # mask='auto' honours PNG alpha so a transparent signature does not
        # paint a white box over the certificate background.
        c.drawImage(reader, base_x, base_y, width=w, height=h,
                    mask='auto', preserveAspectRatio=True, anchor='sw')
        return True
    except Exception:
        logger.warning(
            'Certificate signature image could not be drawn: %s',
            getattr(image_field, 'name', '?'), exc_info=True,
        )
        return False


def _draw_signatory_column(c, x, col_w, baseline_y, role, name, designation, org,
                           signature_field):
    """One centred signature column: signature, rule, role, name, title, org.

    `role` ("COURSE INSTRUCTOR" / "AUTHORIZED SIGNATORY") sits directly under the
    rule and is what tells a reader which column is which — without it the two
    are identically shaped and indistinguishable.

    Everything is centred inside `col_w` so the two columns balance around the
    seal regardless of how wide the names are.
    """
    mid = x + col_w / 2

    # The image draws from its bottom-left, so centre it by hand.
    #
    # Nothing is drawn when there is no stored signature. The old hand-drawn
    # flourish fallback was removed deliberately: an invented squiggle above a
    # real person's name reads as that person's signature, which it is not.
    # Blank space above the rule is the honest state, and matches how a paper
    # certificate looks before it is signed.
    if signature_field:
        _draw_signature_image(
            c, signature_field, mid - 70, baseline_y, max_w=140, max_h=42)

    rule_y = baseline_y - 6
    c.setStrokeColor(_GREY_88)
    c.setLineWidth(0.7)
    c.line(x + 12, rule_y, x + col_w - 12, rule_y)

    # Role first, in brand purple caps — the one line that distinguishes the
    # two columns from each other.
    if role:
        lw = _spaced_text_width(c, role, 'Helvetica-Bold', 6.5, 1.6)
        _draw_spaced_text(c, role, mid - lw / 2, rule_y - 13,
                          'Helvetica-Bold', 6.5, _PRIMARY_700, 1.6)

    if name:
        fitted, sz = _fit_text(c, name, 'Helvetica-Bold', 10.5, 8, col_w - 16)
        c.setFont('Helvetica-Bold', sz)
        c.setFillColor(_GREY_22)
        c.drawCentredString(mid, rule_y - 27, fitted)

    if designation:
        fitted, sz = _fit_text(c, designation, 'Helvetica', 8.5, 7, col_w - 16)
        c.setFont('Helvetica', sz)
        c.setFillColor(_GREY_77)
        c.drawCentredString(mid, rule_y - 39, fitted)

    if org:
        fitted, sz = _fit_text(c, org, 'Helvetica', 8.5, 7, col_w - 16)
        c.setFont('Helvetica', sz)
        c.setFillColor(_GREY_99)
        c.drawCentredString(mid, rule_y - 50, fitted)


def _is_latin(text) -> bool:
    """True when every character is covered by the Latin-only script font."""
    return all(ord(ch) < 0x0250 for ch in text)


def _title_case(name) -> str:
    """Title-case a name without mangling the parts that are already styled.

    "MD. AL AMIN" → "Md. Al Amin", but "McDonald" and "O'Brien" are left alone —
    a token that already mixes cases is assumed to be deliberate.
    """
    out = []
    for word in name.split():
        if word[1:].islower() or (any(ch.isupper() for ch in word[1:]) and not word.isupper()):
            out.append(word)
        else:
            out.append(word.capitalize())
    return ' '.join(out)


def _fit_text(c, text, font, max_size, min_size, max_width):
    """Shrink font until text fits max_width, then truncate with ellipsis if still too wide."""
    size = max_size
    while c.stringWidth(text, font, size) > max_width and size > min_size:
        size -= 1
    if c.stringWidth(text, font, size) > max_width:
        while text and c.stringWidth(text + '…', font, size) > max_width:
            text = text[:-1]
        text = text + '…'
    return text, size


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


def _draw_qr(c, data, x, y, size=54):
    """Draw a QR code encoding `data`, bottom-left anchored at (x, y).

    Best-effort: a QR failure must never cost the learner their PDF — the
    verification URL is printed as text beside it either way. Returns True when
    something was drawn so the caller can lay out around it.
    """
    try:
        import qrcode

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=0,
        )
        qr.add_data(data)
        qr.make(fit=True)

        buf = io.BytesIO()
        # Brand purple (--primary-950) keeps enough contrast to scan reliably.
        qr.make_image(fill_color='#2e076e', back_color='white').save(buf, format='PNG')
        buf.seek(0)

        c.drawImage(ImageReader(buf), x, y, width=size, height=size,
                    mask='auto', preserveAspectRatio=True, anchor='sw')
        return True
    except Exception:
        logger.warning('Certificate QR code could not be drawn.', exc_info=True)
        return False


def _draw_ornament(c, cx, y, width=90, color=None):
    """Thin rule broken by a small diamond at its centre — a classic divider."""
    color = color or _GOLD
    half = width / 2
    c.setStrokeColor(color)
    c.setLineWidth(0.8)
    c.line(cx - half, y, cx - 7, y)
    c.line(cx + 7, y, cx + half, y)

    c.setFillColor(color)
    p = c.beginPath()
    p.moveTo(cx, y + 3.2)
    p.lineTo(cx + 3.2, y)
    p.lineTo(cx, y - 3.2)
    p.lineTo(cx - 3.2, y)
    p.close()
    c.drawPath(p, fill=True, stroke=False)


def _draw_corner_flourish(c, x, y, h_sign, v_sign, color=None):
    """Double L-bracket corner ornament, drawn just inside the gold frame."""
    color = color or _GOLD
    c.setStrokeColor(color)
    for arm, lw, off in ((26, 1.4, 0), (17, 0.6, 5)):
        c.setLineWidth(lw)
        ox, oy = off * h_sign, off * v_sign
        c.line(x + ox, y + oy, x + ox + h_sign * arm, y + oy)
        c.line(x + ox, y + oy, x + ox, y + oy + v_sign * arm)


def _draw_wordmark(c, cx, y, mark=40):
    """Centred brand mark with the wordmark beneath it.

    Uses the real logo bundled at courses/assets/. Falls back to a drawn CC
    monogram if the file is missing or unreadable, so the PDF never breaks on a
    packaging mistake.
    """
    drawn = False
    if _LOGO_PATH and os.path.isfile(_LOGO_PATH):
        try:
            reader = ImageReader(_LOGO_PATH)
            iw, ih = reader.getSize()
            scale = mark / max(iw, ih)
            w, h = iw * scale, ih * scale
            c.drawImage(reader, cx - w / 2, y, width=w, height=h,
                        mask='auto', preserveAspectRatio=True, anchor='sw')
            drawn = True
        except Exception:
            logger.warning('Certificate logo could not be drawn.', exc_info=True)

    if not drawn:
        box = 34
        c.setFillColor(_PRIMARY_950)
        c.rect(cx - box / 2, y, box, box, fill=True, stroke=False)
        c.setStrokeColor(_PRIMARY_400)
        c.setLineWidth(0.7)
        c.rect(cx - box / 2 + 3, y + 3, box - 6, box - 6, fill=False, stroke=True)
        c.setFont('Times-Bold', 14)
        c.setFillColor(_WHITE)
        c.drawCentredString(cx, y + 12, 'CC')

    label = 'CAREER COLLEGE'
    lw = _spaced_text_width(c, label, 'Helvetica-Bold', 11, 3.4)
    _draw_spaced_text(c, label, cx - lw / 2, y - 16,
                      'Helvetica-Bold', 11, _PRIMARY_950, 3.4)


# ── Main generator ────────────────────────────────────────────────────────────

def generate_certificate_pdf(certificate) -> bytes:
    """
    Render a landscape A4 certificate PDF and return raw bytes.

    Centred, symmetric layout: a double gold frame with corner flourishes, the
    wordmark and title stacked at the top, the award statement centred in the
    body over a faint seal watermark, then a three-column footer (instructor
    signature · seal · authorized signature) above a verification strip.

    Every value comes from the certificate's frozen snapshot, never the live
    course or profile rows — re-rendering an old certificate reproduces the
    original exactly.
    """
    buffer = io.BytesIO()
    width, height = landscape(A4)     # 841.89 × 595.28 pt
    c = canvas.Canvas(buffer, pagesize=landscape(A4))
    cx = width / 2

    issuer = certificate.issuer_name or 'Career College'

    # ── Background ────────────────────────────────────────────────────────────
    # Deliberately flat: the tinted disc + sunburst watermark that used to sit
    # behind the body added texture but no meaning, and it fought the name.
    c.setFillColor(_PAPER)
    c.rect(0, 0, width, height, fill=True, stroke=False)

    # ── Double frame + corner flourishes ─────────────────────────────────────
    # Deep purple outer, light inner — a solid brand-purple double rule reads as
    # loud at this weight.
    for inset, lw, col in ((22, 1.4, _PRIMARY_950), (28, 0.5, _PRIMARY_400)):
        c.setStrokeColor(col)
        c.setLineWidth(lw)
        c.rect(inset, inset, width - 2 * inset, height - 2 * inset,
               fill=False, stroke=True)

    f = 36
    for fx, fy, hs, vs in ((f, height - f, +1, -1), (width - f, height - f, -1, -1),
                           (f, f, +1, +1), (width - f, f, -1, +1)):
        _draw_corner_flourish(c, fx, fy, hs, vs, color=_PRIMARY_950)

    # ── Header ────────────────────────────────────────────────────────────────
    _draw_wordmark(c, cx, height - 88)

    title = 'CERTIFICATE OF COMPLETION'
    tw = _spaced_text_width(c, title, 'Helvetica-Bold', 20, 5.5)
    _draw_spaced_text(c, title, cx - tw / 2, height - 148,
                      'Helvetica-Bold', 20, _NEAR_BLACK, 5.5)
    _draw_ornament(c, cx, height - 164, width=170)

    # ── Body ──────────────────────────────────────────────────────────────────
    avail = width - 200

    c.setFont('Times-Italic', 11.5)
    c.setFillColor(_GREY_66)
    c.drawCentredString(cx, height - 196, 'This is to certify that')

    # Learner name — the focal point, set in script (Great Vibes) and title case.
    # Script faces are drawn for mixed case; ALL CAPS in script runs together.
    #
    # Great Vibes is Latin-only, so a name outside that range falls back to
    # _UNICODE_BOLD (VeraBd: Latin Extended, Greek, Cyrillic) rather than
    # rendering as tofu. _fit_text guards against very long names either way.
    raw_name = certificate.learner_name.strip()
    name_font, base_sz, min_sz = _UNICODE_BOLD, 34, 17
    if _SCRIPT_FONT and _is_latin(raw_name):
        raw_name = _title_case(raw_name)
        # Script x-height is much smaller than a sans at the same point size.
        name_font, base_sz, min_sz = _SCRIPT_FONT, 54, 26
    else:
        raw_name = raw_name.upper()

    name, name_sz = _fit_text(c, raw_name, name_font, base_sz, min_sz, avail)
    name_y = height - 214 - name_sz * 0.72
    c.setFont(name_font, name_sz)
    c.setFillColor(_PRIMARY_950)
    c.drawCentredString(cx, name_y, name)

    rule_w = min(c.stringWidth(name, name_font, name_sz) + 70, avail)
    c.setStrokeColor(_PRIMARY_400)
    c.setLineWidth(0.9)
    c.line(cx - rule_w / 2, name_y - 13, cx + rule_w / 2, name_y - 13)

    c.setFont('Times-Italic', 11.5)
    c.setFillColor(_GREY_66)
    c.drawCentredString(cx, name_y - 36, 'has successfully completed the course')

    course_title, title_sz = _fit_text(
        c, certificate.course_title, 'Times-Bold', 19, 12, avail * 0.9)
    course_y = name_y - 50 - title_sz
    c.setFont('Times-Bold', title_sz)
    c.setFillColor(_NEAR_BLACK)
    c.drawCentredString(cx, course_y, course_title)

    c.setFont('Helvetica', 8.5)
    c.setFillColor(_GREY_88)
    c.drawCentredString(
        cx, course_y - 20,
        f'an online course authorized by {issuer} and offered through '
        'the Career College learning platform.')

    # ── Credential metadata strip ─────────────────────────────────────────────
    meta = []
    if certificate.course_duration:
        meta.append(('COURSE DURATION', certificate.course_duration))
    if certificate.learning_hours:
        meta.append(('LEARNING HOURS', f'{certificate.learning_hours} Hours'))
    if certificate.completion_date:
        meta.append(('COMPLETION DATE',
                     certificate.completion_date.strftime('%B %d, %Y')))
    if certificate.certificate_id:
        meta.append(('CERTIFICATE ID', certificate.certificate_id))

    if meta:
        strip_y = course_y - 62
        slot = min(185, (width - 200) / len(meta))
        span = slot * len(meta)
        start = cx - span / 2 + slot / 2

        for i, (label, value) in enumerate(meta):
            col = start + i * slot
            lw = _spaced_text_width(c, label, 'Helvetica', 6.5, 1.2)
            _draw_spaced_text(c, label, col - lw / 2, strip_y + 9,
                              'Helvetica', 6.5, _GREY_99, 1.2)

            fitted, sz = _fit_text(c, value, 'Helvetica-Bold', 9.5, 7, slot - 16)
            c.setFont('Helvetica-Bold', sz)
            c.setFillColor(_GREY_22)
            c.drawCentredString(col, strip_y - 5, fitted)

            if i:
                c.setStrokeColor(_GREY_BB)
                c.setLineWidth(0.4)
                c.line(col - slot / 2, strip_y - 10, col - slot / 2, strip_y + 15)

    # ── Footer: signature · seal · signature ──────────────────────────────────
    # Raised above the verification strip so the QR at bottom-right has its own
    # band and never overlaps the right-hand signature column.
    sig_baseline = 164
    col_w = 175
    left_x = 96
    right_x = width - 96 - col_w

    _draw_signatory_column(
        c, left_x, col_w, sig_baseline, 'COURSE INSTRUCTOR',
        certificate.instructor_name, certificate.instructor_designation,
        issuer, certificate.instructor_signature,
    )

    # Smaller than the default so it sits between the two signature columns
    # without crowding the metadata strip above.
    _draw_seal(c, cx, sig_baseline - 24, outer=44)

    if certificate.authorized_signatory_name:
        _draw_signatory_column(
            c, right_x, col_w, sig_baseline, 'AUTHORIZED SIGNATORY',
            certificate.authorized_signatory_name,
            certificate.authorized_signatory_designation,
            issuer, certificate.authorized_signature,
        )

    # ── Verification strip ────────────────────────────────────────────────────
    # The URL comes from the shared helper, so the QR, the printed text and the
    # API payload can never disagree.
    verify_url = build_verification_url(certificate)
    display = verify_url.replace('https://', '').replace('http://', '')

    c.setStrokeColor(_GOLD)
    c.setLineWidth(0.5)
    c.line(96, 84, width - 96, 84)

    qr_size = 46
    qr_x = width - 96 - qr_size
    qr_y = 42
    has_qr = _draw_qr(c, verify_url, qr_x, qr_y, size=qr_size)

    if has_qr:
        c.setFont('Helvetica-Bold', 5.5)
        c.setFillColor(_GREY_99)
        c.drawCentredString(qr_x + qr_size / 2, qr_y - 8, 'SCAN TO VERIFY')

    # Centred on the page when there is no QR; shifted left of it when there is,
    # so the two blocks never overlap.
    text_cx = cx - (qr_size / 2 + 14) if has_qr else cx

    c.setFont('Helvetica-Bold', 7.5)
    c.setFillColor(_GREY_3A)
    c.drawCentredString(text_cx, 68, display)

    c.setFont('Helvetica', 6.5)
    c.setFillColor(_GREY_55)
    c.drawCentredString(
        text_cx, 56,
        f'{issuer} has confirmed the identity of this individual')
    c.drawCentredString(
        text_cx, 46, 'and their participation in the course.')

    c.save()
    return buffer.getvalue()

