import { useState, cloneElement, type ReactElement } from 'react';
import { JsonViewer } from './JsonViewer';

/** 统一的 JSON 查看器：默认右上角浮动按钮 + 底部抽屉。可通过 trigger 自定义触发按钮。 */
export function JsonDrawer({ data, disabled, trigger }: { data: unknown; disabled?: boolean; trigger?: ReactElement }) {
  const [mounted, setMounted] = useState(false);
  const [open, setOpen] = useState(false);
  if (disabled) return null;

  const handleOpen = () => {
    setMounted(true);
    requestAnimationFrame(() => setOpen(true));
  };

  const handleClose = () => {
    setOpen(false);
    setTimeout(() => setMounted(false), 300);
  };

  const handleExport = () => {
    const jsonStr = JSON.stringify(data, null, 2);
    const blob = new Blob([jsonStr], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `export_${new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')}.json`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  // 有自定义 trigger 时用 absolute（相对画布容器），否则用 fixed（相对视口）
  const overlayPos = trigger ? 'absolute inset-0' : 'fixed inset-0';

  return (
    <>
      {trigger
        ? cloneElement(trigger, { onClick: handleOpen })
        : (
          <button
            type="button"
            title="查看 JSON"
            onClick={handleOpen}
            className="glass fixed right-4 top-4 z-30 flex h-9 w-9 items-center justify-center rounded-lg border border-border shadow-card text-text-2 hover:text-accent"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M8 3H7a2 2 0 00-2 2v14a2 2 0 002 2h1M16 3h1a2 2 0 012 2v14a2 2 0 01-2 2h-1" />
            </svg>
          </button>
        )}
      {mounted && (
        <div className={`${overlayPos} z-40 flex flex-col justify-end`} onClick={handleClose}>
          <div className={`absolute inset-0 bg-black/30 transition-opacity duration-300 ${open ? 'opacity-100' : 'opacity-0'}`} />
          <div
            className={`relative max-h-[70%] overflow-auto rounded-t-xl bg-bg shadow-card transition-transform duration-300 ease-out ${open ? 'translate-y-0' : 'translate-y-full'}`}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="glass-strong sticky top-0 flex items-center justify-between border-b border-border px-4 py-2">
              <span className="text-xs font-semibold text-text">原始 JSON</span>
              <div className="flex items-center gap-2">
                <button
                  onClick={handleExport}
                  title="导出为 JSON 文件"
                  className="flex items-center gap-1 rounded bg-bg-3 px-2 py-0.5 text-[10px] text-text-2 hover:bg-bg-3/80"
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3" />
                  </svg>
                  导出
                </button>
                <button onClick={handleClose} className="text-text-3 hover:text-text">✕</button>
              </div>
            </div>
            <div className="p-3">
              <JsonViewer data={data} initialExpandDepth={2} />
            </div>
          </div>
        </div>
      )}
    </>
  );
}
