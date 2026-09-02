/**
 * 报告 Markdown 读取 hook。
 *
 * 优先使用工具结果内联的 ``markdown_content``（真实算法会把报告正文直接
 * 内联进返回结果）；否则回退到 ``read_file`` MCP 工具按 ``markdown`` 路径读取。
 * 解析结果带模块级缓存（同 path 不重复请求）。
 */

import { useEffect, useMemo, useState } from 'react';
import { mcpApp } from '@/core/mcpApp';
import { parseReportDoc, type ReportDoc } from './reportMarkdown';

const cache = new Map<string, string>();
const inflight = new Map<string, Promise<string>>();

async function fetchMarkdown(path: string): Promise<string> {
  if (cache.has(path)) return cache.get(path)!;
  if (inflight.has(path)) return inflight.get(path)!;

  const p = mcpApp
    .callTool('read_file', { path })
    .then((result) => {
      const sc = result.structuredContent as
        | { content?: unknown; text?: unknown }
        | undefined;
      let text = '';
      if (typeof sc?.content === 'string') text = sc.content;
      else if (typeof sc?.text === 'string') text = sc.text;
      else if (Array.isArray(result.content)) {
        text = result.content.map((c) => c.text ?? '').join('\n');
      }
      if (!text) throw new Error('read_file 未返回文本内容');
      cache.set(path, text);
      inflight.delete(path);
      return text;
    })
    .catch((e) => {
      inflight.delete(path);
      throw e;
    });

  inflight.set(path, p);
  return p;
}

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
    return path && cache.has(path) ? cache.get(path)! : null;
  });
  const [loading, setLoading] = useState<boolean>(!effective && !!path && !cache.has(path));
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  function reload() {
    if (path) {
      cache.delete(path);
      inflight.delete(path);
    }
    setContent(null);
    setError(null);
    setLoading(!!path);
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
    if (cache.has(path)) {
      setContent(cache.get(path)!);
      setLoading(false);
      setError(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchMarkdown(path)
      .then((text) => {
        if (cancelled) return;
        setContent(text);
        setLoading(false);
      })
      .catch((e) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : String(e));
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [path, effective, version]);

  const doc = useMemo(() => (content ? parseReportDoc(content) : null), [content]);

  return { doc, loading, error, reload };
}
