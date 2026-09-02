/**
 * 编辑副本管理 hook -- 统一解决 editedData 不回写 bug。
 *
 * 提供：深拷贝初始数据、path 更新、整体替换、dirty 标记、reset、取提交值。
 * 替代 4 个页面里重复的 cloneData + updateField 逻辑。
 *
 * 说明：initial 按内容（深比较）同步，每次渲染传新对象字面量也不会
 * 触发同步死循环。但为减少每次渲染的深比较开销，仍建议用 useMemo 保持
 * initial 引用稳定（如 useEditedData(useMemo(() => transform(result), [result]))）。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

/** 按 'a.b.0.c' 形式的 path 设置值，返回新对象（不可变更新）。 */
function setByPath<T>(root: T, path: string, value: unknown): T {
  const parts = path.split('.');
  if (parts.length === 0) return root;
  const clone = (o: unknown): unknown =>
    Array.isArray(o) ? o.map((v) => v) :
    o && typeof o === 'object' ? { ...(o as Record<string, unknown>) } : o;

  let cur: unknown = clone(root);
  const newRoot = cur as T;
  let node: Record<string, unknown> | unknown[] = cur as Record<string, unknown> | unknown[];
  for (let i = 0; i < parts.length - 1; i++) {
    const key = parts[i];
    const child = (node as Record<string, unknown>)[key];
    // 中间节点缺失时创建空对象
    const clonedChild = child == null ? {} : clone(child);
    (node as Record<string, unknown>)[key] = clonedChild;
    node = clonedChild as Record<string, unknown> | unknown[];
  }
  (node as Record<string, unknown>)[parts[parts.length - 1]] = value;
  return newRoot;
}

/**
 * 轻量深比较（仅用于判断 initial 内容是否变化）。
 * 支持基本类型 / 对象 / 数组；不处理 Date 等特殊对象（结果数据为 JSON 结构）。
 */
function isDeepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) {
    if (a.length !== b.length) return false;
    return a.every((v, i) => isDeepEqual(v, b[i]));
  }
  if (a && b && typeof a === 'object' && typeof b === 'object') {
    const ka = Object.keys(a as Record<string, unknown>);
    const kb = Object.keys(b as Record<string, unknown>);
    if (ka.length !== kb.length) return false;
    return ka.every((k) =>
      isDeepEqual(
        (a as Record<string, unknown>)[k],
        (b as Record<string, unknown>)[k],
      ),
    );
  }
  return false;
}

export interface UseEditedDataResult<T> {
  /** 当前编辑副本（与 initial 解耦，编辑后 initial 变更不影响）。 */
  data: T | null;
  /** 是否有编辑（与 initial 不同的浅引用判断）。 */
  dirty: boolean;
  /** path 更新：updatePath('towers.0.reflux_structure.has_reflux', true)。 */
  updatePath: (path: string, value: unknown) => void;
  /** 整体替换（用于拓扑编辑器等外部已组装好的新对象）。 */
  update: (newData: T) => void;
  /** 重置回 initial。 */
  reset: () => void;
  /** 取当前编辑副本用于提交（dirty=false 时返回 initial）。 */
  getEdited: () => T | null;
}

export function useEditedData<T>(initial: T | null): UseEditedDataResult<T> {
  const initialRef = useRef<T | null>(initial);
  const [data, setData] = useState<T | null>(initial);

  // initial 内容变化时同步 data（如服务端推送新结果）。
  // 用深比较而非引用比较：页面每次渲染传"内容相同的新对象"不会误触发同步。
  useEffect(() => {
    if (!isDeepEqual(initialRef.current, initial)) {
      initialRef.current = initial;
      setData(initial);
    }
  }, [initial]);

  const dirty = data !== null && data !== initialRef.current;

  const updatePath = useCallback((path: string, value: unknown) => {
    setData((prev) => {
      const base = prev ?? initialRef.current;
      if (base === null) return prev;
      return setByPath(base, path, value);
    });
  }, []);

  const update = useCallback((newData: T) => {
    setData(newData);
  }, []);

  const reset = useCallback(() => {
    setData(initialRef.current);
  }, []);

  const getEdited = useCallback(() => data ?? initialRef.current, [data]);

  return useMemo(
    () => ({ data: data ?? initial, dirty, updatePath, update, reset, getEdited }),
    [data, initial, dirty, updatePath, update, reset, getEdited],
  );
}
