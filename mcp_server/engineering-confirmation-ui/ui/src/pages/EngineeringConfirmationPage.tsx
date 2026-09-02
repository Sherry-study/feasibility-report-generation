/**
 * 阶段②关键事实确认页面。
 *
 * 对应 MCP 工具 ``engineering_confirmation``。执行流程：
 *   年化计算并 merge 回写 -> 生成关键事实确认单 -> 人工确认门槛。
 * 结果就绪后，按与 engineering_facts 完全一致的「浅蓝白工业软件」布局展示
 * report_confirmation 的核心内容：
 *   外层：可研成果交付（资料确认 / 报告编制）
 *   左上：方案 PFD / 流程图（占位）
 *   左下：推荐方案 / 投资概算 / 设备清单 / 改造点 四卡片。
 *
 * 视觉：对齐 engineering-facts-ui 的 .ef-* 浅蓝白工业软件风格（见 index.css）。
 *
 * 数据来源：工具返回 structuredContent 中的 ``confirmation``（镜像
 * report_output_dir/report_confirmation.json 真实字段，不做重命名）。
 */

import { useState } from 'react';
import { Layout } from '@/components/Layout';
import { ErrorBanner } from '@/components/ErrorBanner';
import { TaskProgress } from '@/components/TaskProgress';
import { JsonDrawer } from '@/components/JsonDrawer';
import { useMcpApp, useNormalizedToolResult } from '@/core/mcpApp';

/** 经济指标（economic_summary 单项）。 */
interface EconomicMetric {
  value?: number | string | null;
  unit?: string;
  basis?: string;
  status?: string;
  source_status?: string;
}

/** 待确认项目信息（pending_user_inputs 单项）。 */
interface PendingUserInput {
  field?: string;
  label?: string;
  current?: string;
  how_to_supply?: string;
}

/** 设备清单（equipment_summary 单项）。 */
interface EquipmentSummaryItem {
  equipment_id?: string;
  tag?: string;
  name?: string;
  equipment_type?: string;
  retrofit_status?: string;
  retrofit_required?: boolean | null;
  action?: string;
  retrofit_description?: string;
  source?: string;
}

/** 改造点（retrofit_points 单项）。 */
interface RetrofitPoint {
  point_id?: string;
  type?: string;
  description?: string;
  related_equipment_ids?: string[];
  source?: string;
}

/** report_confirmation 的核心内容。 */
interface ConfirmationPayload {
  contract_version?: string;
  project_id?: string | null;
  project_name?: string | null;
  confirmation_status?: string;
  recommended_scheme?: {
    scheme_id?: string | null;
    name?: string | null;
    description?: string | null;
    status?: string;
  };
  economic_summary?: {
    total_investment?: EconomicMetric;
    average_annual_profit?: EconomicMetric;
    static_payback_period?: EconomicMetric;
    after_tax_irr?: EconomicMetric;
  };
  pending_user_inputs?: PendingUserInput[];
  equipment_summary?: EquipmentSummaryItem[];
  retrofit_points?: RetrofitPoint[];
  confirmed_at?: string;
  confirmed_by?: string;
  baseline_hash?: string;
  confirmation_note?: string;
}

/** engineering_confirmation 返回结构（structuredContent）。 */
interface EngineeringConfirmationResult {
  exit_code?: number;
  status?: string;
  facts?: string;
  annualization?: string;
  energy_conversion?: string;
  report_confirmation?: string;
  report_confirmation_markdown?: string;
  report_confirmation_schema?: string;
  confirmation_response_schema?: string;
  confirmation_response?: string;
  confirmed_facts?: string;
  confirmed_report_confirmation?: string;
  error?: string;
  next_action?: string;
  confirmation?: ConfirmationPayload;
}

/** 设备改造状态 -> 文字色（对齐 engineering-facts-ui 的状态语义色）。 */
function retrofitStatusClass(status: string | undefined): string {
  const s = displayRetrofitStatus(status);
  if (s.startsWith('无变化') || s.startsWith('不改造')) return 'ef-muted';
  if (s.includes('新增')) return 'ef-green';
  if (s.includes('改造')) return 'ef-orange';
  if (s.includes('利旧')) return 'ef-blue';
  return 'ef-muted';
}

function displayRetrofitStatus(status: string | undefined): string {
  if (!status || status === '当前未识别改造要求') return '无变化';
  return status;
}

function equipmentStatusOrder(status: string | undefined): number {
  const s = displayRetrofitStatus(status);
  if (s.startsWith('无变化') || s.startsWith('不改造')) return 3;
  if (s.includes('新增')) return 0;
  if (s.includes('改造')) return 1;
  if (s.includes('利旧')) return 2;
  return 4;
}

function sortedEquipmentSummary(items: EquipmentSummaryItem[]): EquipmentSummaryItem[] {
  return items
    .map((item, index) => ({ item, index }))
    .sort((a, b) => {
      const statusDiff = equipmentStatusOrder(a.item.retrofit_status) - equipmentStatusOrder(b.item.retrofit_status);
      return statusDiff || a.index - b.index;
    })
    .map(({ item }) => item);
}

/** 改造点类型 -> 中文。 */
const RETROFIT_POINT_TYPE_LABEL: Record<string, string> = {
  reuse: '利旧',
  parallel: '并联',
  series: '串联',
  equipment_retrofit: '设备改造',
  topology_change: '拓扑变更',
};

/** 来源专业 -> 中文。 */
const SOURCE_LABEL: Record<string, string> = {
  EA: '设备专业',
  PA: '工艺专业',
};

function retrofitPointTypeLabel(type: string | undefined): string {
  return RETROFIT_POINT_TYPE_LABEL[type ?? ''] ?? type ?? '';
}

function sourceLabel(source: string | undefined): string {
  return SOURCE_LABEL[source ?? ''] ?? source ?? '';
}

/** 经济指标展示顺序。 */
const ECONOMIC_METRICS = [
  { key: 'total_investment', label: '总投资' },
  { key: 'average_annual_profit', label: '年均利润' },
  { key: 'static_payback_period', label: '投资回收期' },
  { key: 'after_tax_irr', label: '内部收益率' },
] as const;

/** 把方案描述按「位置 / 单元作用 / 机理」拆成三行。 */
const SCHEME_SECTION_MARKERS = [
  { key: 'position', label: '位置', marker: '位置：' },
  { key: 'unitRole', label: '单元作用', marker: '单元作用：' },
  { key: 'mechanism', label: '机理', marker: '机理：' },
] as const;

function parseSchemeDescription(desc: string): Array<{ key: string; label: string; text: string }> {
  if (!desc) return [];
  const starts = SCHEME_SECTION_MARKERS.map((m) => ({ ...m, idx: desc.indexOf(m.marker) }))
    .filter((m) => m.idx >= 0)
    .sort((a, b) => a.idx - b.idx);
  return starts.map((m, i) => {
    const start = m.idx + m.marker.length;
    const end = i + 1 < starts.length ? starts[i + 1].idx : desc.length;
    return { key: m.key, label: m.label, text: desc.slice(start, end).trim() };
  });
}

/** 经济指标文本：值 + 单位，或「待计算」。 */
function metricText(metric: EconomicMetric | undefined): string {
  const value = metric?.value;
  if (value === null || value === undefined) return '待计算';
  const unit = metric?.unit ? ` ${metric.unit}` : '';
  return `${value}${unit}`;
}

/** 状态徽标样式（按确认状态切换颜色，外形对齐 .ef-status-badge）。 */
function badgeStyleFor(status: string | undefined): { color: string; background: string; borderColor: string } {
  if (status === 'confirmed') {
    return { color: '#15803d', background: '#dcfce7', borderColor: 'rgba(22, 163, 74, 0.3)' };
  }
  if (status === 'confirmation_invalid') {
    return { color: '#b91c1c', background: '#fee2e2', borderColor: 'rgba(220, 38, 38, 0.3)' };
  }
  return { color: '#b45309', background: '#fef3c7', borderColor: 'rgba(217, 119, 6, 0.3)' };
}

function badgeText(status: string | undefined): string {
  if (status === 'confirmed') return '已确认';
  if (status === 'confirmation_invalid') return '确认无效';
  return '待确认';
}

export function EngineeringConfirmationPage() {
  const app = useMcpApp();
  const { finalResult: rawFinalResult, isError } = useNormalizedToolResult();
  const error = isError ? app.error : null;
  const result = (rawFinalResult ?? null) as EngineeringConfirmationResult | null;

  const payload = result?.confirmation;
  const scheme = payload?.recommended_scheme;
  const schemeName = scheme?.name ?? '关键事实确认';
  const schemeSections = parseSchemeDescription(scheme?.description ?? '');
  const economic = payload?.economic_summary ?? {};
  const equipmentSummary = sortedEquipmentSummary(payload?.equipment_summary ?? []);
  const retrofitPoints = payload?.retrofit_points ?? [];

  const [drawingView, setDrawingView] = useState<'pfd' | 'flow'>('pfd');
  const [activeMode, setActiveMode] = useState<'confirm' | 'authoring'>('confirm');

  const isDone = !!result;
  const isProcessing = !isDone && !error;

  return (
    <Layout>
      <div className="ef-page flex h-full flex-col">
        {/* 外层框架：可研成果交付（资料确认 / 报告编制） */}
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
                aria-selected={activeMode === 'confirm'}
                className={`ef-mode-btn ${activeMode === 'confirm' ? 'active' : ''}`}
                onClick={() => setActiveMode('confirm')}
              >
                资料确认
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={activeMode === 'authoring'}
                className={`ef-mode-btn ${activeMode === 'authoring' ? 'active' : ''}`}
                onClick={() => setActiveMode('authoring')}
              >
                报告编制
              </button>
            </div>
            <span className="ef-status-badge" style={badgeStyleFor(result?.status)}>
              {badgeText(result?.status)}
            </span>
          </div>
        </header>

        <div className="flex-1 overflow-hidden p-3">
          {activeMode === 'authoring' ? (
            <AuthoringPlaceholder />
          ) : (
            <>
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
              {result && (
                <div className="flex h-full flex-col gap-3 overflow-hidden">
                  {/* 左上：方案 PFD / 流程图 */}
                  <section className="ef-panel flex min-h-0 flex-[6] flex-col overflow-hidden">
                    <header
                      className="flex items-center gap-3 border-b px-4 py-2.5"
                      style={{
                        borderColor: 'rgba(15, 23, 42, 0.08)',
                        background: 'linear-gradient(90deg, rgba(255,255,255,0.96), rgba(239,246,255,0.9))',
                      }}
                    >
                      <div className="min-w-0">
                        <strong className="block truncate text-sm font-bold" style={{ color: '#0b1b6e' }}>
                          {schemeName}
                        </strong>
                        <span className="block text-xs" style={{ color: '#64748b' }}>
                          方案对应流程范围
                        </span>
                      </div>
                      <div className="ml-auto">
                        <div className="ef-tabs" role="tablist" aria-label="图纸对比视图切换">
                          {(['pfd', 'flow'] as const).map((view) => (
                            <button
                              key={view}
                              type="button"
                              role="tab"
                              aria-selected={drawingView === view}
                              className={`ef-tab ${drawingView === view ? 'active' : ''}`}
                              onClick={() => setDrawingView(view)}
                            >
                              {view === 'pfd' ? 'PFD图' : '流程图'}
                            </button>
                          ))}
                        </div>
                      </div>
                    </header>
                    <div className="flex min-h-0 flex-1 items-center justify-center p-4">
                      <DrawingPlaceholder kind={drawingView} />
                    </div>
                  </section>

                  {/* 左下：四卡片 */}
                  <section className="grid min-h-0 flex-[3.3] grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
                    {/* 卡片1：推荐方案 */}
                    <article className="ef-card flex min-h-0 flex-col overflow-hidden p-3.5">
                      <h3 className="ef-card-title">推荐方案</h3>
                      <div className="text-[15px] font-bold leading-snug" style={{ color: '#112b4f' }}>
                        {schemeName}
                      </div>
                      <div className="thin-scroll mt-2 flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto">
                        {schemeSections.length > 0 ? (
                          schemeSections.map((section) => (
                            <div key={section.key} className="ef-cell">
                              <span className="ef-cell-label">{section.label}</span>
                              <span className="text-xs" style={{ color: '#183456', lineHeight: 1.55 }}>
                                {section.text}
                              </span>
                            </div>
                          ))
                        ) : (
                          <p className="text-xs" style={{ color: '#64748b' }}>
                            {scheme?.description || '（无方案描述）'}
                          </p>
                        )}
                      </div>
                    </article>

                    {/* 卡片2：投资概算 */}
                    <article className="ef-card flex min-h-0 flex-col overflow-hidden p-3.5">
                      <h3 className="ef-card-title">投资概算</h3>
                      <div className="thin-scroll flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto">
                        {ECONOMIC_METRICS.map(({ key, label }) => {
                          const metric = economic[key];
                          const pending = metric?.value === null || metric?.value === undefined;
                          return (
                            <div key={key} className="ef-cell">
                              <span className="ef-cell-label">{label}</span>
                              <span className={`ef-cell-value ${pending ? 'is-pending' : ''}`}>
                                {metricText(metric)}
                              </span>
                            </div>
                          );
                        })}
                      </div>
                    </article>

                    {/* 卡片3：设备清单 */}
                    <article className="ef-card flex min-h-0 flex-col overflow-hidden p-3.5">
                      <h3 className="ef-card-title">设备清单（{equipmentSummary.length}）</h3>
                      <div className="thin-scroll flex min-h-0 flex-1 flex-col gap-1.5 overflow-y-auto">
                        {equipmentSummary.map((a, i) => (
                          <div
                            key={a.equipment_id ?? i}
                            className="flex items-center gap-2 rounded-lg px-2 py-1.5"
                            style={{
                              background: 'rgba(255, 255, 255, 0.72)',
                              border: '1px solid rgba(37, 99, 235, 0.12)',
                            }}
                          >
                            <span className="ef-id-badge">{a.equipment_id ?? '-'}</span>
                            <span className="min-w-0 flex-1 truncate text-xs" style={{ color: '#183456' }}>
                              {[a.tag, a.name].filter(Boolean).join(' ') || '-'}
                            </span>
                            <span className={`text-xs font-medium ${retrofitStatusClass(a.retrofit_status)}`}>
                              {displayRetrofitStatus(a.retrofit_status)}
                            </span>
                          </div>
                        ))}
                      </div>
                    </article>

                    {/* 卡片4：改造点 */}
                    <article className="ef-card flex min-h-0 flex-col overflow-hidden p-3.5">
                      <h3 className="ef-card-title">改造点（{retrofitPoints.length}）</h3>
                      <div className="thin-scroll flex min-h-0 flex-1 flex-col gap-1.5 overflow-y-auto">
                        {retrofitPoints.map((p, i) => (
                          <div
                            key={p.point_id ?? i}
                            className="flex items-start gap-2.5 rounded-lg px-2 py-1.5"
                            style={{
                              background: 'rgba(255, 255, 255, 0.72)',
                              border: '1px solid rgba(37, 99, 235, 0.12)',
                            }}
                          >
                            <span className="ef-num-dot">{String(i + 1).padStart(2, '0')}</span>
                            <div className="min-w-0 flex-1">
                              <div className="text-xs font-semibold" style={{ color: '#183456', lineHeight: 1.5 }}>
                                {p.description ?? '-'}
                              </div>
                              <div className="text-[10px]" style={{ color: '#64748b' }}>
                                {[retrofitPointTypeLabel(p.type), sourceLabel(p.source)].filter(Boolean).join(' · ')}
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    </article>
                  </section>
                </div>
              )}
            </>
          )}
        </div>

      </div>
      <JsonDrawer data={result} disabled={isProcessing} />
    </Layout>
  );
}

/** PFD / 流程图占位（保留后续接入真实图纸能力）。 */
function DrawingPlaceholder({ kind }: { kind: 'pfd' | 'flow' }) {
  return (
    <div
      className="flex h-full w-full flex-col items-center justify-center gap-2 rounded-lg border border-dashed"
      style={{ borderColor: '#dbe7fb', background: '#fff' }}
    >
      <svg
        width="40"
        height="40"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        style={{ color: '#94a3b8' }}
      >
        <rect x="3" y="3" width="18" height="18" rx="2" />
        <path d="M3 9h18M9 3v18" />
      </svg>
      <div className="text-sm font-semibold" style={{ color: '#112b4f' }}>
        {kind === 'pfd' ? 'PFD 图占位' : '流程图占位'}
      </div>
      <div className="text-[11px]" style={{ color: '#94a3b8' }}>
        待接入真实图纸 / 拓扑渲染
      </div>
    </div>
  );
}

/** 报告编制占位（当前仅实现资料确认，报告生成流程待接入）。 */
function AuthoringPlaceholder() {
  return (
    <div className="flex h-full items-center justify-center p-4">
      <div className="ef-card flex flex-col items-center gap-3 rounded-lg px-10 py-12">
        <svg
          width="40"
          height="40"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.5"
          style={{ color: '#94a3b8' }}
        >
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <path d="M14 2v6h6M8 13h8M8 17h6" />
        </svg>
        <div className="text-sm font-bold" style={{ color: '#112b4f' }}>报告编制</div>
        <div className="text-xs" style={{ color: '#64748b' }}>报告生成流程待接入（当前仅实现「资料确认」）</div>
      </div>
    </div>
  );
}
