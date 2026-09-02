/**
 * 通用撤销/重做历史栈。
 *
 * 编辑器在每次"提交级"变更（拖拽结束、增删、属性修改、折点变更）后调用 commit()。
 * 连续的中间态（如拖拽过程中的每一帧）不应调用 commit，只更新 present。
 *
 * 用法：
 *   const hist = useHistory<{ nodes: N[]; edges: E[] }>(initial);
 *   hist.commit({ nodes, edges });   // 保存当前快照到历史
 *   hist.undo();                      // 回退
 */

import { useCallback, useMemo, useRef, useState } from 'react';

export interface HistorySnapshot<T> {
  past: T[];
  present: T;
  future: T[];
}

export function useHistory<T>(initial: T) {
  const [state, setState] = useState<HistorySnapshot<T>>({
    past: [],
    present: initial,
    future: [],
  });
  // 标记：为 true 时跳过 commit（避免 undo/redo 自身触发又一次 commit）
  const skipRef = useRef(false);

  /** 重置整个历史（加载新数据时调用）。 */
  const reset = useCallback((next: T) => {
    skipRef.current = true;
    setState({ past: [], present: next, future: [] });
  }, []);

  /**
   * 提交一个快照。会把"上一次 present"压入 past 栈，清空 future。
   * 注意：传入的应为变更完成后的最新状态。
   */
  const commit = useCallback((next: T) => {
    if (skipRef.current) {
      skipRef.current = false;
      return;
    }
    setState((s) => ({ past: [...s.past, s.present].slice(-100), present: next, future: [] }));
  }, []);

  /** 撤销，返回新的 present（或 null 表示无法撤销）。 */
  const undo = useCallback((): T | null => {
    let result: T | null = null;
    setState((s) => {
      if (s.past.length === 0) return s;
      const prev = s.past[s.past.length - 1];
      result = prev;
      skipRef.current = true;
      return {
        past: s.past.slice(0, -1),
        present: prev,
        future: [s.present, ...s.future].slice(0, 100),
      };
    });
    return result;
  }, []);

  /** 重做，返回新的 present（或 null 表示无法重做）。 */
  const redo = useCallback((): T | null => {
    let result: T | null = null;
    setState((s) => {
      if (s.future.length === 0) return s;
      const next = s.future[0];
      result = next;
      skipRef.current = true;
      return {
        past: [...s.past, s.present],
        present: next,
        future: s.future.slice(1),
      };
    });
    return result;
  }, []);

  return useMemo(
    () => ({
      present: state.present,
      canUndo: state.past.length > 0,
      canRedo: state.future.length > 0,
      commit,
      undo,
      redo,
      reset,
    }),
    [state, commit, undo, redo, reset],
  );
}
