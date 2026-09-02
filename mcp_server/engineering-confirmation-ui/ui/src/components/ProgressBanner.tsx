/**
 * 进度横幅：显示当前会话状态 + 提取事件流。
 *
 * 用于审核页面顶部，让用户看到"正在提取 / 等待审核 / 已完成"。
 */

import { useMcpApp } from '@/core/mcpApp';
import { useReviewStatus } from '@/patterns/review';
import type { SessionStatus } from '@/core/types';

const STATUS_LABEL: Record<SessionStatus, string> = {
  pending: '等待中',
  processing: '执行中',
  waiting_review: '等待审核',
  completed: '已完成',
  error: '执行失败',
};

const STATUS_COLOR: Record<SessionStatus, string> = {
  pending: 'bg-bg-3 text-text-3',
  processing: 'bg-accent/10 text-accent',
  waiting_review: 'bg-orange/10 text-orange',
  completed: 'bg-green/10 text-green',
  error: 'bg-red/10 text-red',
};

interface ProgressBannerProps {}

export function ProgressBanner({}: ProgressBannerProps) {
  const app = useMcpApp();
  const status = useReviewStatus();
  const events = app.progressEvents;

  if (!status) return null;

  const lastEv = events.filter((e) => e.event !== 'stage_update')[events.length - 1];
  const isTerminal = status === 'completed' || status === 'error';

  return (
    <div className="glass border-b border-border px-4 py-2">
      <div className="flex items-center gap-3">
        <span
          className={`inline-flex items-center gap-1.5 rounded-pill px-2.5 py-1 text-xs font-semibold ${STATUS_COLOR[status]}`}
        >
          <span
            className={`h-1.5 w-1.5 rounded-full ${
              isTerminal ? 'bg-current' : 'bg-current animate-pulse'
            }`}
          />
          {STATUS_LABEL[status]}
        </span>
        <div className="flex-1" />
        {lastEv && (
          <span className="truncate text-xs font-mono text-text-3" title={lastEv.event}>
            <span className="font-mono text-text-2">{lastEv.event}</span>
            {typeof lastEv.data?.message === 'string' && (
              <span className="ml-2 text-text-3">{lastEv.data.message}</span>
            )}
          </span>
        )}
      </div>
      {status === 'error' && app.error && (
        <pre className="mt-2 max-h-24 overflow-auto rounded-lg bg-red/5 p-2 text-[11px] text-red">
          {app.error}
        </pre>
      )}
    </div>
  );
}
