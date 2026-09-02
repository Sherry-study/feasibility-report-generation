/**
 * 审核模式专用类型 -- 从 core 层移出。
 *
 * 这些字段通过 progress.uiEvent 传递，但语义上属于审核工作流。
 * 仅在使用审核模式的页面中引用。
 */

/** 审核工具通过 progress.uiEvent 推送的审核态数据。 */
export interface ReviewUiEvent {
  /** 审核工具推送:提取完成,等待用户审核。 */
  review_pending?: boolean;
  /** 审核会话 id(UI 反向调用 submit_review 时回传)。 */
  review_id?: string;
  /** 审核工具名。 */
  tool_name?: string;
  /** 提取阶段的终态结果(UI 渲染审核编辑器用)。 */
  final_result?: Record<string, unknown>;
}

/** 从 uiEvent 中提取审核字段（类型安全访问）。 */
export function getReviewUiEvent(
  uiEvent: Record<string, unknown> | undefined,
): ReviewUiEvent | null {
  if (!uiEvent) return null;
  if (!uiEvent.review_pending) return null;
  return {
    review_pending: !!uiEvent.review_pending,
    review_id: uiEvent.review_id as string | undefined,
    tool_name: uiEvent.tool_name as string | undefined,
    final_result: uiEvent.final_result as Record<string, unknown> | undefined,
  };
}
