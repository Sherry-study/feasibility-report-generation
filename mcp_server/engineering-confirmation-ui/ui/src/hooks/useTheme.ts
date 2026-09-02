/**
 * 主题 hook -- 根据 hostContext.theme 应用暗色/亮色主题。
 *
 * host 在 ui/initialize 握手时返回 theme，支持 'light' | 'dark' | 'system'。
 */
import { useEffect } from 'react';
import { useMcpApp } from '@/core/mcpApp';

export function useTheme(): void {
  const app = useMcpApp();

  useEffect(() => {
    const theme = app.hostContext?.theme ?? 'light';
    const root = document.documentElement;

    if (theme === 'dark') {
      root.classList.add('dark');
    } else if (theme === 'light') {
      root.classList.remove('dark');
    } else {
      // system: 跟随系统偏好
      const mq = window.matchMedia('(prefers-color-scheme: dark)');
      root.classList.toggle('dark', mq.matches);
      const handler = (e: MediaQueryListEvent) => {
        root.classList.toggle('dark', e.matches);
      };
      mq.addEventListener('change', handler);
      return () => mq.removeEventListener('change', handler);
    }
  }, [app.hostContext?.theme]);
}
