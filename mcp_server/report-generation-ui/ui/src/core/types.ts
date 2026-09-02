/**
 * 公共类型定义。
 */

/** 通用会话状态：状态机流转。 */
export type SessionStatus =
  | 'pending'        // 等待工具入参
  | 'processing'     // 工具执行中
  | 'waiting_review' // 等待用户审核
  | 'completed'      // 完成
  | 'error';         // 错误

/** 单条事件。 */
export interface SessionEvent {
  event: string;
  data: Record<string, unknown>;
  timestamp: number;
}

/** /api/session/{id}/submit 请求体。 */
export interface SubmitReviewRequest {
  approved: boolean;
  edited_data?: unknown;
  reason?: string;
}

/** /api/session/{id}/submit 响应。 */
export interface SubmitReviewResponse {
  session_id: string;
  review_status: 'approved' | 'rejected';
  result_path: string;
  summary: Record<string, unknown>;
  message: string;
  reason?: string;
}
