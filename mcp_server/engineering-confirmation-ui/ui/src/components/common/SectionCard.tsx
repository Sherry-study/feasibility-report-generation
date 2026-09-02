import type { ReactNode } from 'react';

/** 卡片容器。accent 仅在语义场景（orange 警告 / red 错误）影响标题颜色。 */
export function SectionCard({
  title,
  accent = 'accent',
  count,
  action,
  children,
  className = '',
}: {
  title: string;
  accent?: 'accent' | 'green' | 'orange' | 'purple' | 'cyan' | 'yellow' | 'red';
  count?: number;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const titleColor: Record<string, string> = {
    accent: 'text-text',
    green: 'text-text',
    orange: 'text-orange',
    purple: 'text-text',
    cyan: 'text-text',
    yellow: 'text-text',
    red: 'text-red',
  };
  return (
    <section className={`rounded-lg bg-bg-2 p-4 ${className}`}>
      <h3 className={`mb-3 flex items-center gap-2 text-[15px] font-semibold tracking-tight ${titleColor[accent]}`}>
        {title}
        {count !== undefined && <span className="text-[12px] font-normal text-text-3">（{count}）</span>}
        {action && <span className="ml-auto">{action}</span>}
      </h3>
      {children}
    </section>
  );
}
