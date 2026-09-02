/**
 * 审核工具栏：通过/驳回按钮 + 驳回原因输入。
 *
 * 所有审核页面共用。状态由父组件控制（disabled / submitting / submitted）。
 */

import { useState } from 'react';

/** 驳回快捷原因（改成你的业务原因，留空则不显示）。 */
const REJECT_QUICK_REASONS: string[] = [];

interface ReviewToolbarProps {
  /** 是否可提交（waiting_review 状态时为 true）。 */
  canSubmit: boolean;
  /** 是否提交中。 */
  submitting: boolean;
  /** 已提交的结果消息（成功或失败）。 */
  resultMessage?: { type: 'success' | 'error'; text: string } | null;
  /** 提交通过。 */
  onApprove: () => void;
  /** 提交驳回（带原因）。 */
  onReject: (reason: string) => void;
  /** 状态提示文案（覆盖默认逻辑）。 */
  statusHint?: string;
  /** 是否有未保存编辑。 */
  dirty?: boolean;
  /** 重置编辑回调。 */
  onReset?: () => void;
}

export function ReviewToolbar({
  canSubmit,
  submitting,
  resultMessage,
  onApprove,
  onReject,
  statusHint,
  dirty,
  onReset,
}: ReviewToolbarProps) {
  const [rejectMode, setRejectMode] = useState(false);
  const [reason, setReason] = useState('');

  const handleReject = () => {
    if (!reason.trim()) {
      return;
    }
    onReject(reason.trim());
    setReason('');
    setRejectMode(false);
  };

  return (
    <div className="glass border-t border-border px-4 py-3">
      {resultMessage && (
        <div
          className={`mb-2 rounded-lg p-3 text-xs ${
            resultMessage.type === 'success'
              ? 'bg-green/5 text-green'
              : 'bg-red/5 text-red'
          }`}
        >
          {resultMessage.text}
        </div>
      )}
      {rejectMode ? (
        <div className="flex flex-col gap-2">
          {/* 快捷原因 chips */}
          <div className="flex flex-wrap gap-1.5">
            {REJECT_QUICK_REASONS.map((r) => (
              <button
                key={r}
                type="button"
                className="rounded-pill border border-border bg-bg-2 px-3 py-1 text-[11px] text-text-2 hover:border-accent hover:text-accent"
                onClick={() => setReason((prev) => (prev ? `${prev}；${r}` : r))}
              >
                {r}
              </button>
            ))}
          </div>
          <textarea
            className="min-h-[64px] w-full resize-y rounded border border-border bg-bg-2 px-3 py-2 text-sm text-text outline-none focus:border-accent focus:ring-2 focus:ring-accent/20"
            placeholder="请输入驳回原因（必填，可点击上方快捷原因补充）"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            onKeyDown={(e) => {
              if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
                e.preventDefault();
                handleReject();
              }
            }}
            autoFocus
          />
          <div className="flex items-center justify-end gap-2">
            <span className="mr-auto text-[10px] text-text-3">
              {reason.length} 字{reason.trim() ? '' : '（必填）'}
            </span>
            <button
              className="rounded px-3 py-2 text-xs text-text-2 hover:bg-bg-3 disabled:opacity-50"
              onClick={() => {
                setRejectMode(false);
                setReason('');
              }}
              disabled={submitting}
            >
              取消
            </button>
            <button
              className="rounded bg-red px-3 py-2 text-xs font-semibold text-white hover:bg-red/90 disabled:opacity-50"
              onClick={handleReject}
              disabled={submitting || !reason.trim()}
            >
              {submitting ? '提交中…' : '确认驳回 (Ctrl+Enter)'}
            </button>
          </div>
        </div>
      ) : (
        <div className="flex items-center justify-end gap-2">
          <span className="mr-auto text-xs text-text-3">
            {statusHint ?? (canSubmit
              ? '审核数据已就绪，请审核后提交'
              : '提取进行中…')}
          </span>
          {dirty && canSubmit && onReset && (
            <button
              className="rounded px-3 py-2 text-xs text-text-2 hover:bg-bg-3"
              onClick={() => {
                if (confirm('确认放弃当前编辑？')) onReset();
              }}
              disabled={submitting}
            >
              重置编辑
            </button>
          )}
          {dirty && canSubmit && (
            <span className="rounded bg-orange/10 px-1.5 py-1 text-[10px] text-orange">
              未保存
            </span>
          )}
          <button
            className="rounded px-4 py-2 text-xs font-semibold text-red hover:bg-red/10 disabled:opacity-40"
            onClick={() => setRejectMode(true)}
            disabled={!canSubmit || submitting}
          >
            驳回
          </button>
          <button
            className="rounded bg-accent px-4 py-2 text-xs font-semibold text-white hover:bg-accent/90 disabled:opacity-40"
            onClick={onApprove}
            disabled={!canSubmit || submitting}
          >
            {submitting ? '提交中…' : '通过审核'}
          </button>
        </div>
      )}
    </div>
  );
}
