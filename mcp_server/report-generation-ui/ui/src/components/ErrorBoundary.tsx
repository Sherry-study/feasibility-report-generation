/**
 * React ErrorBoundary -- 捕获子组件渲染错误，显示错误提示而非白屏。
 */
import { Component, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo): void {
    console.error('[ErrorBoundary]', error, info.componentStack);
  }

  render(): ReactNode {
    if (this.state.hasError) {
      return (
        <div className="flex h-full flex-col items-center justify-center gap-4 p-8">
          <div className="flex h-12 w-12 items-center justify-center rounded-full bg-red/10 text-red">
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </div>
          <div className="text-center">
            <div className="text-sm font-semibold text-text">页面渲染出错</div>
            <pre className="mt-2 max-w-md overflow-auto rounded-lg bg-red/5 p-3 text-[11px] text-red/80">
              {this.state.error?.message ?? '未知错误'}
            </pre>
          </div>
          <button
            className="rounded-lg bg-accent px-4 py-2 text-xs font-semibold text-white hover:bg-accent/90"
            onClick={() => window.location.reload()}
          >
            重新加载
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}
