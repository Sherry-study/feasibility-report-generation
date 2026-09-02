/**
 * 全屏加载动画 -- 用于握手连接中 / 等待任务时。
 *
 * 视觉：雷达脉动环 + 中央 PFD 流程图徽标 + 弹跳圆点。
 */

interface LoadingScreenProps {
  /** 提示文案。 */
  message?: string;
}

export function LoadingScreen({ message = '等待任务' }: LoadingScreenProps) {
  return (
    <div className="relative flex h-full items-center justify-center overflow-hidden bg-bg">
      <div className="relative flex flex-col items-center">
        {/* 雷达脉动环 + 中央徽标 */}
        <div className="relative mb-6 flex h-20 w-20 items-center justify-center">
          {/* 三层扩散环（错开延迟形成涟漪） */}
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent/15" />
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent/10 [animation-delay:400ms]" />
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-accent/5 [animation-delay:800ms]" />

          {/* 中央徽标 */}
          <div className="relative flex h-12 w-12 items-center justify-center rounded-xl bg-accent shadow-card animate-breathe">
            <svg
              className="h-6 w-6 text-white"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              {/* PFD 流程图图标：三节点 + 连线 */}
              <circle cx="5" cy="6" r="2" />
              <circle cx="19" cy="6" r="2" />
              <circle cx="12" cy="18" r="2" />
              <path d="M7 6h10" />
              <path d="M6 8l5 8" />
              <path d="M18 8l-5 8" />
            </svg>
          </div>
        </div>

        {/* 文案 + 弹跳圆点 */}
        <div className="flex items-center gap-1.5 text-sm font-normal tracking-tight text-text-3">
          <span>{message}</span>
          <span className="flex items-center gap-1">
            <span className="inline-block h-1 w-1 animate-dot-bounce rounded-full bg-current" />
            <span className="inline-block h-1 w-1 animate-dot-bounce rounded-full bg-current [animation-delay:160ms]" />
            <span className="inline-block h-1 w-1 animate-dot-bounce rounded-full bg-current [animation-delay:320ms]" />
          </span>
        </div>
      </div>
    </div>
  );
}
