/**
 * 源图获取 hook -- 通过 read_image MCP 工具读取本地图片为 data URL。
 *
 * 设计：显式接收 path（单图 / 多图），内部带内存缓存（同 path 不重复请求），
 * 多个组件订阅同一 path 共享同一请求。
 */
import { useEffect, useState } from 'react';
import { mcpApp } from '@/core/mcpApp';

// 模块级缓存：path -> data URL（同 path 跨组件/跨页面共享）
const cache = new Map<string, string>();
// 进行中的请求：path -> Promise（防并发重复）
const inflight = new Map<string, Promise<string>>();

async function fetchImage(path: string): Promise<string> {
  if (cache.has(path)) return cache.get(path)!;
  if (inflight.has(path)) return inflight.get(path)!;

  const p = mcpApp
    .callTool('read_image', { path })
    .then((result) => {
      const sc = result.structuredContent as
        | { mime_type?: string; data_base64?: string }
        | undefined;
      if (!sc?.data_base64 || !sc?.mime_type) {
        throw new Error('read_image 返回数据格式异常');
      }
      const url = `data:${sc.mime_type};base64,${sc.data_base64}`;
      cache.set(path, url);
      inflight.delete(path);
      return url;
    })
    .catch((e) => {
      inflight.delete(path);
      throw e;
    });
  inflight.set(path, p);
  return p;
}

export interface SourceImageState {
  url: string | null;
  loading: boolean;
  error: string | null;
}

/** 单图 hook。 */
export function useSourceImage(imagePath: string | undefined | null): SourceImageState {
  const [state, setState] = useState<SourceImageState>({
    url: imagePath && cache.has(imagePath) ? cache.get(imagePath)! : null,
    loading: !!imagePath && !cache.has(imagePath),
    error: null,
  });

  useEffect(() => {
    if (!imagePath) {
      setState({ url: null, loading: false, error: null });
      return;
    }
    if (cache.has(imagePath)) {
      setState({ url: cache.get(imagePath)!, loading: false, error: null });
      return;
    }
    setState({ url: null, loading: true, error: null });
    let cancelled = false;
    fetchImage(imagePath)
      .then((url) => {
        if (!cancelled) setState({ url, loading: false, error: null });
      })
      .catch((e) => {
        if (!cancelled) {
          setState({ url: null, loading: false, error: e instanceof Error ? e.message : String(e) });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [imagePath]);

  return state;
}

/** 多图 hook（按页索引取图）。 */
export function useSourceImages(imagePaths: string[]): {
  urls: (string | null)[];
  loading: boolean;
  error: string | null;
} {
  const [urls, setUrls] = useState<(string | null)[]>(
    () => imagePaths.map((p) => cache.get(p) ?? null),
  );
  const [loading, setLoading] = useState<boolean>(imagePaths.some((p) => !cache.has(p)));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(imagePaths.some((p) => !cache.has(p)));
    setError(null);

    Promise.all(
      imagePaths.map((p) =>
        cache.has(p) ? Promise.resolve(cache.get(p)!) : fetchImage(p),
      ),
    )
      .then((results) => {
        if (!cancelled) {
          setUrls(results);
          setLoading(false);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : String(e));
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [imagePaths.join('|')]);

  return { urls, loading, error };
}
