"""Build the Russian and English user-guide PDFs from their Markdown sources.

Requires reportlab and pypdf. Use --font-dir for a directory containing
DejaVuSans.ttf, DejaVuSans-Bold.ttf and DejaVuSans-Oblique.ttf, or Arial files.
"""

import argparse
import html
import re
from pathlib import Path

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, CondPageBreak, Frame, Image, KeepTogether, PageBreak,
    PageTemplate, Paragraph, Spacer, Table, TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

ROOT = Path(__file__).resolve().parents[1]
INK = colors.HexColor('#172B46')
BLUE = colors.HexColor('#1F6085')
MUTED = colors.HexColor('#526578')
LIGHT = colors.HexColor('#EAF1F6')
WIDTH, HEIGHT = A4
MARGIN = 19 * mm
CONTENT_WIDTH = WIDTH - 2 * MARGIN


def register_fonts(directory):
    candidates = [directory] if directory else [
        Path('C:/Windows/Fonts'), Path('/usr/share/fonts/truetype/dejavu'),
    ]
    for folder in candidates:
        for regular, bold, italic in [
            ('arial.ttf', 'arialbd.ttf', 'ariali.ttf'),
            ('DejaVuSans.ttf', 'DejaVuSans-Bold.ttf', 'DejaVuSans-Oblique.ttf'),
        ]:
            if all((folder / file).is_file() for file in (regular, bold, italic)):
                for name, file in [('Guide', regular), ('Guide-Bold', bold), ('Guide-Italic', italic)]:
                    pdfmetrics.registerFont(TTFont(name, str(folder / file)))
                pdfmetrics.registerFontFamily('Guide', normal='Guide', bold='Guide-Bold',
                                             italic='Guide-Italic', boldItalic='Guide-Bold')
                return
    raise SystemExit('Arial or DejaVu Sans fonts not found; supply --font-dir.')


def inline(text):
    text = html.escape(text.replace('—', '-').replace('–', '-').replace('\u2011', '-'))
    text = re.sub(r'`([^`]+)`', r'<font color="#1F6085">\1</font>', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\[([^\]]+)\]\((#[^)]+)\)',
                  lambda m: f'<link href="{m[2]}" color="#1F6085">{m[1]}</link>', text)
    return text


def styles():
    base = dict(fontName='Guide', fontSize=10.3, leading=14.7, textColor=INK,
                spaceAfter=7, alignment=TA_LEFT)
    normal = ParagraphStyle('Body', **base)
    return {
        'body': normal,
        'h2': ParagraphStyle('Section', parent=normal, fontName='Guide-Bold',
                             fontSize=17, leading=21, spaceBefore=16, spaceAfter=11,
                             keepWithNext=True),
        'h3': ParagraphStyle('Subsection', parent=normal, fontName='Guide-Bold',
                             fontSize=11.5, leading=15.5, spaceBefore=10,
                             spaceAfter=6, keepWithNext=True),
        'cell': ParagraphStyle('Cell', parent=normal, fontSize=9.1, leading=12.6,
                               spaceAfter=0),
        'head': ParagraphStyle('TableHead', parent=normal, fontName='Guide-Bold',
                               fontSize=9.2, leading=12.5, textColor=colors.white,
                               spaceAfter=0),
        'quote': ParagraphStyle('Note', parent=normal, backColor=LIGHT,
                                borderPadding=10, leftIndent=10, rightIndent=10,
                                spaceBefore=7, spaceAfter=13),
        'list': ParagraphStyle('List', parent=normal, leftIndent=17, firstLineIndent=-17,
                               spaceAfter=5),
        'caption': ParagraphStyle('Caption', parent=normal, fontSize=8.5, leading=11.5,
                                  textColor=MUTED, spaceBefore=5, spaceAfter=12),
    }


class GuideDocument(BaseDocTemplate):
    def __init__(self, destination, language, **kwargs):
        super().__init__(str(destination), pagesize=A4, leftMargin=MARGIN,
                         rightMargin=MARGIN, topMargin=20*mm, bottomMargin=19*mm,
                         title='OKF Knowledge Service - ' + ('Руководство пользователя' if language == 'ru' else 'User guide'),
                         author='OKF Knowledge Service', **kwargs)
        self.language = language
        frame = Frame(MARGIN, 19*mm, CONTENT_WIDTH, HEIGHT-39*mm,
                      leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
        self.addPageTemplates(PageTemplate(id='Guide', frames=[frame], onPage=self.decorate))

    def decorate(self, canvas, doc):
        canvas.saveState()
        if doc.page > 1:
            canvas.setFont('Guide', 8)
            canvas.setFillColor(MUTED)
            canvas.drawString(MARGIN, HEIGHT-12*mm, 'OKF KNOWLEDGE SERVICE')
            canvas.drawRightString(WIDTH-MARGIN, HEIGHT-12*mm,
                                   'Руководство пользователя · RU' if self.language == 'ru' else 'User guide · EN')
            canvas.setStrokeColor(colors.HexColor('#CEDAE4'))
            canvas.line(MARGIN, HEIGHT-15*mm, WIDTH-MARGIN, HEIGHT-15*mm)
            canvas.line(MARGIN, 14*mm, WIDTH-MARGIN, 14*mm)
            canvas.setFont('Guide', 8)
            canvas.drawString(MARGIN, 9*mm, '18.10.2026')
            canvas.drawRightString(WIDTH-MARGIN, 9*mm, str(doc.page))
        else:
            canvas.setFillColor(INK)
            canvas.rect(0, HEIGHT-29*mm, WIDTH, 29*mm, fill=1, stroke=0)
            canvas.setFillColor(colors.white)
            canvas.setFont('Guide-Bold', 11)
            canvas.drawString(MARGIN, HEIGHT-19*mm, 'OKF / KNOWLEDGE SERVICE')
            canvas.setFillColor(BLUE)
            canvas.rect(MARGIN, 53*mm, 32*mm, 2*mm, fill=1, stroke=0)
        canvas.restoreState()

    def afterFlowable(self, flowable):
        if hasattr(flowable, 'guide_key'):
            key = flowable.guide_key
            title = flowable.getPlainText()
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(title, key, 0)
            self.notify('TOCEntry', (0, title, self.page, key))


def table(rows, style):
    cells = [[Paragraph(inline(cell), style['head'] if i == 0 else style['cell'])
              for cell in row] for i, row in enumerate(rows)]
    count = len(rows[0])
    widths = [CONTENT_WIDTH * 0.34, CONTENT_WIDTH * 0.66] if count == 2 else [CONTENT_WIDTH/count]*count
    item = Table(cells, colWidths=widths, repeatRows=1, hAlign='LEFT')
    item.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), BLUE),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [LIGHT, colors.white]),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 7),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 7),
        ('LINEBELOW', (0, 0), (-1, 0), 0.6, BLUE),
        ('LINEBELOW', (0, 1), (-1, -1), 0.3, colors.HexColor('#D7E1E9')),
    ]))
    return item


def build(source, language):
    text = source.read_text(encoding='utf-8-sig')
    style = styles()
    preface, body = text.split('<a id="guide-01"></a>', 1)
    intro = [line for line in preface.splitlines() if line.strip()]
    story = [Spacer(1, 43*mm),
             Paragraph('OKF Knowledge<br/>Service', ParagraphStyle('CoverBrand',
                       fontName='Guide-Bold', fontSize=34, leading=41, textColor=INK)),
             Spacer(1, 12*mm),
             Paragraph(inline(intro[1]), ParagraphStyle('CoverTitle', fontName='Guide',
                       fontSize=24, leading=30, textColor=BLUE)),
             Spacer(1, 8*mm), Paragraph(inline(intro[2]), style['body']),
             Spacer(1, 12*mm), Paragraph(inline(intro[3]), style['body']), PageBreak()]
    toc = TableOfContents()
    toc.levelStyles = [ParagraphStyle('TOC', fontName='Guide', fontSize=10.5,
                                      leading=21, textColor=INK, leftIndent=0,
                                      firstLineIndent=0, spaceBefore=0)]
    story += [Paragraph('Содержание' if language == 'ru' else 'Contents', style['h2']),
              Spacer(1, 7*mm), toc, PageBreak()]
    lines = ('<a id="guide-01"></a>' + body).splitlines()
    i, key = 0, None
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1
            continue
        anchor = re.fullmatch(r'<a id="([^"]+)"></a>', line)
        if anchor:
            key = anchor[1]
        elif line.startswith('## '):
            story.append(CondPageBreak(42*mm))
            paragraph = Paragraph(inline(line[3:]), style['h2'])
            paragraph.guide_key = key
            story.append(paragraph)
        elif line.startswith('### '):
            story.append(Paragraph(inline(line[4:]), style['h3']))
        elif line.startswith('|'):
            rows = []
            while i < len(lines) and lines[i].strip().startswith('|'):
                row = [cell.strip() for cell in lines[i].strip().strip('|').split('|')]
                if not all(re.fullmatch(r':?-+:?', cell) for cell in row):
                    rows.append(row)
                i += 1
            story += [table(rows, style), Spacer(1, 9)]
            continue
        elif line.startswith('!['):
            match = re.fullmatch(r'!\[([^]]*)\]\(([^)]+)\)', line)
            if not match:
                raise ValueError(f'Unsupported image: {line}')
            path = source.parent / match[2]
            pic = Image(str(path))
            scale = min(CONTENT_WIDTH / pic.imageWidth, 110*mm / pic.imageHeight)
            pic.drawWidth = pic.imageWidth * scale
            pic.drawHeight = pic.imageHeight * scale
            pic.hAlign = 'LEFT'
            story.append(KeepTogether([pic, Paragraph(inline(match[1]), style['caption'])]))
        elif line.startswith('> '):
            story.append(Paragraph(inline(line[2:]), style['quote']))
        elif line.startswith('- ') or re.match(r'\d+\. ', line):
            if line.startswith('- '):
                line = '• ' + line[2:]
            story.append(Paragraph(inline(line), style['list']))
        else:
            parts = [line]
            while i+1 < len(lines) and lines[i+1].strip() and not re.match(r'^(#|<a |\||!\[|>|- |\d+\. )', lines[i+1]):
                i += 1
                parts.append(lines[i].strip())
            story.append(Paragraph(inline(' '.join(parts)), style['body']))
        i += 1
    output = source.with_suffix('.pdf')
    document = GuideDocument(output, language)
    document.multiBuild(story)
    reader = PdfReader(output)
    extracted = '\n'.join(page.extract_text() or '' for page in reader.pages)
    expected = re.findall(r'^## (\d+\. .+)$', text, re.M)
    for heading in expected:
        normalized = lambda value: re.sub(r'\s+', '', value).replace('—', '-').replace('–', '-')
        if normalized(heading) not in normalized(extracted):
            raise ValueError(f'Missing PDF heading: {heading}')
    if len(reader.outline) != len(expected):
        raise ValueError('PDF bookmark count differs from section count')
    print(f'{output.name}: {len(reader.pages)} pages, {len(reader.outline)} bookmarks, {output.stat().st_size:,} bytes')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--font-dir', type=Path)
    args = parser.parse_args()
    register_fonts(args.font_dir)
    for name, locale in [('OKF_User_Guide.md', 'ru'), ('OKF_User_Guide_EN.md', 'en')]:
        build(ROOT / 'docs' / name, locale)
