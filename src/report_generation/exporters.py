"""把报告模型导出为 Markdown 与 DOCX 的模块。

排版规范与 internal/report/exporters.py 对齐：
A4 页面与边距、中文字体宋体 / 西文 Times New Roman、正文 10.5pt、
封面页、页眉页脚、自动更新目录域、标题与表格编号规范化、表格列宽与字号。

对外保持 export_markdown / export_docx 两个入口不变，字段在内部适配：
- 章节用 title/level（internal 用 heading/level）
- 表格用 headers/rows（internal 用 columns/rows）
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from docx import Document
from docx.shared import Mm, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT, WD_TAB_LEADER
from docx.enum.table import WD_TABLE_ALIGNMENT, WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.section import WD_SECTION
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

# ===== 排版规范 =====
CN_FONT = '宋体'
EN_FONT = 'Times New Roman'
BODY_SIZE = 10.5
HEADING_SIZES = {1: 18, 2: 16, 3: 16, 4: 14}
USABLE_TWIPS = 8300


def _set_east_asia(rPr, cn=CN_FONT, en=EN_FONT):
    rFonts = rPr.first_child_found_in('w:rFonts')
    if rFonts is None:
        rFonts = OxmlElement('w:rFonts')
        rPr.append(rFonts)
    # 模板样式的 rFonts 带主题字体属性，Word 解析时优先于显式字体，
    # 不移除会导致标题渲染为等线/Calibri Light 而非宋体/Times New Roman
    for attr in ('asciiTheme', 'eastAsiaTheme', 'hAnsiTheme', 'cstheme'):
        rFonts.attrib.pop(qn('w:' + attr), None)
    rFonts.set(qn('w:ascii'), en)
    rFonts.set(qn('w:hAnsi'), en)
    rFonts.set(qn('w:eastAsia'), cn)


def set_font(run, size=None, bold=None):
    run.font.name = EN_FONT
    _set_east_asia(run._element.get_or_add_rPr())
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold


def repeat_header(row):
    trPr = row._tr.get_or_add_trPr()
    el = OxmlElement('w:tblHeader')
    el.set(qn('w:val'), 'true')
    trPr.append(el)


def prevent_row_split(row):
    """Prevent Word from splitting a table row across two pages."""
    trPr = row._tr.get_or_add_trPr()
    if trPr.find(qn('w:cantSplit')) is None:
        trPr.append(OxmlElement('w:cantSplit'))


def cell_width(cell, twips):
    tcPr = cell._tc.get_or_add_tcPr()
    tcW = tcPr.first_child_found_in('w:tcW')
    if tcW is None:
        tcW = OxmlElement('w:tcW')
        tcPr.append(tcW)
    tcW.set(qn('w:w'), str(twips))
    tcW.set(qn('w:type'), 'dxa')


def _table_font_size(ncols):
    """配置基准 10.5pt；列数多时按阶梯缩小，避免超出可用页宽。"""
    if ncols <= 5:
        return 10.5
    if ncols == 6:
        return 9.5
    return 9.0


def set_cell_text(cell, text, bold=False, size=BODY_SIZE):
    cell.text = ''
    p = cell.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.line_spacing = 1.5
    r = p.add_run('' if text is None else str(text))
    set_font(r, size, bold)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def _widths_for(n, table_id=None):
    """通用等比列宽（尽量铺满可用宽度）。可按 table_id 后续补充细粒度列宽。"""
    n = max(int(n), 1)
    base = USABLE_TWIPS // n
    remainder = USABLE_TWIPS - base * n
    return [base + (1 if i < remainder else 0) for i in range(n)]


def configure(doc):
    sec = doc.sections[0]
    sec.page_width = Mm(210)
    sec.page_height = Mm(297)
    sec.top_margin = Mm(25)
    sec.bottom_margin = Mm(25)
    sec.left_margin = Mm(31.7)
    sec.right_margin = Mm(31.7)
    styles = doc.styles
    normal = styles['Normal']
    normal.font.name = EN_FONT
    _set_east_asia(normal.element.get_or_add_rPr())
    normal.font.size = Pt(BODY_SIZE)
    for name, size in [('Title', 22), ('Heading 1', 18), ('Heading 2', 16), ('Heading 3', 16), ('Heading 4', 14)]:
        s = styles[name]
        s.font.name = EN_FONT
        _set_east_asia(s.element.get_or_add_rPr())
        s.font.size = Pt(size)
        s.font.bold = True
        s.font.color.rgb = RGBColor(0, 0, 0)
    return doc


def normalize_heading(text, level):
    """标题编号规范化: 一级 "{n}."、其余 "{n}.{n}"(无尾点)。"""
    s = str(text or '').strip()
    m = re.match(r'^(\d+(?:\.\d+)*)[\s.、]*(.+)$', s)
    if not m:
        return s
    num, rest = m.group(1), m.group(2).strip()
    prefix = f'{num}.' if level == 1 and '.' not in num else num
    return f'{prefix} {rest}'


def _table_caption(table_id, caption):
    """把重构侧分开存储的表号与表题合并为 "表X.Y 标题"。"""
    tid = str(table_id or '').strip()
    cap = str(caption or '').strip()
    if tid and cap:
        return f'{tid} {cap}'
    return cap or tid


def _field_run(p, instr, size=9):
    r = p.add_run()
    set_font(r, size)
    b = OxmlElement('w:fldChar')
    b.set(qn('w:fldCharType'), 'begin')
    i = OxmlElement('w:instrText')
    i.set(qn('xml:space'), 'preserve')
    i.text = instr
    e = OxmlElement('w:fldChar')
    e.set(qn('w:fldCharType'), 'end')
    r._r.append(b)
    r._r.append(i)
    r._r.append(e)


def _auto_update_fields(doc):
    """打开文档时提示更新域（目录等）。"""
    s = doc.settings.element
    if s.find(qn('w:updateFields')) is None:
        el = OxmlElement('w:updateFields')
        el.set(qn('w:val'), 'true')
        s.append(el)


def _cover(report):
    cover = report.get('cover') or {}
    pname = str(cover.get('project_name') or report.get('project_name') or '').strip()
    rtitle = str(cover.get('report_title') or '可行性研究报告（初稿）').strip()
    return {
        'company_name': str(cover.get('company_name') or '').strip(),
        'project_name': pname,
        'report_title': rtitle,
        'project_code': str(cover.get('project_code') or '').strip(),
    }


def _cover_fields(report):
    cover = _cover(report)
    company = cover['company_name']
    if company == '待明确':
        company = ''
    return company, cover['project_name'], cover['report_title'], cover['project_code']


def _render_cover(doc, cover):
    """封面四要素：公司名 / 项目名 / 报告标题 / 项目编号。"""
    company = str(cover.get('company_name') or '').strip()
    pname = str(cover.get('project_name') or cover.get('title') or '').strip()
    rtitle = str(cover.get('report_title') or cover.get('subtitle') or '可行性研究报告').strip()
    code = str(cover.get('project_code') or '').strip()

    def cline(text, size, bold):
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(0)
        r = p.add_run(text)
        set_font(r, size, bold)

    for _ in range(2):
        doc.add_paragraph()
    if company:
        cline(company, 16, False)
    for _ in range(5):
        doc.add_paragraph()
    if pname:
        cline(pname, 20 if len(pname) > 18 else 22, True)
    cline(rtitle, 26, True)
    for _ in range(5):
        doc.add_paragraph()
    if code:
        cline(f'项目编号：{code}', 12, False)


def _setup_body_section(doc, project_name):
    """封面节无页眉页脚；正文节页眉为项目名，页脚页码居中且从 1 起。"""
    sec = doc.add_section(WD_SECTION.NEW_PAGE)
    pg = OxmlElement('w:pgNumType')
    pg.set(qn('w:start'), '1')
    sec._sectPr.append(pg)
    sec.header.is_linked_to_previous = False
    hp = sec.header.paragraphs[0]
    hp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = hp.add_run(f'{project_name} 可行性研究报告')
    set_font(r, 9)
    sec.footer.is_linked_to_previous = False
    fp = sec.footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _field_run(fp, 'PAGE', 9)
    return sec


def _add_toc(doc, entries):
    """预渲染 1-3 级目录条目，含点线前导符与页码；内嵌 TOC 域供 Word 更新。"""
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run('目  录')
    set_font(r, 16, True)
    if not entries:
        ph = doc.add_paragraph()
        ph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        rr = ph.add_run('（目录将在打开文档时自动更新）')
        set_font(rr, BODY_SIZE)
        return

    def fld_char(type_):
        el = OxmlElement('w:fldChar')
        el.set(qn('w:fldCharType'), type_)
        return el

    def instr_run(p_, instr):
        r_ = p_.add_run()
        set_font(r_, BODY_SIZE)
        i = OxmlElement('w:instrText')
        i.set(qn('xml:space'), 'preserve')
        i.text = instr
        r_._r.append(i)

    for idx, (level, text) in enumerate(entries):
        ep = doc.add_paragraph()
        ep.paragraph_format.left_indent = Mm(7.4 * (level - 1))
        ep.paragraph_format.line_spacing = 1.5
        ep.paragraph_format.space_after = Pt(0)
        # 页码统一对齐正文右边界，不随层级缩进左移
        ep.paragraph_format.tab_stops.add_tab_stop(Mm(146.6), WD_TAB_ALIGNMENT.RIGHT, WD_TAB_LEADER.DOTS)
        is_l1 = (level == 1)
        if idx == 0:
            r_ = ep.add_run()
            set_font(r_, BODY_SIZE)
            r_._r.append(fld_char('begin'))
            instr_run(ep, r'TOC \o "1-3" \h \z \u')
            r_ = ep.add_run()
            set_font(r_, BODY_SIZE)
            r_._r.append(fld_char('separate'))
        r_ = ep.add_run(text)
        set_font(r_, BODY_SIZE, is_l1)
        r_ = ep.add_run(f'\t{idx + 1}')
        set_font(r_, BODY_SIZE)
        if idx == len(entries) - 1:
            r_ = ep.add_run()
            set_font(r_, BODY_SIZE)
            r_._r.append(fld_char('end'))


def add_para(doc, text, indent=True, bold=False):
    p = doc.add_paragraph()
    p.paragraph_format.line_spacing = 1.5
    p.paragraph_format.space_after = Pt(0)
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    if indent:
        p.paragraph_format.first_line_indent = Mm(7.4)
    r = p.add_run(str(text))
    set_font(r, BODY_SIZE, bold)
    return p


def add_bullet(doc, text):
    p = doc.add_paragraph(style='List Bullet')
    p.paragraph_format.line_spacing = 1.5
    p.paragraph_format.space_after = Pt(0)
    r = p.add_run(str(text))
    set_font(r, BODY_SIZE)
    return p


def add_heading(doc, text, level):
    level = max(1, min(int(level), 4))
    h = doc.add_heading(normalize_heading(text, level), level)
    if level == 1:
        h.paragraph_format.page_break_before = True
    return h


def add_table(doc, block):
    cols = block.get('headers') or block.get('columns') or []
    rows = block.get('rows') or []
    caption = _table_caption(block.get('table_id'), block.get('caption'))
    if caption:
        p = doc.add_paragraph()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_after = Pt(2)
        r = p.add_run(caption)
        set_font(r, BODY_SIZE, True)
    fs = _table_font_size(len(cols))
    t = doc.add_table(rows=1, cols=len(cols))
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.style = 'Table Grid'
    t.autofit = False
    widths = _widths_for(len(cols), block.get('table_id'))
    for i, c in enumerate(cols):
        set_cell_text(t.rows[0].cells[i], c, True, fs)
        cell_width(t.rows[0].cells[i], widths[i])
    repeat_header(t.rows[0])
    prevent_row_split(t.rows[0])
    for row in rows:
        table_row = t.add_row()
        prevent_row_split(table_row)
        cells = table_row.cells
        for i, v in enumerate(row):
            set_cell_text(cells[i], v, False, fs)
            cell_width(cells[i], widths[i] if i < len(widths) else widths[-1])
    doc.add_paragraph().paragraph_format.space_after = Pt(0)


def _collect_toc(report):
    entries = []
    for sec in report.get('sections') or []:
        level = int(sec.get('level', 1))
        title = str(sec.get('title') or '').strip()
        if title and level <= 3:
            entries.append((level, normalize_heading(title, level)))
        for block in sec.get('blocks') or []:
            if block.get('type') == 'heading':
                bl = int(block.get('level', 2))
                btext = str(block.get('text') or '').strip()
                if btext and bl <= 3:
                    entries.append((bl, normalize_heading(btext, bl)))
    return entries


def _docx_block(doc, block):
    typ = block.get('type')
    if typ == 'paragraph':
        add_para(doc, block.get('text', ''), block.get('indent', True), block.get('bold', False))
    elif typ == 'numbered_list':
        for i, x in enumerate(block.get('items') or [], 1):
            add_para(doc, f'{i}）{x}', False)
    elif typ == 'bullet_list':
        for x in block.get('items') or []:
            add_bullet(doc, x)
    elif typ == 'table':
        add_table(doc, block)
    elif typ == 'heading':
        add_heading(doc, block.get('text', ''), int(block.get('level', 2)))
    elif typ == 'page_break':
        doc.add_page_break()


def export_docx(report, output_path):
    doc = configure(Document())
    _render_cover(doc, _cover(report))
    company, pname, rtitle, code = _cover_fields(report)
    _setup_body_section(doc, pname or '改造项目')
    _add_toc(doc, _collect_toc(report))
    for sec in report.get('sections') or []:
        heading = sec.get('title')
        level = int(sec.get('level', 1))
        if heading:
            add_heading(doc, heading, level)
        for block in sec.get('blocks') or []:
            _docx_block(doc, block)
    _auto_update_fields(doc)
    props = report.get('properties') or {}
    doc.core_properties.title = props.get('title') or f"{report.get('project_name', '')}可行性研究报告（初稿）"
    doc.core_properties.subject = props.get('subject') or '流程工业改造项目可行性研究报告'
    doc.core_properties.author = ''
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output_path)
    return output_path


# ===== Markdown 导出（与 DOCX 共用标题编号/表题合成规则，正文一致） =====


def _text(value):
    return '' if value is None else str(value)


def _escape_table_cell(value):
    return _text(value).replace('\\', '\\\\').replace('|', '\\|').replace('\r\n', '<br>').replace('\n', '<br>')


def _heading(text, level):
    level = max(1, min(int(level or 1), 6))
    return f"{'#' * level} {normalize_heading(text, level)}".rstrip()


def render_model(report):
    lines = []
    company, pname, rtitle, code = _cover_fields(report)
    if company:
        lines.append(f'**{company}**')
        lines.append('')
    if pname:
        lines.append(f'# {pname}')
        lines.append('')
    lines.append(f'**{rtitle}**')
    lines.append('')
    if code:
        lines.append(f'项目编号：{code}')
        lines.append('')
    lines.extend(['---', ''])
    toc = _collect_toc(report)
    if toc:
        lines.append('## 目录')
        lines.append('')
        for level, title in toc:
            lines.append(f"{'  ' * (level - 1)}- {title}")
        lines.extend(['', '---', ''])
    for sec in report.get('sections') or []:
        heading = sec.get('title')
        if heading:
            lines.append(_heading(heading, sec.get('level', 1)))
            lines.append('')
        for block in sec.get('blocks') or []:
            typ = block.get('type')
            if typ == 'paragraph':
                text = _text(block.get('text', '')).strip()
                if text:
                    lines.append(text)
                    lines.append('')
            elif typ == 'numbered_list':
                for i, item in enumerate(block.get('items') or [], 1):
                    lines.append(f'{i}. {_text(item)}')
                if block.get('items'):
                    lines.append('')
            elif typ == 'bullet_list':
                for item in block.get('items') or []:
                    lines.append(f'- {_text(item)}')
                if block.get('items'):
                    lines.append('')
            elif typ == 'table':
                caption = _table_caption(block.get('table_id'), block.get('caption')).strip()
                if caption:
                    lines.append(f'**{caption}**')
                    lines.append('')
                cols = block.get('headers') or block.get('columns') or []
                rows = block.get('rows') or []
                if cols:
                    lines.append('| ' + ' | '.join(_escape_table_cell(c) for c in cols) + ' |')
                    lines.append('| ' + ' | '.join('---' for _ in cols) + ' |')
                    for row in rows:
                        vals = list(row or [])
                        if len(vals) < len(cols):
                            vals += [''] * (len(cols) - len(vals))
                        vals = vals[:len(cols)]
                        lines.append('| ' + ' | '.join(_escape_table_cell(v) for v in vals) + ' |')
                    lines.append('')
            elif typ == 'heading':
                text = _text(block.get('text', '')).strip()
                if text:
                    lines.append(_heading(text, block.get('level', 2)))
                    lines.append('')
            elif typ == 'page_break':
                lines.extend(['---', ''])
    while lines and lines[-1] == '':
        lines.pop()
    return '\n'.join(lines) + '\n'


def export_markdown(report, output_path):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_model(report), encoding='utf-8')
    return output_path
