/**
 * 应用根组件 -- MCP Apps 架构。
 *
 * 单工具单项目:本 UI 只服务 report_generation 一个工具,直接渲染 ReportGenerationPage。
 * 启动时通过 useMcpInitialize 发起 ui/initialize 握手。
 */

import { useMcpApp, useMcpInitialize } from '@/core/mcpApp';
import { Layout } from '@/components/Layout';
import { LoadingScreen } from '@/components/LoadingScreen';
import { ErrorBoundary } from '@/components/ErrorBoundary';
import { useTheme } from '@/hooks/useTheme';
import { ReportGenerationPage } from '@/pages/ReportGenerationPage';

export default function App() {
  useMcpInitialize();
  useTheme();
  const app = useMcpApp();

  if (app.status === 'initializing') {
    return (
      <Layout>
        <ErrorBoundary>
          <LoadingScreen message="正在连接 host" />
        </ErrorBoundary>
      </Layout>
    );
  }

  if (app.status === 'error' && !app.toolResult) {
    return (
      <Layout>
        <div className="flex h-full items-center justify-center">
          <div className="text-sm text-red">连接失败:{app.error}</div>
        </div>
      </Layout>
    );
  }

  if (!app.toolInput) {
    return (
      <Layout>
        <LoadingScreen message="等待任务" />
      </Layout>
    );
  }

  return (
    <ErrorBoundary>
      <div className="h-full animate-fade-in">
        <ReportGenerationPage />
      </div>
    </ErrorBoundary>
  );
}
