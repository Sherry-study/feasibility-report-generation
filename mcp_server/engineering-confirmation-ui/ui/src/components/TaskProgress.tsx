/**
 * 任务进度面板：流式展示工具执行过程。
 *
 * 在结果数据就绪前（status 非 waiting_review/completed/error），
 * 替代"等待执行完成…"占位，显示：
 *   - 当前状态大图标 + 动画
 *   - 事件时间线（流式滚动，最新在底部）
 *   - 错误详情（status=error 时）
 *
 * 自己订阅 useMcpApp + useReviewStatus，与 ProgressBanner 独立。
 */

import { useEffect, useRef } from 'react';
import { useMcpApp } from '@/core/mcpApp';
import { useReviewStatus } from '@/patterns/review';
import type { SessionEvent, SessionStatus } from '@/core/types';

const STATUS_LABEL: Record<SessionStatus, string> = {
  pending: '等待中',
  processing: '执行中',
  waiting_review: '等待审核',
  completed: '已完成',
  error: '执行失败',
};

/** 事件类型 -> 中文标签。 */
const EVENT_LABEL: Record<string, string> = {
  progress: '进度更新',
  task_completed: '执行完成',
  stage_update: '阶段更新',
  waiting_for_review: '等待审核',
  review_approved: '审核通过',
  review_rejected: '审核驳回',
  completed: '已完成',
  error: '错误',
  cancelled: '已取消',
};

/** 终态事件类型。 */
const TERMINAL_EVENT_TYPES = new Set([
  'completed', 'error', 'cancelled', 'review_approved', 'review_rejected',
]);

function formatTime(ts: number): string {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString('zh-CN', { hour12: false });
}

interface TaskProgressProps {}

export function TaskProgress({}: TaskProgressProps) {
  const app = useMcpApp();
  const status = useReviewStatus();
  const events = app.progressEvents;
  const scrollRef = useRef<HTMLDivElement>(null);

  // 新事件自动滚动到底部
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [events.length]);

  if (!status) {
    return (
      <div className="flex h-full items-center justify-center text-text-3">
        <Spinner />
        <span className="ml-3 text-sm">正在连接会话…</span>
      </div>
    );
  }

  const isTerminal = status === 'completed' || status === 'error';
  const isError = status === 'error';

  return (
    <div className="flex h-full flex-col overflow-hidden">
      {/* 顶部：当前状态 */}
      <div className="border-b border-border bg-bg-2 px-6 py-6">
        <div className="flex items-center gap-4">
          {!isTerminal && <Spinner size="lg" />}
          {isTerminal && !isError && (
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-green/10 text-green">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M20 6L9 17l-5-5" />
              </svg>
            </div>
          )}
          {isError && (
            <div className="flex h-10 w-10 items-center justify-center rounded-full bg-red/10 text-red">
              <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M18 6L6 18M6 6l12 12" />
              </svg>
            </div>
          )}
          <div>
            <div className="text-xl font-semibold tracking-tight text-text">
              {STATUS_LABEL[status]}
            </div>
            <div className="mt-0.5 flex items-center gap-2 text-xs text-text-3">
              <span className="font-mono">mcp-app</span>
            </div>
          </div>
        </div>
      </div>

      {/* 事件时间线 */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-6 py-4">
        {(() => {
          const visibleEvents = events.filter((e) => e.event !== 'stage_update');
          return visibleEvents.length === 0 ? (
            <div className="flex h-full items-center justify-center text-sm text-text-3">
              {isError ? '执行失败' : '正在执行，等待阶段事件…'}
            </div>
          ) : (
            <div className="flex flex-col gap-0">
              {visibleEvents.map((ev, i) => (
                <EventRow key={i} event={ev} isLast={i === visibleEvents.length - 1} />
              ))}
            </div>
          );
        })()}
      </div>

      {/* 错误详情 */}
      {isError && app.error && (
        <div className="max-h-40 overflow-auto bg-red/5 p-4">
          <pre className="whitespace-pre-wrap text-[11px] text-red">{app.error}</pre>
        </div>
      )}
    </div>
  );
}

/** 单条事件行：时间轴样式。 */
function EventRow({ event, isLast }: { event: SessionEvent; isLast: boolean }) {
  const label = EVENT_LABEL[event.event] ?? event.event;
  const isTerminal = TERMINAL_EVENT_TYPES.has(event.event);
  const isError = event.event === 'error';
  const isWaiting = event.event === 'waiting_for_review';

  const dotColor = isError
    ? 'bg-red'
    : isTerminal
      ? 'bg-green'
      : isWaiting
        ? 'bg-orange'
        : 'bg-accent';

  const message =
    (typeof event.data?.message === 'string' && event.data.message) ||
    (typeof event.data?.summary === 'string' && event.data.summary) ||
    '';

  // 额外信息（如节点数、页数等）
  const extraParts: string[] = [];
  const d = event.data ?? {};
  for (const key of ['count', 'total', 'processed']) {
    if (typeof d[key] === 'number') {
      extraParts.push(`${key}=${d[key]}`);
    }
  }

  return (
    <div className="flex gap-3 pb-4">
      {/* 时间轴线 */}
      <div className="flex flex-col items-center">
        <div className={`mt-1 h-2.5 w-2.5 rounded-full ${dotColor} ${!isTerminal ? 'animate-pulse' : ''}`} />
        {!isLast && <div className="w-px flex-1 bg-border" />}
      </div>
      {/* 内容 */}
      <div className={`flex-1 ${isLast ? 'pb-0' : 'pb-1'}`}>
        <div className="flex items-center gap-2">
          <span className="font-mono text-[10px] text-text-3">{formatTime(event.timestamp)}</span>
          <span className={`text-xs font-semibold ${isError ? 'text-red' : isTerminal ? 'text-green' : isWaiting ? 'text-orange' : 'text-text'}`}>
            {label}
          </span>
          <span className="rounded bg-bg-3/60 px-1.5 py-0.5 font-mono text-[9px] text-text-3">
            {event.event}
          </span>
        </div>
        {message && (
          <div className="mt-0.5 text-xs text-text-2">{message}</div>
        )}
        {extraParts.length > 0 && (
          <div className="mt-0.5 font-mono text-[10px] text-text-3">
            {extraParts.join(' · ')}
          </div>
        )}
      </div>
    </div>
  );
}

/** 加载动画。 */
function Spinner({ size = 'md' }: { size?: 'md' | 'lg' }) {
  const dim = size === 'lg' ? 'h-8 w-8' : 'h-4 w-4';
  return (
    <svg className={`${dim} animate-spin text-accent`} viewBox="0 0 24 24" fill="none">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
    </svg>
  );
}
