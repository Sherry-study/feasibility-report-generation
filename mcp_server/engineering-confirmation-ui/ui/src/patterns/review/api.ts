/**
 * API 层 -- 审核模式专用。
 *
 * 提交审核 -- 反向调用 MCP server 的 submit_review 工具，唤醒阻塞的审核工具。
 */
import { mcpApp } from '@/core/mcpApp';
import type { SubmitReviewRequest, SubmitReviewResponse } from '@/core/types';

/**
 * 提交审核 -- 反向调用 MCP server 的 submit_review 工具,唤醒阻塞的审核工具。
 *
 * review_id 从 mcpApp store 的 progress.uiEvent.review_id 读取
 * (由审核工具通过 progress 通知推送)。
 */
export async function submitReview(
  _id: string,
  body: SubmitReviewRequest,
): Promise<SubmitReviewResponse> {
  const reviewId = (mcpApp.getSnapshot().progress?.uiEvent?.review_id as string | undefined) ?? null;
  if (!reviewId) {
    throw new Error('未收到 review_id,无法提交审核(审核工具可能未进入阻塞态)');
  }

  const result = await mcpApp.callTool('submit_review', {
    review_id: reviewId,
    approved: body.approved,
    edited_data: body.approved ? body.edited_data : undefined,
    reason: body.reason ?? '',
  });

  if (result.isError) {
    const text = Array.isArray(result.content)
      ? result.content.map((c) => c.text).filter(Boolean).join('\n')
      : 'submit_review 调用失败';
    throw new Error(text);
  }

  return {
    session_id: _id || 'local',
    review_status: body.approved ? 'approved' : 'rejected',
    result_path: '(submitted via MCP submit_review)',
    summary: (result.structuredContent as Record<string, unknown>) ?? {},
    message: body.approved ? '审核已通过' : `已驳回:${body.reason || '未提供原因'}`,
    reason: body.reason,
  };
}
