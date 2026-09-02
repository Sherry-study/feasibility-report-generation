/** 分类错误横幅：区分网络/工具/数据格式错误，提供重试与复制。 */
export function ErrorBanner({
  error,
  onRetry,
}: {
  error: string;
  onRetry?: () => void;
}) {
  const kind = classifyError(error);
  const label = { network: '网络错误', tool: '工具执行错误', data: '数据格式错误', unknown: '错误' }[kind];

  return (
    <div className="m-4 rounded-lg bg-red/5 p-4">
      <div className="flex items-center gap-2">
        <span className="rounded bg-red/10 px-2 py-0.5 text-[10px] font-bold text-red">{label}</span>
        <span className="text-sm font-semibold text-red">提取失败</span>
        <div className="ml-auto flex gap-2">
          {onRetry && (
            <button
              className="rounded bg-red/10 px-3 py-1.5 text-[11px] text-red hover:bg-red/20"
              onClick={onRetry}
            >
              重试提取
            </button>
          )}
          <button
            className="rounded bg-bg-3 px-3 py-1.5 text-[11px] text-text-2 hover:bg-bg-3/80"
            onClick={() => navigator.clipboard.writeText(error)}
          >
            复制错误
          </button>
        </div>
      </div>
      <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap text-[11px] text-red/80">{error}</pre>
    </div>
  );
}

function classifyError(msg: string): 'network' | 'tool' | 'data' | 'unknown' {
  const lower = msg.toLowerCase();
  if (lower.includes('network') || lower.includes('timeout') || lower.includes('超时') || lower.includes('连接')) return 'network';
  if (lower.includes('json') || lower.includes('parse') || lower.includes('格式')) return 'data';
  if (lower.includes('tool') || lower.includes('工具') || lower.includes('执行')) return 'tool';
  return 'unknown';
}
