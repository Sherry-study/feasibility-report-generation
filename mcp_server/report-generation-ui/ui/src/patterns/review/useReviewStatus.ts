/**
 * 审核状态派生 -- 从 MCP App store 派生 SessionStatus（审核模式专用）。
 *
 * 通用映射：app status -> SessionStatus。
 * 业务页面如需细分执行阶段，可直接读 app.progress 自行展示。
 */
import { useMcpApp } from '@/core/mcpApp';
import type { SessionStatus } from '@/core/types';

/** MCP App 状态 -> SessionStatus 映射。 */
function statusFromApp(appStatus: string): SessionStatus {
  switch (appStatus) {
    case 'initializing':
    case 'ready':
      return 'pending';
    case 'processing':
      return 'processing';
    case 'result_ready':
      return 'waiting_review';
    case 'error':
      return 'error';
    default:
      return 'pending';
  }
}

/**
 * 派生当前审核状态。
 * @returns SessionStatus；null 表示初始化中(app.status === 'initializing')
 */
export function useReviewStatus(): SessionStatus | null {
  const app = useMcpApp();

  if (app.status === 'initializing') return null;

  if (app.progress?.uiEvent?.review_pending) return 'waiting_review';

  return statusFromApp(app.status);
}
