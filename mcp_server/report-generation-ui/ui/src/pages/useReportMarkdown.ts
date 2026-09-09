/**
 * 报告 Markdown 读取 hook。
 *
 * 只使用 progress.uiEvent.final_result.markdown_content。MCP Server 不提供公开
 * read_file/read_artifact 工具，若 Host 未透传该字段，页面应暴露为可见性问题。
 */

import { useEffect, useMemo, useState } from 'react';
import { parseReportDoc, type ReportDoc } from './reportMarkdown';

export function useReportMarkdown(
  path: string | null | undefined,
  inlineContent?: string | null,
): {
  doc: ReportDoc | null;
  loading: boolean;
  error: string | null;
  reload: () => void;
} {
  const effective = inlineContent && inlineContent.length > 0 ? inlineContent : undefined;

  const [content, setContent] = useState<string | null>(() => {
    if (effective) return effective;
    return null;
  });
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  function reload() {
    setContent(null);
    setError(null);
    setLoading(false);
    setVersion((v) => v + 1);
  }

  useEffect(() => {
    if (effective) {
      setContent(effective);
      setLoading(false);
      setError(null);
      return;
    }
    if (!path) {
      setContent(null);
      setLoading(false);
      setError(null);
      return;
    }
    setContent(null);
    setLoading(false);
    setError('progress.uiEvent.final_result.markdown_content 缺失，无法展示报告正文。');
    return undefined;
  }, [path, effective, version]);

  const doc = useMemo(() => (content ? parseReportDoc(content) : null), [content]);

  return { doc, loading, error, reload };
}
