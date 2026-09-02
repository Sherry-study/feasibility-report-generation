/**
 * 全局布局：主内容区。
 */

import type { ReactNode } from 'react';

interface LayoutProps {
  /** 主内容。 */
  children: ReactNode;
}

export function Layout({ children }: LayoutProps) {
  return <main className="h-full overflow-hidden bg-bg">{children}</main>;
}
