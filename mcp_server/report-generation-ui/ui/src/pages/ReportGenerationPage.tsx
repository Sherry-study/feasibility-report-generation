/**
 * 阶段④报告生成页面。
 *
 * 对应 MCP 工具 ``report_generation``。结果就绪后提供两个工作台：
 *   - 报告编制（默认）：可研成果 / 可研报告目录 / 正文展示 三栏。
 *     目录与正文由 ``report_output_dir/可行性研究报告_初稿.md`` 的标题结构动态解析，
 *     不写死章节、不新增报告数据结构。
 *   - 资料确认：展示 exit_code / 阻断章节 / 产物路径等生成结果摘要。
 *
 * 视觉：浅蓝白工业软件风格（对齐 AI for Redesign Demo，见 index.css 的 .ef-* 类）。
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { Layout } from '@/components/Layout';
import { ErrorBanner } from '@/components/ErrorBanner';
import { TaskProgress } from '@/components/TaskProgress';
import { JsonDrawer } from '@/components/JsonDrawer';
import { useMcpApp, useNormalizedToolResult } from '@/core/mcpApp';
import { SectionCard, InfoCell } from '@/components/common';
import { useReportMarkdown } from './useReportMarkdown';
import { renderFullDocument, type ReportDoc } from './reportMarkdown';

/** 阻断章节（blocked_sections 单项，来自缺口分析）。 */
interface BlockedSection {
  section_id?: string;
  field?: string;
  reason?: string;
  affected_sections?: string[];
  recommended_action?: string;
}

/** 一致性检查问题（consistency_check.issues 单项）。 */
interface ConsistencyIssue {
  severity?: string;
  code?: string;
  message?: string;
  section_id?: string;
}

/** report_generation 返回结构（structuredContent）。 */
interface ReportGenerationResult {
  exit_code?: number;
  status?: string;
  completion_status?: string;
  resume_exit_code?: number;
  blocked_section_count?: number;
  blocked_sections?: BlockedSection[];
  docx?: string;
  markdown?: string;
  /** 报告 Markdown 正文（算法生成后内联进返回结果，前端直接回显）。 */
  markdown_content?: string;
  facts?: string;
  report_model?: string;
  consistency_check?: string;
  report_trace?: string;
  issues?: ConsistencyIssue[];
}

/** 状态 -> 文案与语义色。 */
const STATUS_META: Record<string, { label: string; color: 'green' | 'orange' | 'red' }> = {
  generated: { label: '生成完成', color: 'green' },
  consistency_blocked: { label: '一致性阻断', color: 'red' },
};

/** completion_status -> 文案。 */
const COMPLETION_LABEL: Record<string, string> = {
  generated: '数据闭合',
  generated_with_blocked_sections: '含阻断章节生成',
};

/** 需要展示的产物路径字段（label -> key）。 */
const ARTIFACT_FIELDS: Array<{ key: keyof ReportGenerationResult; label: string }> = [
  { key: 'report_model', label: '报告模型' },
  { key: 'consistency_check', label: '一致性检查' },
  { key: 'report_trace', label: '报告溯源' },
  { key: 'facts', label: '确认后事实' },
];

/** 报告文件状态文案。 */
function reportStatusText(status: string | undefined): string {
  if (status === 'generated') return '初稿生成完成';
  if (status === 'consistency_blocked') return '一致性阻断';
  return '初稿已生成';
}

export function ReportGenerationPage() {
  const app = useMcpApp();
  const { finalResult: rawFinalResult, isError } = useNormalizedToolResult();
  const error = isError ? app.error : null;
  const result = (rawFinalResult ?? null) as ReportGenerationResult | null;

  const markdownPath = typeof result?.markdown === 'string' ? result.markdown : null;
  const markdownContent = typeof result?.markdown_content === 'string' ? result.markdown_content : null;
  const {
    doc,
    loading: mdLoading,
    error: mdError,
    reload: reloadMarkdown,
  } = useReportMarkdown(markdownPath, markdownContent);

  const [mode, setMode] = useState<'confirm' | 'authoring'>('authoring');

  const isDone = !!result;
  const isProcessing = !isDone && !error;
  const hasMarkdown = !!markdownPath || !!markdownContent;

  // 结果就绪但无 Markdown 产物（如一致性阻断）时，默认切到「资料确认」。
  useEffect(() => {
    if (result && !hasMarkdown) setMode('confirm');
  }, [result, hasMarkdown]);

  return (
    <Layout>
      <div className="ef-page flex h-full flex-col">
        {/* 顶部阶段导航：可研成果交付工作台 */}
        <header className="ef-head">
          <div className="min-w-0">
            <h1 className="ef-head-title">可研成果交付</h1>
            <p className="ef-head-sub">核对报告、工程附图附表与专项支撑成果的编制状态，并协同完善可研报告。</p>
          </div>
          <div className="ef-head-actions">
            <div className="ef-mode-switch" role="tablist" aria-label="可研成果工作台">
              <button
                type="button"
                role="tab"
                aria-selected={mode === 'confirm'}
                className={`ef-mode-btn ${mode === 'confirm' ? 'active' : ''}`}
                onClick={() => setMode('confirm')}
              >
                资料确认
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={mode === 'authoring'}
                className={`ef-mode-btn ${mode === 'authoring' ? 'active' : ''}`}
                onClick={() => setMode('authoring')}
              >
                报告编制
              </button>
            </div>
            <span className="ef-status-badge">可研资料交付</span>
          </div>
        </header>

        <div className="flex min-h-0 flex-1 flex-col overflow-hidden p-3">
          {isProcessing && (
            <div className="flex h-full items-center justify-center p-4">
              <div className="ef-card h-full w-full max-w-2xl overflow-hidden">
                <TaskProgress />
              </div>
            </div>
          )}
          {error && !result && (
            <ErrorBanner error={error} onRetry={() => window.location.reload()} />
          )}
          {result && (mode === 'authoring' ? (
            <ReportAuthoring
              result={result}
              doc={doc}
              loading={mdLoading}
              mdError={mdError}
              reload={reloadMarkdown}
            />
          ) : (
            <GenerationSummary result={result} />
          ))}
        </div>

      </div>
      <JsonDrawer data={result} disabled={isProcessing} />
    </Layout>
  );
}

/** 报告编制：左（可研成果）/ 中（目录，快速定位）/ 右（连续完整正文，可编辑）。 */
function ReportAuthoring({
  result,
  doc,
  loading,
  mdError,
  reload,
}: {
  result: ReportGenerationResult;
  doc: ReportDoc | null;
  loading: boolean;
  mdError: string | null;
  reload: () => void;
}) {
  const [activeId, setActiveId] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [filesCollapsed, setFilesCollapsed] = useState(false);
  const [outlineCollapsed, setOutlineCollapsed] = useState(false);
  const [dirty, setDirty] = useState(false);

  const editorRef = useRef<HTMLDivElement>(null);

  // 正文渲染为一段连续完整文档；loading / 错误 / 空态直接写入编辑器
  useEffect(() => {
    const el = editorRef.current;
    if (!el) return;

    if (loading) {
      el.innerHTML = '';
      const p = document.createElement('p');
      p.className = 'md-empty';
      p.textContent = '正在加载正文…';
      el.appendChild(p);
      return;
    }
    if (mdError) {
      el.innerHTML = '';
      const p = document.createElement('p');
      p.className = 'md-error';
      p.textContent = mdError;
      el.appendChild(p);
      return;
    }
    if (!doc || doc.chapters.length === 0) {
      el.innerHTML = '';
      const p = document.createElement('p');
      p.className = 'md-empty';
      p.textContent = '请稍候…';
      el.appendChild(p);
      return;
    }

    el.innerHTML = renderFullDocument(doc);
    setActiveId(doc.chapters[0].id);
    setExpanded((e) => {
      if (Object.keys(e).length > 0) return e;
      const next: Record<string, boolean> = {};
      doc.chapters.forEach((c) => {
        next[c.id] = true;
      });
      return next;
    });
    setDirty(false);
  }, [doc, loading, mdError]);

  const statusText = reportStatusText(result.status);

  function scrollTo(id: string) {
    setActiveId(id);
    const el = editorRef.current?.querySelector<HTMLElement>(`[id="${id}"]`);
    el?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function toggleChapter(chapterId: string) {
    setExpanded((e) => ({ ...e, [chapterId]: !e[chapterId] }));
  }

  // 滚动时高亮当前所处章节（目录仅用于快速定位，不切换正文）
  function handleEditorScroll() {
    const el = editorRef.current;
    if (!el) return;
    const containerTop = el.getBoundingClientRect().top;
    let current: string | null = null;
    el.querySelectorAll<HTMLElement>('h1[id],h2[id]').forEach((h) => {
      if (h.getBoundingClientRect().top <= containerTop + 80) current = h.id;
    });
    setActiveId(current);
  }

  return (
    <section className="ef-report-workspace">
      <header className="ef-report-header">
        <div>
          <h2>{doc?.title ?? '可行性研究报告'}</h2>
          <p>正文为连续完整文档，可垂直滚动浏览并直接编辑；目录用于快速定位。</p>
        </div>
      </header>

      <div
        className={`ef-report-grid ${filesCollapsed ? 'collapse-files' : ''} ${outlineCollapsed ? 'collapse-outline' : ''}`}
      >
        {/* 左：可研成果 */}
        <aside className={`ef-package-files ${filesCollapsed ? 'is-collapsed' : ''}`}>
          {filesCollapsed ? (
            <button
              type="button"
              className="ef-collapse-rail"
              onClick={() => setFilesCollapsed(false)}
              aria-label="展开可研成果"
            >
              <ChevronRight />
            </button>
          ) : (
            <>
              <div className="ef-column-title">
                <strong>可研成果</strong>
                <button
                  type="button"
                  className="ef-collapse-button"
                  onClick={() => setFilesCollapsed(true)}
                  aria-label="折叠可研成果"
                >
                  <ChevronDown />
                </button>
              </div>
              <span className="ef-column-subtitle">1 项成果 · 主成果</span>
              <div className="ef-attachment-list">
                <section className="ef-deliverable-group">
                  <h3>主成果</h3>
                  <button type="button" className="ef-file-row active generated">
                    <FileIcon />
                    <span>可行性研究报告</span>
                    <b>{statusText}</b>
                  </button>
                </section>
              </div>
            </>
          )}
        </aside>

        {/* 中：可研报告目录 */}
        <aside className={`ef-report-outline ${outlineCollapsed ? 'is-collapsed' : ''}`}>
          {outlineCollapsed ? (
            <button
              type="button"
              className="ef-collapse-rail"
              onClick={() => setOutlineCollapsed(false)}
              aria-label="展开可研报告目录"
            >
              <ChevronRight />
            </button>
          ) : (
            <>
              <div className="ef-column-title">
                <strong>可研报告目录</strong>
                <button
                  type="button"
                  className="ef-collapse-button"
                  onClick={() => setOutlineCollapsed(true)}
                  aria-label="折叠可研报告目录"
                >
                  <ChevronDown />
                </button>
              </div>
              {loading && <p className="ef-md-empty">正在加载目录…</p>}
              {mdError && (
                <div className="ef-md-error-box">
                  <p className="ef-md-error">{mdError}</p>
                  <button type="button" className="ef-retry-btn" onClick={reload}>
                    重试读取
                  </button>
                </div>
              )}
              {doc && (
                <div className="ef-outline-tree">
                  {doc.chapters.map((chapter) => {
                    const isExpanded = !!expanded[chapter.id];
                    const hasChildren = chapter.sections.length > 0;
                    return (
                      <section key={chapter.id} className={isExpanded ? 'expanded' : ''}>
                        <button
                          type="button"
                          className={`ef-outline-chapter ${activeId === chapter.id ? 'active' : ''}`}
                          onClick={() => {
                            if (hasChildren) toggleChapter(chapter.id);
                            else scrollTo(chapter.id);
                          }}
                        >
                          <span>{chapter.text}</span>
                          {hasChildren ? <ChevronDown /> : null}
                        </button>
                        {isExpanded && hasChildren && (
                          <div className="ef-outline-subsections">
                            {chapter.sections.map((section) => (
                              <button
                                key={section.id}
                                type="button"
                                className={activeId === section.id ? 'active' : ''}
                                onClick={() => scrollTo(section.id)}
                              >
                                {section.text}
                              </button>
                            ))}
                          </div>
                        )}
                      </section>
                    );
                  })}
                </div>
              )}
            </>
          )}
        </aside>

        {/* 右：正文展示（连续完整 + 可编辑） */}
        <main className="ef-report-editor">
          <header>
            <div>
              <span>正文展示区 · 可行性研究报告_初稿.md</span>
              <h3>{doc?.title ?? '可行性研究报告'}</h3>
            </div>
            <div className="ef-editor-badges">
              {dirty && <em className="is-edited">已编辑</em>}
              <em>{statusText}</em>
            </div>
          </header>
          <div
            ref={editorRef}
            className="ef-md ef-md-editable thin-scroll"
            contentEditable
            suppressContentEditableWarning
            onInput={() => setDirty(true)}
            onScroll={handleEditorScroll}
          />
        </main>
      </div>
    </section>
  );
}

/** 资料确认：生成结果摘要（保留原报告生成页的核心信息）。 */
function GenerationSummary({ result }: { result: ReportGenerationResult }) {
  const status = result.status ?? '';
  const statusMeta = STATUS_META[status];
  const artifacts = useMemo(
    () => ARTIFACT_FIELDS.filter((f) => typeof result[f.key] === 'string' && result[f.key]),
    [result],
  );

  return (
    <div className="thin-scroll flex h-full flex-col gap-3 overflow-y-auto p-1">
      <SectionCard title="生成结果" accent={statusMeta?.color ?? 'accent'}>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <InfoCell label="状态" value={statusMeta?.label ?? (status || '-')} highlight={statusMeta?.color} />
          <InfoCell
            label="完成情况"
            value={
              result.completion_status
                ? COMPLETION_LABEL[result.completion_status] ?? result.completion_status
                : '-'
            }
            highlight={result.completion_status === 'generated_with_blocked_sections' ? 'orange' : 'green'}
          />
          <InfoCell
            label="阻断章节数"
            value={result.blocked_section_count ?? result.blocked_sections?.length ?? 0}
            highlight={result.blocked_section_count ? 'orange' : 'green'}
          />
          <InfoCell label="exit_code" value={result.exit_code ?? '-'} />
        </div>
      </SectionCard>

      {(result.markdown || result.docx) && (
        <SectionCard title="报告文件" accent="green">
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {result.markdown && (
              <div className="rounded-lg bg-bg px-3 py-2">
                <div className="text-[10px] uppercase tracking-wider text-text-3">Markdown</div>
                <div className="mt-0.5 truncate font-mono text-xs text-text" title={result.markdown}>
                  {result.markdown}
                </div>
              </div>
            )}
            {result.docx && (
              <div className="rounded-lg bg-bg px-3 py-2">
                <div className="text-[10px] uppercase tracking-wider text-text-3">DOCX</div>
                <div className="mt-0.5 truncate font-mono text-xs text-text" title={result.docx}>
                  {result.docx}
                </div>
              </div>
            )}
          </div>
        </SectionCard>
      )}

      {status === 'consistency_blocked' && result.issues && result.issues.length > 0 && (
        <SectionCard title={`一致性检查问题（${result.issues.length}）`} accent="red">
          <div className="flex flex-col gap-1.5">
            {result.issues.map((issue, i) => (
              <div key={i} className="flex items-start gap-2 rounded-lg bg-bg px-3 py-2 text-xs">
                {issue.severity && (
                  <span
                    className={`mt-0.5 shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                      issue.severity === 'high' ? 'bg-red/10 text-red' : 'bg-orange/10 text-orange'
                    }`}
                  >
                    {issue.severity}
                  </span>
                )}
                <div className="min-w-0 flex-1">
                  <div className="text-text">{issue.message ?? '-'}</div>
                  {(issue.code || issue.section_id) && (
                    <div className="mt-0.5 font-mono text-[10px] text-text-3">
                      {[issue.code, issue.section_id].filter(Boolean).join(' · ')}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </SectionCard>
      )}

      {result.blocked_sections && result.blocked_sections.length > 0 && (
        <SectionCard title={`阻断章节（${result.blocked_sections.length}）`} accent="orange">
          <div className="flex flex-col gap-1.5">
            {result.blocked_sections.map((b, i) => (
              <div key={i} className="flex items-start gap-2 rounded-lg bg-bg px-3 py-2 text-xs">
                <span className="mt-0.5 font-mono text-text-2">{b.section_id ?? b.field ?? '-'}</span>
                <div className="min-w-0 flex-1">
                  <div className="text-text">{b.reason ?? '-'}</div>
                  {b.affected_sections && b.affected_sections.length > 0 && (
                    <div className="mt-0.5 font-mono text-[10px] text-text-3">
                      影响章节：{b.affected_sections.join('、')}
                    </div>
                  )}
                </div>
                {b.recommended_action && (
                  <span className="shrink-0 rounded bg-bg-3 px-1.5 py-0.5 font-mono text-[10px] text-text-3">
                    {b.recommended_action}
                  </span>
                )}
              </div>
            ))}
          </div>
        </SectionCard>
      )}

      {artifacts.length > 0 && (
        <SectionCard title={`产物（${artifacts.length}）`}>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {artifacts.map((f) => (
              <div key={f.key} className="rounded-lg bg-bg px-3 py-2">
                <div className="text-[10px] uppercase tracking-wider text-text-3">{f.label}</div>
                <div className="mt-0.5 truncate font-mono text-xs text-text" title={String(result[f.key])}>
                  {String(result[f.key])}
                </div>
              </div>
            ))}
          </div>
        </SectionCard>
      )}
    </div>
  );
}

function ChevronDown() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M6 9l6 6 6-6" />
    </svg>
  );
}

function ChevronRight() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 18l6-6-6-6" />
    </svg>
  );
}

function FileIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
      <path d="M14 2v6h6M8 13h8M8 17h6" />
    </svg>
  );
}
