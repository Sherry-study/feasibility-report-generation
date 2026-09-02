/**
 * engineering_confirmation 的 mock 数据。
 *
 * 本 host 控制台只服务 engineering_confirmation 一个工具:有进度推送,无审核。
 * 进度步骤镜像 server 中真实的 ctx.report_progress 调用:
 *   1. 年化计算并 merge 回写 (progress=1, total=3)
 *   2. 确认阶段完成 (progress=3, total=3)
 */

export interface UiEvent {
  review_pending?: boolean;
  review_id?: string;
  tool_name?: string;
  final_result?: Record<string, unknown>;
  [k: string]: unknown;
}

export interface ProgressStep {
  progress: number;
  total: number;
  message: string;
  uiEvent?: UiEvent;
}

export interface ReviewStage {
  reviewId: string;
  finalResult: Record<string, unknown>;
  result: Record<string, unknown>;
  resultIsError?: boolean;
}

export interface MockScenario {
  id: string;
  label: string;
  toolName: string;
  args: Record<string, unknown>;
  steps: ProgressStep[];
  review?: ReviewStage;
  /** skipReview=true 时,host 不发 review_pending,直接发 tool-result(progress 工具用)。 */
  skipReview?: boolean;
  /** 错误结果(设置此项时,跳过审核,直接发送错误 tool-result)。 */
  errorResult?: { content: Array<{ type: string; text: string }> };
}

export interface ToolGroup {
  name: string;
  label: string;
  page: string;
  scenarios: MockScenario[];
}

const OUT = 'report_output_dir';

/** 推荐方案描述(与 engineering_facts mock 保持一致,字段镜像真实算法输出)。 */
const SCHEME_DESCRIPTION =
  '位置：催化蒸馏塔B至催化蒸馏塔A的进料管线。单元作用：增设独立反应单元，进行高转化率预反应。机理：在物料进入催化蒸馏塔A前增设新醚化反应器，提前消耗大部分异戊烯，大幅降低进入塔A的反应负荷。预反应与催化蒸馏塔A形成“反应-分离-反应”梯级转化结构，缓解塔内平衡压力，提升系统整体收率。';

/**
 * report_confirmation.json 的核心内容(镜像 build_report_confirmation 输出字段)。
 * status: pending -> 待确认;confirmed -> 已确认。
 */
function confirmationPayload(status: 'pending' | 'confirmed'): Record<string, unknown> {
  const economic =
    status === 'confirmed'
      ? {
          total_investment: { value: 12480.6, unit: '万元', basis: '项目总投资', status: 'available', source_status: 'user_confirmed' },
          average_annual_profit: { value: 2186.3, unit: '万元', basis: '年均利润总额', status: 'available', source_status: 'user_confirmed' },
          static_payback_period: { value: 5.7, unit: '年', basis: '含建设期', status: 'available', source_status: 'user_confirmed' },
          after_tax_irr: { value: 14.8, unit: '%', basis: '税后 IRR', status: 'available', source_status: 'user_confirmed' },
        }
      : {
          total_investment: { value: 12480.6, unit: '万元', basis: '项目总投资', status: 'available', source_status: 'available' },
          average_annual_profit: { value: 2186.3, unit: '万元', basis: '年均利润总额', status: 'available', source_status: 'available' },
          static_payback_period: { value: null, unit: '年', basis: '含建设期', status: 'pending_calculation', source_status: 'not_available' },
          after_tax_irr: { value: null, unit: '%', basis: '税后 IRR', status: 'pending_calculation', source_status: 'not_available' },
        };

  const equipment_summary = [
    { equipment_id: '17', tag: 'R-104', name: '新醚化反应器', equipment_type: 'reactor', retrofit_status: '利旧', retrofit_required: true, action: 'reuse', retrofit_description: '利用已有设备承担新的工程用途', source: 'EA' },
    { equipment_id: '1', tag: 'R-101', name: '醚化反应器', equipment_type: 'reactor', retrofit_status: '改造', retrofit_required: true, action: 'increase_volume', retrofit_description: '原设备本体实施扩容/结构改造', source: 'EA' },
    { equipment_id: '5_1', tag: 'T-101B', name: '甲醇回收塔_并联', equipment_type: 'tower', retrofit_status: '新增', retrofit_required: true, action: 'parallel_new', retrofit_description: '新增并联设备', source: 'EA' },
    { equipment_id: '2_1', tag: 'T-201B', name: '催化蒸馏塔B_并联', equipment_type: 'tower', retrofit_status: '新增', retrofit_required: true, action: 'parallel_new', retrofit_description: '新增并联设备', source: 'EA' },
    { equipment_id: '18', tag: 'T-301B', name: '异戊烯精制塔_串联', equipment_type: 'tower', retrofit_status: '新增', retrofit_required: true, action: 'series_new', retrofit_description: '新增串联设备', source: 'EA' },
    { equipment_id: '6', tag: 'T-102', name: 'TAME精制塔', equipment_type: 'tower', retrofit_status: '无变化', retrofit_required: false, action: 'optimal', retrofit_description: '设备本体不改；仅调整运行参数', source: 'EA' },
  ];

  const retrofit_points = [
    { point_id: 'RP-001', type: 'reuse', description: '新醚化反应器 利旧 现有设备', related_equipment_ids: ['17'], source: 'EA' },
    { point_id: 'RP-002', type: 'equipment_retrofit', description: '醚化反应器：原设备本体实施扩容/结构改造', related_equipment_ids: ['1'], source: 'EA' },
    { point_id: 'RP-003', type: 'parallel', description: '甲醇回收塔 增设并联系列', related_equipment_ids: ['5_1'], source: 'EA' },
    { point_id: 'RP-004', type: 'series', description: '催化蒸馏塔B 增设串联系列', related_equipment_ids: ['2_1'], source: 'EA' },
  ];

  const base = {
    contract_version: '1.0',
    project_id: 'demo-unit-001',
    project_name: 'MTBE 装置扩能改造项目',
    recommended_scheme: {
      scheme_id: 'SCHEME-C',
      name: '方案C（新增设备：前置预反应强化系统）',
      description: SCHEME_DESCRIPTION,
      status: 'available',
    },
    economic_summary: economic,
    pending_user_inputs: [
      { field: 'construction_unit', label: '建设单位（主办单位）', current: '未提供（保留缺口）', how_to_supply: '--construction-unit 传入后重跑阶段①' },
    ],
    equipment_summary,
    retrofit_points,
    confirmation_instructions: {
      required_action: '请用户确认以上关键事实；如需修正，在 confirmation_response.json 的 changes 中提供修改后值。',
      missing_value_rule: '未形成 FA-06/FA-07 结果时显示待计算，不允许由 LLM 补造经济指标。',
    },
  };

  if (status === 'confirmed') {
    return {
      ...base,
      confirmation_status: 'confirmed',
      confirmed_at: '2026-09-01T03:00:00+00:00',
      confirmed_by: 'user',
      baseline_hash: 'a1b2c3d4e5f6...',
    };
  }
  return { ...base, confirmation_status: 'pending' };
}

/** 正常结果:生成确认单待人工确认(needs_confirmation)。 */
function needsConfirmationResult(): Record<string, unknown> {
  return {
    exit_code: 12,
    status: 'needs_confirmation',
    resume_exit_code: 12,
    facts: `${OUT}/project_facts.json`,
    report_confirmation: `${OUT}/report_confirmation.json`,
    report_confirmation_markdown: `${OUT}/report_confirmation.md`,
    report_confirmation_schema: 'schemas/report_confirmation.schema.json',
    confirmation_response_schema: 'schemas/report_confirmation_response.schema.json',
    next_action:
      'Present recommended scheme, economic summary, full equipment list and retrofit points to the user. After explicit confirmation, rerun with --confirm-as-is or --confirmation-response.',
    confirmation: confirmationPayload('pending'),
  };
}

/** 确认响应无效(confirmation_invalid)。 */
function confirmationInvalidResult(): Record<string, unknown> {
  return {
    exit_code: 12,
    status: 'confirmation_invalid',
    resume_exit_code: 12,
    report_confirmation: `${OUT}/report_confirmation.json`,
    confirmation_response: `${OUT}/confirmation_response.json`,
    error: 'confirmation_response.decision 必须为 confirmed',
    next_action: 'Correct the confirmation response and rerun.',
    confirmation: confirmationPayload('pending'),
  };
}

/** 已确认结果(confirmed)。 */
function confirmedResult(): Record<string, unknown> {
  return {
    exit_code: 0,
    status: 'confirmed',
    resume_exit_code: 0,
    facts: `${OUT}/project_facts.json`,
    annualization: `${OUT}/annualization_result.json`,
    energy_conversion: `${OUT}/energy_conversion_result.json`,
    report_confirmation: `${OUT}/report_confirmation.json`,
    report_confirmation_markdown: `${OUT}/report_confirmation.md`,
    confirmed_facts: `${OUT}/confirmed_project_facts.json`,
    confirmed_report_confirmation: `${OUT}/confirmed_report_confirmation.json`,
    confirmation: confirmationPayload('confirmed'),
  };
}

/** 与 server 真实 report_progress 调用一致的进度步骤。 */
function progressSteps(): ProgressStep[] {
  return [
    { progress: 1, total: 3, message: '年化计算并 merge 回写' },
    { progress: 3, total: 3, message: '确认阶段完成' },
  ];
}

function scenario(
  id: string,
  label: string,
  args: Record<string, unknown>,
  result: Record<string, unknown>,
): MockScenario {
  return {
    id,
    label,
    toolName: 'engineering_confirmation',
    args,
    steps: progressSteps(),
    review: {
      reviewId: `review-conf-${id}`,
      finalResult: result,
      result,
    },
    skipReview: true,
  };
}

function needsConfirmationScenario(): MockScenario {
  return scenario(
    'needs-confirmation',
    '正常:生成确认单待确认',
    { facts: `${OUT}/project_facts.json`, output_dir: OUT },
    needsConfirmationResult(),
  );
}

function confirmationInvalidScenario(): MockScenario {
  return scenario(
    'confirmation-invalid',
    '异常:确认响应无效',
    { facts: `${OUT}/project_facts.json`, output_dir: OUT, confirmation_response: `${OUT}/confirmation_response.json` },
    confirmationInvalidResult(),
  );
}

function confirmedScenario(): MockScenario {
  return scenario(
    'confirmed',
    '正常:已确认',
    { facts: `${OUT}/project_facts.json`, output_dir: OUT, confirmation_response: `${OUT}/confirmation_response.json` },
    confirmedResult(),
  );
}

function missingArgsScenario(): MockScenario {
  return {
    id: 'missing-facts',
    label: '异常:缺少 facts 参数',
    toolName: 'engineering_confirmation',
    args: { output_dir: OUT },
    steps: progressSteps(),
    errorResult: {
      content: [{ type: 'text', text: '缺少必填参数 facts' }],
    },
  };
}

export const MOCK_TOOLS: ToolGroup[] = [
  {
    name: 'engineering_confirmation',
    label: '关键事实确认',
    page: 'EngineeringConfirmationPage',
    scenarios: [
      needsConfirmationScenario(),
      confirmationInvalidScenario(),
      confirmedScenario(),
      missingArgsScenario(),
    ],
  },
];

export function findToolGroup(toolName: string): ToolGroup | undefined {
  return MOCK_TOOLS.find((t) => t.name === toolName);
}
