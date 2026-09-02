/**
 * Toast 轻提示：临时显示成功/错误消息。
 *
 * 用法：
 *   const { toast, show } = useToast();
 *   show('已保存', 'success');
 *   return <>{toast}</>
 */

import { useCallback, useState } from 'react';

export type ToastType = 'success' | 'error' | 'info';

export interface ToastState {
  id: number;
  type: ToastType;
  text: string;
}

export function useToast() {
  const [toasts, setToasts] = useState<ToastState[]>([]);

  const show = useCallback((text: string, type: ToastType = 'info') => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev, { id, type, text }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 3000);
  }, []);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const toast = toasts.length > 0 && (
    <div className="pointer-events-none fixed right-6 top-16 z-50 flex flex-col gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          className={`pointer-events-auto flex cursor-pointer items-center gap-2 rounded-lg px-4 py-2.5 text-sm text-white shadow-card backdrop-blur-xl transition hover:scale-[1.02] active:scale-[0.98] ${
            t.type === 'success'
              ? 'bg-green/95'
              : t.type === 'error'
                ? 'bg-red/95'
                : 'bg-accent/95'
          }`}
          onClick={() => dismiss(t.id)}
        >
          {t.type === 'success' && (
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <path d="M20 6L9 17l-5-5" />
            </svg>
          )}
          {t.type === 'error' && (
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
              <circle cx="12" cy="12" r="10" />
              <path d="M12 8v4M12 16h.01" />
            </svg>
          )}
          {t.text}
        </div>
      ))}
    </div>
  );

  return { toast, show };
}
