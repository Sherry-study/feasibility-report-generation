/**
 * MCP Apps postMessage 客户端。
 *
 * 本模块实现 SEP-1865 (io.modelcontextprotocol/ui) 的 iframe 侧通信：
 *   1. ui/initialize 握手 -> 获取 host context（theme / capabilities）
 *   2. ui/notifications/initialized 通知 host 握手完成
 *   3. 接收 ui/notifications/tool-input（工具入参，如 image_path）
 *   4. 接收 ui/notifications/tool-result（工具返回结果，含 structuredContent）
 *   5. tools/call 反向调用 host 上其它 MCP 工具
 *
 * MCP server 只提供工具，不管理 session、不提供 HTTP 端点。
 * 所有数据通过 postMessage 从 host 获取。
 */

import { useEffect, useSyncExternalStore } from 'react';
import type { SessionEvent } from '@/core/types';

// ---------------------------------------------------------------------------
// 类型定义
// ---------------------------------------------------------------------------

/** JSON-RPC 2.0 消息。 */
interface JsonRpcMessage {
  jsonrpc: '2.0';
  id?: number | string;
  method?: string;
  params?: unknown;
  result?: unknown;
  error?: { code: number; message: string; data?: unknown };
}

/** host context（ui/initialize 响应）。 */
export interface HostContext {
  protocolVersion?: string;
  theme?: 'light' | 'dark' | 'system';
  capabilities?: Record<string, unknown>;
  [k: string]: unknown;
}

/** 工具入参通知（ui/notifications/tool-input）。 */
export interface ToolInput {
  toolName?: string;
  args?: Record<string, unknown>;
  [k: string]: unknown;
}

/** 工具结果通知（ui/notifications/tool-result）。 */
export interface ToolResult {
  /** 结构化结果（工作流返回的 dict）。 */
  structuredContent?: Record<string, unknown>;
  /** 文本内容数组（fallback）。 */
  content?: Array<{ type: string; text?: string }>;
  isError?: boolean;
  [k: string]: unknown;
}

/** 进度通知（notifications/progress，host 可能转发）。
 *
 * 标准 MCP progress 通知只有 progress/total/message，但
 * ProgressNotificationParams 的 pydantic model_config = extra="allow"，
 * 因此后端可通过 _send_progress_with_data 携带一个结构化的 ``uiEvent``
 * 扩展字段，前端直接从 ``progress.uiEvent.<key>`` 读取逐步渲染所需数据。
 */
export interface ProgressNotification {
  progressToken?: unknown;
  progress?: number;
  total?: number;
  message?: string;
  /** 扩展字段：承载所有非标准 progress 数据。
   *
   * core 层不定义具体字段 -- 业务模式（如审核）在 patterns/ 下定义专用类型。
   * 例如：import { getReviewUiEvent } from '@/patterns/review/reviewTypes'
   */
  uiEvent?: {
    [k: string]: unknown;
  };
}

/** progress 事件名解析器：输入 uiEvent，返回事件名；不识别时返回 null。 */
export type ProgressEventResolver = (
  uiEvent: ProgressNotification['uiEvent'] | undefined,
) => string | null;

const progressEventResolvers = new Set<ProgressEventResolver>();

/**
 * 注册 progress 事件名解析器（业务模式在模块加载时调用）。
 *
 * 事件名归属业务模式，core 不感知业务语义（如 review_pending/final_result）；
 * 未注册任何解析器或均不识别时，事件名默认 'progress'。
 *
 * 用法（patterns/review/reviewEvents.ts）：
 *   registerProgressEventResolver((uiEvent) =>
 *     uiEvent?.review_pending ? 'waiting_for_review' : null);
 *
 * @returns 注销函数（测试隔离用）。
 */
export function registerProgressEventResolver(
  resolver: ProgressEventResolver,
): () => void {
  progressEventResolvers.add(resolver);
  return () => progressEventResolvers.delete(resolver);
}

/** App 连接状态。 */
export type AppStatus =
  | 'initializing' // 等待 ui/initialize 握手
  | 'ready' // 握手完成，等待 tool-input/result
  | 'processing' // 收到 tool-input，等待 tool-result
  | 'result_ready' // 收到 tool-result
  | 'error';

// ---------------------------------------------------------------------------
// McpApp 单例
// ---------------------------------------------------------------------------

interface AppStore {
  status: AppStatus;
  hostContext: HostContext | null;
  toolInput: ToolInput | null;
  toolResult: ToolResult | null;
  progress: ProgressNotification | null;
  progressEvents: SessionEvent[];
  error: string | null;
  /** 反向工具调用（tools/call）是否进行中。 */
  callToolLoading: boolean;
}

class McpApp {
  private nextId = 1;
  private pending = new Map<
    number,
    { resolve: (v: unknown) => void; reject: (e: Error) => void }
  >();
  /** host 的 origin，握手前为 '*'，握手后锁定为实际 origin。 */
  private hostOrigin: string = '*';

  private store: AppStore = {
    status: 'initializing',
    hostContext: null,
    toolInput: null,
    toolResult: null,
    progress: null,
    progressEvents: [],
    error: null,
    callToolLoading: false,
  };

  private listeners = new Set<() => void>();
  private initStarted = false;
  /** 在途的 tools/call 请求数（callToolLoading 由它派生，支持并发调用）。 */
  private callToolPendingCount = 0;

  constructor() {
    if (typeof window !== 'undefined' && window.parent) {
      window.addEventListener('message', this.onMessage);
    }
  }

  // ---- 外部 store 订阅（useSyncExternalStore） ----

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  getSnapshot = (): AppStore => this.store;

  private emit(): void {
    // 浅拷贝触发 React 重渲染
    this.store = { ...this.store };
    this.listeners.forEach((l) => l());
  }

  // ---- postMessage 收发 ----

  private onMessage = (event: MessageEvent): void => {
    // 安全校验：握手完成后，只接受来自 hostOrigin 的消息
    if (this.hostOrigin !== '*' && event.origin !== this.hostOrigin) return;

    const msg: JsonRpcMessage = event.data;
    if (!msg || typeof msg !== 'object' || msg.jsonrpc !== '2.0') return;

    // 1) 响应：匹配 pending 请求
    if (msg.id != null && this.pending.has(Number(msg.id))) {
      // 首次收到 host 响应时锁定 origin
      if (this.hostOrigin === '*') {
        this.hostOrigin = event.origin;
      }
      const id = Number(msg.id);
      const p = this.pending.get(id)!;
      this.pending.delete(id);
      if (msg.error) {
        p.reject(new Error(msg.error.message || 'JSON-RPC error'));
      } else {
        p.resolve(msg.result);
      }
      return;
    }

    // 2) 通知：host -> app
    if (!msg.method) return;

    // 通知也更新 hostOrigin（host 可能在握手前发送通知）
    if (this.hostOrigin === '*' && event.origin !== 'null' && event.origin !== '') {
      this.hostOrigin = event.origin;
    }

    this.handleNotification(msg.method, msg.params);
  };

  private handleNotification(method: string, params: unknown): void {
    const p = (params ?? {}) as Record<string, unknown>;

    switch (method) {
      case 'ui/notifications/tool-input':
        // 新会话开始：清空上一会话的进度/结果/错误。
        // 否则旧 progress.uiEvent(如 review_id) 或旧 toolResult 残留，
        // 会导致 submitReview 用旧 review_id 提交、页面展示上一会话的数据。
        this.store = {
          ...this.store,
          toolInput: p as unknown as ToolInput,
          status: 'processing',
          progress: null,
          progressEvents: [],
          toolResult: null,
          error: null,
        };
        this.emit();
        break;

      case 'ui/notifications/tool-result': {
        const result = p as unknown as ToolResult;
        this.store = {
          ...this.store,
          toolResult: result,
          status: result.isError ? 'error' : 'result_ready',
          error: result.isError ? this.extractErrorText(result) : null,
        };
        this.emit();
        break;
      }

      case 'notifications/progress': {
        const incoming = p as unknown as ProgressNotification;
        const incomingUiEvent = incoming.uiEvent;

        // 把 progress 通知转成 SessionEvent 累积到 progressEvents
        // data 合并 uiEvent 业务字段，时间线可展示业务细节（如 item_count）
        const ev: SessionEvent = {
          event: inferEventType(incoming, incomingUiEvent),
          data: {
            message: incoming.message ?? '',
            progress: incoming.progress,
            total: incoming.total,
            ...(incomingUiEvent ?? {}),
          },
          timestamp: Date.now() / 1000,
        };
        const prevEvents = this.store.progressEvents;
        // 去重：相同 message + progress 视为重复（host 可能重发）
        const isDup =
          prevEvents.length > 0 &&
          prevEvents[prevEvents.length - 1].data.message === ev.data.message &&
          prevEvents[prevEvents.length - 1].data.progress === ev.data.progress;
        const nextEvents = isDup ? prevEvents : [...prevEvents, ev];

        this.store = {
          ...this.store,
          progress: {
            ...incoming,
            // 浅合并 uiEvent：后到的字段覆盖前值
            uiEvent: { ...this.store.progress?.uiEvent, ...incomingUiEvent },
          },
          progressEvents: nextEvents,
        };
        this.emit();
        break;
      }

      case 'ui/resource-teardown':
        // host 即将卸载 iframe；无需处理
        break;

      default:
        // 忽略未知通知
        break;
    }
  }

  private extractErrorText(result: ToolResult): string {
    if (Array.isArray(result.content)) {
      const text = result.content
        .map((c) => c.text)
        .filter(Boolean)
        .join('\n');
      if (text) return text;
    }
    return '工具执行失败';
  }

  private send(message: object): void {
    if (window.parent) {
      window.parent.postMessage(message, this.hostOrigin);
    }
  }

  private sendRequest(method: string, params: object): Promise<unknown> {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      this.send({ jsonrpc: '2.0', id, method, params });
      // 30s 超时
      setTimeout(() => {
        if (this.pending.has(id)) {
          this.pending.delete(id);
          reject(new Error(`${method} 请求超时`));
        }
      }, 30000);
    });
  }

  private sendNotification(method: string, params: object): void {
    this.send({ jsonrpc: '2.0', method, params });
  }

  // ---- 公开 API ----

  /** 发起 ui/initialize 握手（幂等，只执行一次）。 */
  async initialize(): Promise<HostContext> {
    if (this.initStarted) return this.store.hostContext ?? {};
    this.initStarted = true;

    try {
      const result = (await this.sendRequest('ui/initialize', {
        protocolVersion: '2025-06-18',
        appInfo: { name: 'mcp-app-ui', version: '1.0.0' },
        appCapabilities: {},
      })) as HostContext;

      this.store = {
        ...this.store,
        hostContext: result,
        status: 'ready',
      };
      this.emit();

      // 通知 host 握手完成
      this.sendNotification('ui/notifications/initialized', {});
      return result;
    } catch (e) {
      this.store = {
        ...this.store,
        status: 'error',
        error: e instanceof Error ? e.message : String(e),
      };
      this.emit();
      throw e;
    }
  }

  /** 反向调用 host 上的 MCP 工具。 */
  async callTool(
    name: string,
    args: Record<string, unknown>,
  ): Promise<ToolResult> {
    // 计数式 loading：并发调用全部结束后才熄灭，避免互相覆盖
    this.callToolPendingCount += 1;
    this.store = { ...this.store, callToolLoading: true };
    this.emit();
    try {
      return (await this.sendRequest('tools/call', {
        name,
        arguments: args,
      })) as ToolResult;
    } finally {
      this.callToolPendingCount -= 1;
      this.store = {
        ...this.store,
        callToolLoading: this.callToolPendingCount > 0,
      };
      this.emit();
    }
  }

  /** 请求取消当前工具调用（通知 host）。best-effort，无响应等待。 */
  cancelCurrentTool(): void {
    this.sendNotification('notifications/cancelled', {});
  }
}

// ---------------------------------------------------------------------------
// 单例
// ---------------------------------------------------------------------------

export const mcpApp = new McpApp();

// ---------------------------------------------------------------------------
// React Hooks
// ---------------------------------------------------------------------------

/** 订阅 MCP App 全局状态。 */
export function useMcpApp(): AppStore {
  return useSyncExternalStore(mcpApp.subscribe, mcpApp.getSnapshot, mcpApp.getSnapshot);
}

/** 订阅工具入参（image_path 等）。 */
export function useToolInput(): ToolInput | null {
  return useMcpApp().toolInput;
}

/** 订阅反向工具调用（tools/call）是否进行中。 */
export function useCallToolLoading(): boolean {
  return useMcpApp().callToolLoading;
}

/**
 * 订阅工具结果，并归一化为 final_result 结构。
 *
 * toolResult 优先。阻塞期(审核等待)toolResult 为 null 时，
 * 审核模式页面可使用 patterns/review/useReviewResult 获取 final_result 兜底。
 */
export function useNormalizedToolResult(): {
  finalResult: Record<string, unknown> | null;
  rawResult: ToolResult | null;
  isError: boolean;
} {
  const app = useMcpApp();
  const { toolResult } = app;

  const raw = toolResult?.structuredContent ?? null;
  const progressFinalResult =
    typeof app.progress?.uiEvent?.final_result === 'object' &&
    app.progress.uiEvent.final_result !== null
      ? (app.progress.uiEvent.final_result as Record<string, unknown>)
      : null;
  const progressUiEvent = app.progress?.uiEvent ?? null;

  if (!raw) {
    if (progressFinalResult) {
      return {
        finalResult: normalizeResult(progressFinalResult, progressUiEvent),
        rawResult: toolResult,
        isError: !!toolResult?.isError,
      };
    }
    return { finalResult: null, rawResult: toolResult, isError: !!toolResult?.isError };
  }

  const finalResult = normalizeResult(raw, progressUiEvent);
  return { finalResult, rawResult: toolResult, isError: !!toolResult?.isError };
}

/** 把 report_finalize 的 {status, data, warnings} 信封归一化为页面期望的 final_result。
 *
 * - 状态、产物路径、摘要、warnings、error 始终以 structuredContent 为准；
 * - Tool 外层三态中的 success 会映射为 data.business_status，兼容既有页面状态；
 * - 完整 Markdown 只从 progress.uiEvent.markdown_content 读取；
 * - warnings 与 data.error 映射为页面现有 diagnostics 结构。
 */
function normalizeResult(
  raw: Record<string, unknown>,
  progressUiEvent: Record<string, unknown> | null,
): Record<string, unknown> {
  const data = raw.data;
  // 非新信封结构（无 status/data 判别字段）：原样透传，避免破坏未知上游
  if (typeof raw.status !== 'string' || typeof data !== 'object' || data === null) {
    return raw;
  }

  const envelope = raw as {
    status: string;
    data: {
      business_status?: unknown;
      artifacts?: unknown;
      artifact?: unknown;
      manifest_path?: unknown;
      summary?: unknown;
      error?: { code?: unknown; message?: unknown } | null;
    };
    warnings?: Array<{ level?: unknown; code?: unknown; message?: unknown }>;
  };

  const diagnostics: Array<{ level: string; code: string; message: string }> = [];
  for (const w of envelope.warnings ?? []) {
    if (typeof w !== 'object' || w === null) continue;
    diagnostics.push({
      level: typeof w.level === 'string' ? w.level : 'warning',
      code: typeof w.code === 'string' ? w.code : '',
      message: typeof w.message === 'string' ? w.message : '',
    });
  }
  if (envelope.data.error) {
    diagnostics.push({
      level: 'fatal',
      code: typeof envelope.data.error.code === 'string' ? envelope.data.error.code : '',
      message: typeof envelope.data.error.message === 'string' ? envelope.data.error.message : '',
    });
  }

  const normalizedData = { ...envelope.data } as Record<string, unknown>;
  const artifact = normalizedData.artifact as { path?: unknown } | undefined;
  if (artifact && typeof artifact.path === 'string') {
    normalizedData.artifact = { ...artifact, uri: artifact.path };
  }
  const artifacts = normalizedData.artifacts as
    | { markdown_path?: unknown; docx_path?: unknown }
    | undefined;
  if (artifacts) {
    normalizedData.artifacts = {
      ...artifacts,
      markdown:
        typeof artifacts.markdown_path === 'string'
          ? artifacts.markdown_path
          : undefined,
      docx:
        typeof artifacts.docx_path === 'string'
          ? artifacts.docx_path
          : undefined,
    };
  }
  const pageStatus =
    envelope.status === 'success' && typeof normalizedData.business_status === 'string'
      ? normalizedData.business_status
      : envelope.status === 'error'
        ? 'failed'
        : envelope.status;

  return {
    ...normalizedData,
    status: pageStatus,
    markdown_content: progressUiEvent?.markdown_content,
    diagnostics,
  };
}

/**
 * 在应用启动时发起 ui/initialize 握手。
 * 放在 App 根组件 useEffect 中调用。
 */
export function useMcpInitialize(): void {
  useEffect(() => {
    mcpApp.initialize().catch((e) => {
      // 错误已写入 store，这里只防止 unhandled rejection
      console.error('[mcpApp] initialize failed:', e);
    });
  }, []);
}

// ---------------------------------------------------------------------------
// 辅助纯函数
// ---------------------------------------------------------------------------

function inferEventType(
  _incoming: ProgressNotification,
  uiEvent?: ProgressNotification['uiEvent'],
): string {
  // 事件名由业务模式注册的解析器决定（见 registerProgressEventResolver）；
  // core 不感知 review 等业务语义。
  for (const resolver of progressEventResolvers) {
    const type = resolver(uiEvent);
    if (type) return type;
  }
  return 'progress';
}
