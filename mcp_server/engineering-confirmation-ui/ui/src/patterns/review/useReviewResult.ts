/** 审核模式专用：toolResult 为 null 时用 progress.uiEvent.final_result 兜底。 */
import { useMcpApp, type ToolResult } from '@/core/mcpApp';

export function useReviewResult(): {
  finalResult: Record<string, unknown> | null;
  rawResult: ToolResult | null;
  isError: boolean;
} {
  const app = useMcpApp();
  const { toolResult } = app;

  const raw = toolResult?.structuredContent
    ?? (app.progress?.uiEvent?.final_result as Record<string, unknown> | undefined)
    ?? null;

  if (!raw) {
    return { finalResult: null, rawResult: toolResult, isError: !!toolResult?.isError };
  }

  return { finalResult: raw, rawResult: toolResult, isError: !!toolResult?.isError };
}
