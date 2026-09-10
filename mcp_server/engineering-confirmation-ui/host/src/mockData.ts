/**
 * engineering_facts 的 mock 数据。
 *
 * 本 host 控制台只服务 engineering_facts 一个工具:有进度推送,无审核。
 * 进度步骤镜像真实核心通过 content.report_progress 发出的阶段。
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
const SOURCE_DIR = 'D:/0公司相关/Redesign/可研报告编写/算法输出-测试数据/装置级测试数据';

/** 采用方案描述(与 engineering_facts mock 保持一致,字段镜像真实算法输出)。 */
const SCHEME_DESCRIPTION =
  '位置：催化蒸馏塔B至催化蒸馏塔A的进料管线。单元作用：增设独立反应单元，进行高转化率预反应。机理：在物料进入催化蒸馏塔A前增设新醚化反应器，提前消耗大部分异戊烯，大幅降低进入塔A的反应负荷。预反应与催化蒸馏塔A形成“反应-分离-反应”梯级转化结构，缓解塔内平衡压力，提升系统整体收率。';

function engineeringFactsPayload(withEconomics = false): Record<string, unknown> {
  return {
    meta: { schema_version: '2.0', generated_at: '2026-09-08T00:00:00+00:00' },
    sources: [
      { source_id: 'plant_info', source_type: 'plant_info', location: '装置级/plant_info.json' },
      { source_id: 'scheme', source_type: 'scheme', location: '装置级/scheme.json' },
      { source_id: 'reactor_result', source_type: 'reactor_result', location: '反应器/plant_reactor_result.json' },
    ],
    basic_info: {
      construction_unit: '测试建设单位',
      ...(withEconomics
        ? {
            economic_summary: {
              total_investment: { value: 12480.6, unit: '万元' },
              average_annual_profit: { value: 2186.3, unit: '万元' },
            },
          }
        : {}),
    },
    unit: { name: '1万吨/年异戊烯联合生产装置', annual_operating_hours: 8000 },
    adopted_scheme: {
      status: 'selected',
      selected_names: ['方案C（新增设备：前置预反应强化系统）'],
      schemes: [
        {
          name: '前置预反应强化系统',
          description: SCHEME_DESCRIPTION,
        },
      ],
      optimized: true,
    },
    equipment: {
      object_catalog: [
        { equipment_id: '17', tag: 'R-104', name: '新醚化反应器', retrofit_status: '利旧' },
        { equipment_id: '1', tag: 'R-101', name: '醚化反应器', retrofit_status: '改造' },
        { equipment_id: '5_1', tag: 'T-101B', name: '甲醇回收塔_并联', retrofit_status: '新增' },
        { equipment_id: '2_1', tag: 'T-201B', name: '催化蒸馏塔B_并联', retrofit_status: '新增' },
      ],
      reactor: {
        selected_scheme: {
          reuse: [
            { id: '17', name: '新醚化反应器', scheme_type: 'reuse', description: '新醚化反应器利旧原醚化反应器设备。' },
          ],
          addition: [
            { id: '1', name: '醚化反应器', scheme_type: 'increase_volume', description: '醚化反应器采用扩容改造路线。' },
          ],
          parallel: [
            { id: '7', name: '醚解反应器', scheme_type: 'add_parallel', description: '醚解反应器增设并联反应器分担处理量。' },
            { id: '9', name: '异构化反应器', scheme_type: 'add_parallel', description: '异构化反应器启用备用反应器并联运行。' },
          ],
        },
      },
    },
    derived_facts: {
      utility_consumption_summary: {
        status: 'identified',
        items: [
          { medium: 'HO', medium_name: '导热油', status: 'clue_only', reason: 'quantity_or_hours_missing' },
          { medium: 'CW', medium_name: '循环冷却水', status: 'clue_only', reason: 'quantity_or_hours_missing' },
        ],
      },
      energy_conversion: {
        status: 'blocked',
        items: [
          { medium: 'HO', status: 'blocked', reason: 'physical_quantity_missing' },
          { medium: 'CW', status: 'blocked', reason: 'physical_quantity_missing' },
        ],
      },
    },
  };
}

/** mock structuredContent：{status, data, warnings} 统一信封（模型可见，不含完整工程事实）。 */
function engineeringFactsCompletedResult(): Record<string, unknown> {
  return {
    status: 'success',
    data: {
      business_status: 'completed',
      artifact: {
        path: `${OUT}/mcp_runs/engineering_facts/engineering_facts.json`,
        schema_version: '2.0',
        media_type: 'application/json',
      },
      summary: {
        source_count: 3,
        equipment_count: 4,
        adopted_scheme_count: 1,
        selected_names: ['方案C（新增设备：前置预反应强化系统）'],
        optimized: true,
      },
      error: null,
    },
    warnings: [
      { level: 'warning', code: 'NEW_DEVICE_PARAMS_MISSING', message: 'new equipment exists but new_device_params is missing.' },
    ],
  };
}

function engineeringFactsFailedResult(): Record<string, unknown> {
  return {
    status: 'failed',
    data: {
      business_status: 'failed',
      artifact: null,
      summary: {},
      error: {
        code: 'SOURCE_JSON_INVALID',
        message: '装置级/scheme.json is not valid JSON: Expecting value',
        retryable: true,
      },
    },
    warnings: [],
  };
}

function engineeringFactsProgressSteps(): ProgressStep[] {
  return [
    { progress: 0, total: 100, message: '扫描工程输入来源' },
    { progress: 35, total: 100, message: '读取工程输入来源' },
    { progress: 65, total: 100, message: '组装工程事实' },
    { progress: 90, total: 100, message: '保存工程事实' },
    { progress: 100, total: 100, message: '工程事实已完成' },
  ];
}

function diagnosticsFromEnvelope(result: Record<string, unknown>): Array<Record<string, string>> {
  const warnings = Array.isArray(result.warnings) ? result.warnings : [];
  const diagnostics = warnings
    .filter((item): item is Record<string, unknown> => typeof item === 'object' && item !== null)
    .map((item) => ({
      level: item.level === 'info' ? 'info' : 'warning',
      code: typeof item.code === 'string' ? item.code : '',
      message: typeof item.message === 'string' ? item.message : '',
    }));
  const data = result.data && typeof result.data === 'object' ? result.data as Record<string, unknown> : {};
  const error = data.error && typeof data.error === 'object' ? data.error as Record<string, unknown> : null;
  if (error) {
    diagnostics.push({
      level: 'fatal',
      code: typeof error.code === 'string' ? error.code : '',
      message: typeof error.message === 'string' ? error.message : '',
    });
  }
  return diagnostics;
}

function engineeringFactsUiFinalResult(
  result: Record<string, unknown>,
  withEconomics = false,
): Record<string, unknown> {
  const data = result.data && typeof result.data === 'object' ? result.data as Record<string, unknown> : {};
  const artifact = data.artifact && typeof data.artifact === 'object'
    ? data.artifact as Record<string, unknown>
    : null;
  const businessStatus = typeof data.business_status === 'string' ? data.business_status : result.status;
  return {
    ...data,
    artifact: artifact && typeof artifact.path === 'string' ? { ...artifact, uri: artifact.path } : artifact,
    status: result.status === 'success' ? businessStatus : result.status === 'error' ? 'failed' : result.status,
    engineering_facts: result.status === 'success' ? engineeringFactsPayload(withEconomics) : undefined,
    diagnostics: diagnosticsFromEnvelope(result),
  };
}

function engineeringFactsScenario(
  id: string,
  label: string,
  args: Record<string, unknown>,
  result: Record<string, unknown>,
  withEconomics = false,
): MockScenario {
  const steps = engineeringFactsProgressSteps();
  steps[steps.length - 1] = {
    ...steps[steps.length - 1],
    uiEvent: { final_result: engineeringFactsUiFinalResult(result, withEconomics) },
  };
  return {
    id,
    label,
    toolName: 'engineering_facts',
    args,
    steps,
    review: {
      reviewId: `review-facts-${id}`,
      finalResult: result,
      result,
    },
    skipReview: true,
  };
}

function engineeringFactsCompletedScenario(): MockScenario {
  return engineeringFactsScenario(
    'completed',
    '正常:整理完成',
    { input: { provider: 'local_directory', root: SOURCE_DIR, construction_unit: '测试建设单位' } },
    engineeringFactsCompletedResult(),
    true,
  );
}

function engineeringFactsNoEconomicsScenario(): MockScenario {
  return engineeringFactsScenario(
    'economics-pending',
    '边界:经济指标待计算',
    { input: { provider: 'local_directory', root: SOURCE_DIR } },
    engineeringFactsCompletedResult(),
    false,
  );
}

function engineeringFactsSourceErrorScenario(): MockScenario {
  return engineeringFactsScenario(
    'source-json-invalid',
    '异常:来源 JSON 损坏',
    { input: { provider: 'local_directory', root: SOURCE_DIR } },
    engineeringFactsFailedResult(),
  );
}

function engineeringFactsMissingLocationScenario(): MockScenario {
  return {
    id: 'missing-location',
    label: '异常:缺少 root',
    toolName: 'engineering_facts',
    args: { input: { provider: 'local_directory' } },
    steps: engineeringFactsProgressSteps(),
    errorResult: {
      content: [{ type: 'text', text: 'input.root is required.' }],
    },
  };
}

export const MOCK_TOOLS: ToolGroup[] = [
  {
    name: 'engineering_facts',
    label: '工程事实整理',
    page: 'EngineeringFactsPage',
    scenarios: [
      engineeringFactsCompletedScenario(),
      engineeringFactsNoEconomicsScenario(),
      engineeringFactsSourceErrorScenario(),
      engineeringFactsMissingLocationScenario(),
    ],
  },
];

export function findToolGroup(toolName: string): ToolGroup | undefined {
  return MOCK_TOOLS.find((t) => t.name === toolName);
}
