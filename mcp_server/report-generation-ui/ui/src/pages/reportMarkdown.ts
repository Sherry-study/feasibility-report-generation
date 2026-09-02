/**
 * 可行性研究报告 Markdown 解析与渲染（轻量实现，无第三方依赖）。
 *
 * 仅服务于「可研成果交付 - 报告编制」页：
 *   - 解析报告名称、一级章节、二级章节；
 *   - 把正文区渲染为一段连续完整的 HTML 文档（每个标题带锚点 id），
 *     目录点击后滚动定位到对应标题。
 *
 * 不做章节写死、不引入新的报告数据结构 —— 目录与正文完全由 Markdown 标题结构驱动。
 */

/** 正文区标题节点（含行号，用于目录树与内容定位）。 */
export interface ReportHeading {
  /** 1..6，对应 #..######。 */
  level: number;
  /** 标题文本（不含 #）。 */
  text: string;
  /** 0-based 行号。 */
  line: number;
  /** 稳定锚点 id（按行号生成，渲染时写入标题元素 id）。 */
  id: string;
}

/** 二级章节（挂在一级章节下）。 */
export interface ReportSection {
  id: string;
  text: string;
  level: number;
  line: number;
  endLine: number;
}

/** 一级章节。 */
export interface ReportChapter {
  id: string;
  text: string;
  line: number;
  endLine: number;
  sections: ReportSection[];
}

/** 解析后的报告文档。 */
export interface ReportDoc {
  /** 报告名称（首个 H1）。 */
  title: string;
  /** 副标题（front matter 中首个加粗独占行）。 */
  subtitle: string;
  /** 一级章节。 */
  chapters: ReportChapter[];
  /** 正文区全部标题（1..6 级），按行号排序。 */
  headings: ReportHeading[];
  /** 原文按行拆分（供全文渲染）。 */
  lines: string[];
}

const HEADING_RE = /^(#{1,6})\s+(.*)$/;
const HR_RE = /^(-{3,}|\*{3,}|_{3,})$/;
const IMAGE_RE = /^!\[([^\]]*)\]\(([^)\s]+)\)$/;
const TABLE_SEP_CELL_RE = /^:?-{2,}:?$/;

function splitRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((c) => c.trim());
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/** 行内元素：图片占位 / 链接 / 加粗 / 斜体 / 行内代码。 */
function inline(text: string): string {
  let s = escapeHtml(text);
  s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (_m, alt: string) => {
    const label = alt || '图片';
    return `<span class="md-img-inline">图片：${escapeHtml(label)}</span>`;
  });
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  return s;
}

/** 表格：表头 + 分隔行 + 若干数据行。 */
function parseTable(lines: string[], start: number): { html: string; next: number } | null {
  const header = lines[start].trim();
  const sep = (lines[start + 1] ?? '').trim();
  if (!header.startsWith('|') || !sep.startsWith('|') || !sep.includes('-')) return null;

  const sepCells = splitRow(sep);
  if (sepCells.length === 0 || !sepCells.every((c) => TABLE_SEP_CELL_RE.test(c))) return null;

  const headers = splitRow(header);
  const rows: string[][] = [];
  let i = start + 2;
  while (i < lines.length && lines[i].trim().startsWith('|')) {
    rows.push(splitRow(lines[i]));
    i += 1;
  }

  const thead = `<tr>${headers.map((c) => `<th>${inline(c)}</th>`).join('')}</tr>`;
  const tbody = rows
    .map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`)
    .join('');
  return {
    html: `<div class="md-table-wrap"><table><thead>${thead}</thead><tbody>${tbody}</tbody></table></div>`,
    next: i,
  };
}

/** 有序 / 无序列表。 */
function parseList(lines: string[], start: number): { html: string; next: number } | null {
  const ordered = /^\s*\d+\.\s+/.test(lines[start]);
  const re = ordered ? /^\s*\d+\.\s+/ : /^\s*[-*+]\s+/;
  if (!re.test(lines[start])) return null;

  const items: string[] = [];
  let i = start;
  while (i < lines.length && re.test(lines[i])) {
    items.push(lines[i].replace(re, ''));
    i += 1;
  }
  const tag = ordered ? 'ol' : 'ul';
  return {
    html: `<${tag}>${items.map((t) => `<li>${inline(t)}</li>`).join('')}</${tag}>`,
    next: i,
  };
}

/** 是否为块级起始行（标题 / 表格 / 列表 / 引用 / 分隔线）。 */
function isBlockStart(line: string): boolean {
  const t = line.trim();
  return (
    HEADING_RE.test(line) ||
    t.startsWith('|') ||
    /^\s*(\d+\.|[-*+])\s+/.test(line) ||
    t.startsWith('>') ||
    HR_RE.test(t)
  );
}

/**
 * 把 lines[start..] 渲染为 HTML。
 *
 * withHeadingIds=true 时，标题元素写入 id="h-<行号>"，供目录滚动定位。
 */
function renderLines(lines: string[], start: number, withHeadingIds: boolean): string {
  const out: string[] = [];
  let i = start;

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    if (trimmed === '') {
      i += 1;
      continue;
    }

    // 分隔线
    if (HR_RE.test(trimmed)) {
      out.push('<hr/>');
      i += 1;
      continue;
    }

    // 标题
    const hm = HEADING_RE.exec(line);
    if (hm) {
      const level = Math.min(hm[1].length, 6);
      const text = hm[2].replace(/\s+#+\s*$/, '').trim();
      const idAttr = withHeadingIds ? ` id="h-${i}"` : '';
      out.push(`<h${level}${idAttr}>${inline(text)}</h${level}>`);
      i += 1;
      continue;
    }

    // 表格
    if (trimmed.startsWith('|')) {
      const table = parseTable(lines, i);
      if (table) {
        out.push(table.html);
        i = table.next;
        continue;
      }
    }

    // 列表
    const list = parseList(lines, i);
    if (list) {
      out.push(list.html);
      i = list.next;
      continue;
    }

    // 引用
    if (trimmed.startsWith('>')) {
      const buf: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith('>')) {
        buf.push(lines[i].trim().replace(/^>\s?/, ''));
        i += 1;
      }
      out.push(`<blockquote>${renderLines(buf, 0, false)}</blockquote>`);
      continue;
    }

    // 独立图片占位
    const img = IMAGE_RE.exec(trimmed);
    if (img) {
      const alt = escapeHtml(img[1] || '图片');
      out.push(`<div class="md-img-placeholder">图片：${alt}</div>`);
      i += 1;
      continue;
    }

    // 段落：聚集到空行或下一个块级起始
    const buf: string[] = [];
    while (i < lines.length && lines[i].trim() !== '' && !isBlockStart(lines[i])) {
      buf.push(lines[i]);
      i += 1;
    }
    if (buf.length > 0) {
      out.push(`<p>${inline(buf.join(' '))}</p>`);
    } else {
      // 防御：无法归类的行（如非表格的 | 行）按段落处理，避免死循环
      out.push(`<p>${inline(line)}</p>`);
      i += 1;
    }
  }

  return out.join('\n');
}

/** 解析报告文档：报告名称 + 一级/二级目录 + 正文区标题（带行号）。 */
export function parseReportDoc(markdown: string): ReportDoc {
  const lines = markdown.split(/\r?\n/);

  const headings: ReportHeading[] = [];
  lines.forEach((raw, line) => {
    const m = HEADING_RE.exec(raw);
    if (!m) return;
    const text = m[2].replace(/\s+#+\s*$/, '').trim();
    headings.push({ level: m[1].length, text, line, id: `h-${line}` });
  });

  const firstH1 = headings.find((h) => h.level === 1);
  const title = firstH1?.text ?? '可行性研究报告';

  // 副标题：front matter（首个 --- 之前）中第一个加粗独占行
  let subtitle = '可行性研究报告（初稿）';
  const firstHr = lines.findIndex((l) => HR_RE.test(l.trim()));
  const frontEnd = firstHr >= 0 ? firstHr : Math.min(20, lines.length);
  for (let i = 0; i < frontEnd; i += 1) {
    const m = /^\*\*(.+?)\*\*$/.exec(lines[i].trim());
    if (m) {
      subtitle = m[1];
      break;
    }
  }

  // 正文区：从「目录」标题之后开始（无目录时排除标题 H1 及其之前内容）
  const tocIdx = headings.findIndex((h) => h.text === '目录');
  let bodyHeadings: ReportHeading[];
  if (tocIdx >= 0) {
    bodyHeadings = headings.slice(tocIdx + 1);
  } else if (firstH1) {
    bodyHeadings = headings.filter((h) => h.line > firstH1.line);
  } else {
    bodyHeadings = headings;
  }

  const h1s = bodyHeadings.filter((h) => h.level === 1);
  const chapters: ReportChapter[] = h1s.map((h, idx) => {
    const endLine = idx + 1 < h1s.length ? h1s[idx + 1].line : lines.length;
    const h2s = bodyHeadings.filter((x) => x.level === 2 && x.line > h.line && x.line < endLine);
    const sections: ReportSection[] = h2s.map((x, j) => ({
      id: x.id,
      text: x.text,
      level: x.level,
      line: x.line,
      endLine: j + 1 < h2s.length ? h2s[j + 1].line : endLine,
    }));
    return { id: h.id, text: h.text, line: h.line, endLine, sections };
  });

  return { title, subtitle, chapters, headings: bodyHeadings, lines };
}

/** 把正文区（从第一个一级章节到文末）渲染为连续完整的 HTML 文档。 */
export function renderFullDocument(doc: ReportDoc): string {
  const start = doc.chapters.length > 0 ? doc.chapters[0].line : 0;
  return renderLines(doc.lines, start, true);
}
