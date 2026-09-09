/**
 * engineering_facts 只读结果页（保留原工程确认页布局）。
 *
 * 页面保留原工程确认 UI 的视觉和布局：顶部资料确认/报告编制 header、
 * 上方 PFD/流程图占位、下方采用方案/投资概算/设备清单/改造点四卡片。
 * 数据契约改为 engineering_facts structuredContent，不提供审核提交入口。
 */

import { useMemo, useState } from 'react';
import { Layout } from '@/components/Layout';
import { ErrorBanner } from '@/components/ErrorBanner';
import { TaskProgress } from '@/components/TaskProgress';
import { JsonDrawer } from '@/components/JsonDrawer';
import { useMcpApp, useNormalizedToolResult } from '@/core/mcpApp';

interface Diagnostic {
  level?: string;
  code?: string;
  message?: string;
}
interface EconomicMetric {
  value?: number | string | null;
  unit?: string;
  basis?: string;
  status?: string;
  source_status?: string;
}
interface EquipmentSummaryItem {
  equipment_id?: string;
  id?: string;
  tag?: string;
  name?: string;
  equipment_type?: string;
  type?: string;
  retrofit_status?: string;
  retrofit_required?: boolean | null;
  action?: string;
  retrofit_description?: string;
  source?: string;
  evaluation_status?: string;
  is_new_reactor?: boolean;
  retrofit?: Record<string, unknown>;
  conclusion?: string;
}
interface RetrofitPoint {
  point_id?: string;
  type?: string;
  description?: string;
  related_equipment_ids?: string[];
  source?: string;
}

interface EngineeringFactsResult {
  status?: string;
  artifact?: {
    uri?: string;
    schema_version?: string;
    media_type?: string;
  } | null;
  engineering_facts?: Record<string, unknown>;
  summary?: Record<string, unknown>;
  diagnostics?: Diagnostic[];
}

const ECONOMIC_METRICS = [
  { key: 'total_investment', label: '总投资' },
  { key: 'average_annual_profit', label: '年均利润' },
  { key: 'static_payback_period', label: '投资回收期' },
  { key: 'after_tax_irr', label: '内部收益率' },
] as const;

const SCHEME_SECTION_MARKERS = [
  { key: 'position', label: '位置', marker: '位置：' },
  { key: 'unitRole', label: '单元作用', marker: '单元作用：' },
  { key: 'mechanism', label: '机理', marker: '机理：' },
] as const;

const RETROFIT_POINT_TYPE_LABEL: Record<string, string> = {
  reuse: '利旧',
  addition: '新增/扩容',
  parallel: '并联',
  series: '串联',
  heat_transfer: '换热改造',
  equipment_retrofit: '设备改造',
  topology_change: '拓扑变更',
};

const SOURCE_LABEL: Record<string, string> = {
  EA: '设备专业',
  PA: '工艺专业',
};

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asArray(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? (value.filter((item) => item && typeof item === 'object') as Record<string, unknown>[])
    : [];
}

function text(value: unknown, fallback = '-'): string {
  if (value === null || value === undefined || value === '') return fallback;
  return String(value);
}

function parseSchemeDescription(desc: string): Array<{ key: string; label: string; text: string }> {
  if (!desc) return [];
  const starts = SCHEME_SECTION_MARKERS.map((m) => ({ ...m, idx: desc.indexOf(m.marker) }))
    .filter((m) => m.idx >= 0)
    .sort((a, b) => a.idx - b.idx);
  if (starts.length === 0) return [];
  return starts.map((m, i) => {
    const start = m.idx + m.marker.length;
    const end = i + 1 < starts.length ? starts[i + 1].idx : desc.length;
    return { key: m.key, label: m.label, text: desc.slice(start, end).trim() };
  });
}

function metricText(metric: unknown): string {
  const item = asRecord(metric) as EconomicMetric;
  const value = item.value;
  if (value === null || value === undefined || value === '') return '待计算';
  return `${value}${item.unit ? ` ${item.unit}` : ''}`;
}

function economicSource(facts: Record<string, unknown>, key: string): unknown {
  const candidates = [
    asRecord(asRecord(facts.economic_summary)[key]),
    asRecord(asRecord(asRecord(facts.derived_facts).economic_summary)[key]),
    asRecord(asRecord(asRecord(facts.basic_info).economic_summary)[key]),
  ];
  return candidates.find((item) => Object.keys(item).length > 0);
}

function displayRetrofitStatus(status: string | undefined): string {
  if (!status || status === '当前未识别改造要求') return '无变化';
  return status;
}

function retrofitStatusClass(status: string | undefined): string {
  const s = displayRetrofitStatus(status);
  if (s.startsWith('无变化') || s.startsWith('不改造')) return 'ef-muted';
  if (s.includes('新增')) return 'ef-green';
  if (s.includes('改造') || s.includes('扩容')) return 'ef-orange';
  if (s.includes('利旧')) return 'ef-blue';
  return 'ef-muted';
}

function equipmentStatusOrder(status: string | undefined): number {
  const s = displayRetrofitStatus(status);
  if (s.startsWith('无变化') || s.startsWith('不改造')) return 3;
  if (s.includes('新增')) return 0;
  if (s.includes('改造') || s.includes('扩容')) return 1;
  if (s.includes('利旧')) return 2;
  return 4;
}

function retrofitPointTypeLabel(type: string | undefined): string {
  return RETROFIT_POINT_TYPE_LABEL[type ?? ''] ?? type ?? '';
}

function sourceLabel(source: string | undefined): string {
  return SOURCE_LABEL[source ?? ''] ?? source ?? '';
}

function selectedActionMap(facts: Record<string, unknown>): Map<string, { status: string; type: string; description: string }> {
  const selected = asRecord(asRecord(asRecord(facts.equipment).reactor).selected_scheme);
  const map = new Map<string, { status: string; type: string; description: string }>();
  const groups: Array<{ key: string; status: string; type: string }> = [
    { key: 'reuse', status: '利旧', type: 'reuse' },
    { key: 'addition', status: '改造', type: 'addition' },
    { key: 'parallel', status: '新增', type: 'parallel' },
    { key: 'series', status: '新增', type: 'series' },
    { key: 'heat_transfer', status: '改造', type: 'heat_transfer' },
  ];

  groups.forEach(({ key, status, type }) => {
    asArray(selected[key]).forEach((item) => {
      const id = text(item.id ?? item.equipment_id, '');
      if (!id) return;
      const schemeType = text(item.scheme_type, '');
      const displayStatus = schemeType === 'new_reactor' ? '新增' : status;
      map.set(id, {
        status: displayStatus,
        type,
        description: text(item.description, `${text(item.name, `设备 ${id}`)}：${displayStatus}`),
      });
    });
  });

  return map;
}

function collectEquipment(facts: Record<string, unknown>, actions: Map<string, { status: string }>): EquipmentSummaryItem[] {
  const equipment = asRecord(facts.equipment);
  const objectCatalog = asArray(equipment.object_catalog) as EquipmentSummaryItem[];
  const source = objectCatalog.length > 0
    ? objectCatalog
    : ([
        ...asArray(asRecord(equipment.reactor).evaluations).map((item) => ({ ...item, equipment_type: 'reactor' })),
        ...asArray(asRecord(equipment.tower).evaluations).map((item) => ({ ...item, equipment_type: 'tower' })),
      ] as EquipmentSummaryItem[]);

  return source.map((item) => {
    const id = text(item.equipment_id ?? item.id, '');
    const action = id ? actions.get(id) : undefined;
    const retrofit = asRecord(item.retrofit);
    return {
      ...item,
      equipment_id: id || undefined,
      retrofit_status: text(
        item.retrofit_status ?? action?.status ?? item.action ?? retrofit.route ?? item.evaluation_status,
        item.is_new_reactor ? '新增' : '无变化',
      ),
    };
  });
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

function collectRetrofitPoints(
  equipment: EquipmentSummaryItem[],
  actions: Map<string, { status: string; type: string; description: string }>,
): RetrofitPoint[] {
  const points: RetrofitPoint[] = [];
  actions.forEach((action, id) => {
    points.push({
      point_id: `RP-${String(points.length + 1).padStart(3, '0')}`,
      type: action.type,
      description: action.description,
      related_equipment_ids: [id],
      source: 'EA',
    });
  });

  equipment.forEach((item) => {
    const id = text(item.equipment_id ?? item.id, '');
    if (!id || actions.has(id)) return;
    const status = displayRetrofitStatus(item.retrofit_status);
    if (status === '无变化') return;
    points.push({
      point_id: `RP-${String(points.length + 1).padStart(3, '0')}`,
      type: text(item.action ?? item.retrofit_status, ''),
      description: text(item.retrofit_description ?? item.conclusion ?? `${text(item.name, id)}：${status}`),
      related_equipment_ids: [id],
      source: text(item.source, 'EA'),
    });
  });

  return points;
}

function adoptedSchemeInfo(facts: Record<string, unknown>): { name: string; description: string; sections: Array<{ key: string; label: string; text: string }> } {
  const adopted = asRecord(facts.adopted_scheme);
  const schemes = asArray(adopted.schemes);
  const selectedNames = Array.isArray(adopted.selected_names) ? adopted.selected_names.map(String) : [];
  const firstScheme = schemes[0] ?? {};
  const name = text(firstScheme.name ?? firstScheme.scheme_name ?? selectedNames[0], '未形成采用方案');
  const description = text(firstScheme.description ?? firstScheme.summary ?? firstScheme.conclusion, '');
  return { name, description, sections: parseSchemeDescription(description) };
}

function badgeStyleFor(status: string | undefined): { color: string; background: string; borderColor: string } {
  if (status === 'completed') return { color: '#15803d', background: '#dcfce7', borderColor: 'rgba(22, 163, 74, 0.3)' };
  if (status === 'failed') return { color: '#b91c1c', background: '#fee2e2', borderColor: 'rgba(220, 38, 38, 0.3)' };
  return { color: '#1d4ed8', background: '#dbeafe', borderColor: 'rgba(37, 99, 235, 0.25)' };
}

function badgeText(status: string | undefined): string {
  if (status === 'completed') return '已整理';
  if (status === 'failed') return '整理失败';
  return '整理中';
}

export function EngineeringFactsPage() {
  const app = useMcpApp();
  const { finalResult: rawFinalResult, isError } = useNormalizedToolResult();
  const error = isError ? app.error : null;
  const result = (rawFinalResult ?? null) as EngineeringFactsResult | null;
  const facts = asRecord(result?.engineering_facts);

  const actions = useMemo(() => selectedActionMap(facts), [facts]);
  const equipmentSummary = useMemo(() => sortedEquipmentSummary(collectEquipment(facts, actions)), [facts, actions]);
  const retrofitPoints = useMemo(() => collectRetrofitPoints(equipmentSummary, actions), [equipmentSummary, actions]);
  const scheme = useMemo(() => adoptedSchemeInfo(facts), [facts]);

  const [drawingView, setDrawingView] = useState<'pfd' | 'flow'>('pfd');
  const [activeMode, setActiveMode] = useState<'confirm' | 'authoring'>('confirm');
  const isDone = !!result;
  const isProcessing = !isDone && !error;

  return (
    <Layout>
      <div className="ef-page flex h-full flex-col">
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
              {error && !result && <ErrorBanner error={error} onRetry={() => window.location.reload()} />}
              {result && (
                <div className="flex h-full flex-col gap-3 overflow-hidden">
                  {result.status === 'completed' && Object.keys(facts).length === 0 && (
                    <div
                      className="shrink-0 border px-3 py-2 text-xs"
                      style={{
                        borderColor: 'rgba(245, 158, 11, 0.35)',
                        background: 'rgba(254, 243, 199, 0.6)',
                        color: '#92400e',
                        borderRadius: 6,
                      }}
                    >
                      已收到完成信封，但宿主未透传 <code>_meta.ui_payload.engineering_facts</code>
                      ，完整工程事实未到达 UI（工具产物已保存在 <code>{result.artifact?.uri ?? '宿主存储'}</code>）。
                      请检查宿主在推送 tool-result 时是否原样透传 MCP 响应的 <code>_meta</code> 字段。
                    </div>
                  )}
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
                          {scheme.name}
                        </strong>
                        <span className="block text-xs" style={{ color: '#64748b' }}>
                          采用方案对应流程范围
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

                  <section className="grid min-h-0 flex-[3.3] grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
                    <article className="ef-card flex min-h-0 flex-col overflow-hidden p-3.5">
                      <h3 className="ef-card-title">采用方案</h3>
                      <div className="text-[15px] font-bold leading-snug" style={{ color: '#112b4f' }}>
                        {scheme.name}
                      </div>
                      <div className="thin-scroll mt-2 flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto">
                        {scheme.sections.length > 0 ? (
                          scheme.sections.map((section) => (
                            <div key={section.key} className="ef-cell">
                              <span className="ef-cell-label">{section.label}</span>
                              <span className="text-xs" style={{ color: '#183456', lineHeight: 1.55 }}>
                                {section.text}
                              </span>
                            </div>
                          ))
                        ) : (
                          <p className="text-xs" style={{ color: '#64748b' }}>
                            {scheme.description || 'engineering_facts.json 未形成 adopted_scheme 描述'}
                          </p>
                        )}
                      </div>
                    </article>

                    <article className="ef-card flex min-h-0 flex-col overflow-hidden p-3.5">
                      <h3 className="ef-card-title">投资概算</h3>
                      <div className="thin-scroll flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto">
                        {ECONOMIC_METRICS.map(({ key, label }) => {
                          const metric = economicSource(facts, key);
                          const pending = metricText(metric) === '待计算';
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

                    <article className="ef-card flex min-h-0 flex-col overflow-hidden p-3.5">
                      <h3 className="ef-card-title">设备清单（{equipmentSummary.length}）</h3>
                      <div className="thin-scroll flex min-h-0 flex-1 flex-col gap-1.5 overflow-y-auto">
                        {equipmentSummary.length === 0 && <p className="text-xs" style={{ color: '#64748b' }}>未识别到设备清单</p>}
                        {equipmentSummary.slice(0, 80).map((item, i) => (
                          <div
                            key={item.equipment_id ?? item.id ?? i}
                            className="flex items-center gap-2 rounded-lg px-2 py-1.5"
                            style={{
                              background: 'rgba(255, 255, 255, 0.72)',
                              border: '1px solid rgba(37, 99, 235, 0.12)',
                            }}
                          >
                            <span className="ef-id-badge">{item.equipment_id ?? item.id ?? '-'}</span>
                            <span className="min-w-0 flex-1 truncate text-xs" style={{ color: '#183456' }}>
                              {[item.tag, item.name].filter(Boolean).join(' ') || '-'}
                            </span>
                            <span className={`text-xs font-medium ${retrofitStatusClass(item.retrofit_status)}`}>
                              {displayRetrofitStatus(item.retrofit_status)}
                            </span>
                          </div>
                        ))}
                      </div>
                    </article>

                    <article className="ef-card flex min-h-0 flex-col overflow-hidden p-3.5">
                      <h3 className="ef-card-title">改造点（{retrofitPoints.length}）</h3>
                      <div className="thin-scroll flex min-h-0 flex-1 flex-col gap-1.5 overflow-y-auto">
                        {retrofitPoints.length === 0 && <p className="text-xs" style={{ color: '#64748b' }}>未识别到改造点</p>}
                        {retrofitPoints.map((point, i) => (
                          <div
                            key={point.point_id ?? i}
                            className="flex items-start gap-2.5 rounded-lg px-2 py-1.5"
                            style={{
                              background: 'rgba(255, 255, 255, 0.72)',
                              border: '1px solid rgba(37, 99, 235, 0.12)',
                            }}
                          >
                            <span className="ef-num-dot">{String(i + 1).padStart(2, '0')}</span>
                            <div className="min-w-0 flex-1">
                              <div className="text-xs font-semibold" style={{ color: '#183456', lineHeight: 1.5 }}>
                                {point.description ?? '-'}
                              </div>
                              <div className="text-[10px]" style={{ color: '#64748b' }}>
                                {[retrofitPointTypeLabel(point.type), sourceLabel(point.source)].filter(Boolean).join(' · ')}
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
function DrawingPlaceholder({ kind }: { kind: 'pfd' | 'flow' }) {
  return (
    <div
      className="flex h-full w-full flex-col items-center justify-center gap-2 rounded-lg border border-dashed"
      style={{ borderColor: '#dbe7fb', background: '#fff' }}
    >
      <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" style={{ color: '#94a3b8' }}>
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
function AuthoringPlaceholder() {
  return (
    <div className="flex h-full items-center justify-center p-4">
      <div className="ef-card flex flex-col items-center gap-3 rounded-lg px-10 py-12">
        <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" style={{ color: '#94a3b8' }}>
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <path d="M14 2v6h6M8 13h8M8 17h6" />
        </svg>
        <div className="text-sm font-bold" style={{ color: '#112b4f' }}>报告编制</div>
        <div className="text-xs" style={{ color: '#64748b' }}>报告编制由 report_generation Tool 负责。</div>
      </div>
    </div>
  );
}
