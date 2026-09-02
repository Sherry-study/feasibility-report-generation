/**
 * 通过 `read_image` MCP 工具读取图片，返回 data URL。
 *
 * image_path 来源：
 *   1. tool-input.args.image_path（工具入参，最早可用）
 *   2. tool-input.args.image_paths[0]（多页工具入参）
 *   3. tool-result.structuredContent.image_path（工具返回，兜底）
 *   4. tool-result.structuredContent.image_paths[0]（多页工具返回，兜底）
 *
 * 同一 path 只请求一次（ref 去重）。请求失败时返回 null（编辑器无背景图也能工作）。
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useMcpApp, mcpApp } from '@/core/mcpApp';

export function useToolImage(): {
  imageUrl: string | null;
  loading: boolean;
  error: string | null;
} {
  const { toolInput, toolResult, progress } = useMcpApp();
  const [imageUrl, setImageUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fetchedPathRef = useRef<string | null>(null);

  // 从 progress / tool-input.args / tool-result.structuredContent 提取 image_path
  const imagePath = useMemo(() => {
    const progressImagePaths = progress?.uiEvent?.image_paths;
    if (Array.isArray(progressImagePaths) && progressImagePaths.length > 0) {
      return progressImagePaths[0] as string;
    }
    const args = toolInput?.args as Record<string, unknown> | undefined;
    if (args?.image_path && typeof args.image_path === 'string') {
      return args.image_path;
    }
    if (Array.isArray(args?.image_paths) && args.image_paths.length > 0) {
      return args.image_paths[0] as string;
    }
    const sc = toolResult?.structuredContent as
      | { image_path?: unknown; image_paths?: unknown }
      | undefined;
    if (sc?.image_path && typeof sc.image_path === 'string') {
      return sc.image_path;
    }
    if (Array.isArray(sc?.image_paths) && sc.image_paths.length > 0) {
      return sc.image_paths[0] as string;
    }
    return null;
  }, [progress, toolInput, toolResult]);

  useEffect(() => {
    if (!imagePath || fetchedPathRef.current === imagePath) return;
    fetchedPathRef.current = imagePath;
    setLoading(true);
    setError(null);

    mcpApp
      .callTool('read_image', { path: imagePath })
      .then((result) => {
        const sc = result.structuredContent as
          | { mime_type?: string; data_base64?: string }
          | undefined;
        if (sc?.data_base64 && sc?.mime_type) {
          setImageUrl(`data:${sc.mime_type};base64,${sc.data_base64}`);
        } else {
          setError('read_image 返回数据格式异常');
        }
      })
      .catch((e) => {
        setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => setLoading(false));
  }, [imagePath]);

  return { imageUrl, loading, error };
}
