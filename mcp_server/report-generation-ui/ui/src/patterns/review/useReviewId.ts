/** 从 progress.uiEvent 中提取 review_id（审核模式专用）。 */
import { useMcpApp } from '@/core/mcpApp';

export function useReviewId(): string | null {
  const app = useMcpApp();
  return (app.progress?.uiEvent?.review_id as string | undefined) ?? null;
}
