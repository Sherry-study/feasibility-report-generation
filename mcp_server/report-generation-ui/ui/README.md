# MCP Apps UI 模板（progress-ui）

progress_tool 工具的 UI 项目。**单工具单项目**：`src/App.tsx` 直接渲染 `ProgressPage`，无工具名路由（无 `PAGES` / `resolveToolName` / `window.__MCP_TOOL_NAME__`）。

## 适用范围

本模板适用于：**MCP 工具需要在宿主(如 Claude Desktop)中渲染交互式界面**的场景。这是三种复杂度中的中等一种——工具耗时较长、需要展示执行进度，但不需要人工审核。

具体特征：
- 工具执行过程中调用 `ctx.report_progress()` 推送进度（含 `progress` / `total` / `message`）
- 可选通过 `uiEvent` 扩展字段携带中间数据
- 工具执行完成后同步返回结构化结果（无 `review_pending` 阻塞、无反向调用 `submit_review`）

> 同类还有两个兄弟模板项目（按需选用）：
> - `mcp-apps-ui/simple-ui/` — 同步返回，无进度推送
> - `mcp-apps-ui/review-ui/`   — 进度 + 人工审核 + 提交

> **使用模型：一个工具 = 一个独立项目 = 一个 `ui://` 资源。** 宿主执行一个工具时创建 iframe 展示该工具的 view，所以每个工具用独立的 ui 项目，`App.tsx` 直接渲染该工具的页面。服务端为该工具绑定一个独立的 `ui://` 资源（见 `../server_example/server.py` 中 `progress_tool` 的注册方式）。

## 快速开始

### 1. 构建 UI

```bash
cd mcp-apps-ui/progress-ui/ui
npm install
npm run build        # 生成 dist/index.html
```

> **注意：host 控制台加载的是 `dist/index.html` 构建产物。每次改 UI 代码后必须重新 `npm run build`，否则改动不会生效——这是最常见的困惑点。**

### 2. 启动 host 测试控制台

```bash
cd mcp-apps-ui/progress-ui/host
npm install
npm run dev          # 打开 http://localhost:5184
```

在控制台点击"自动播放"，即可看到完整流程（3 步进度推送 -> 结果展示）。

### 3. 启动服务端示例（可选）

```bash
pip install fastmcp>=3.4.4
cd mcp-apps-ui       # 在 mcp-apps-ui 根目录执行，server_example 与 progress-ui 同级
python -m server_example.server    # http://127.0.0.1:8000
```

服务端示例演示了如何注册 UI 资源、推送进度。详见 `../server_example/README.md`。

## 目录结构

```
mcp-apps-ui/
├── progress-ui/                  # 本项目（progress_tool 专用 UI）
│   ├── ui/                       # MCP Apps UI 前端
│   │   ├── src/
│   │   │   ├── core/                # 通用层(请勿修改)
│   │   │   │   ├── mcpApp.ts        #   MCP postMessage 通信核心(单例 + hooks)
│   │   │   │   ├── types.ts         #   通用类型定义(SessionStatus / SubmitReviewRequest 等)
│   │   │   │   └── hooks/           #   useEditedData / useDirtyGuard / useHistory
│   │   │   ├── patterns/            # 业务模式层(按领域拆分，与 core 解耦)
│   │   │   │   ├── review/          #   审核工作流专用(本模板未直接使用，ProgressBanner 间接引用 useReviewStatus，保留备用)
│   │   │   │   └── image/           #   图片读取专用(本模板未使用，保留备用)
│   │   │   ├── components/          # 通用组件
│   │   │   │   ├── Layout / LoadingScreen / Toast / ErrorBanner / PageHeader
│   │   │   │   ├── TaskProgress / TaskWithImage / ProgressBanner
│   │   │   │   ├── ReviewToolbar / JsonDrawer / JsonViewer
│   │   │   │   ├── ErrorBoundary    #   渲染错误兜底(防白屏)
│   │   │   │   └── common/          #   SectionCard / InfoCell
│   │   │   ├── pages/               # 业务页面(本模板只有 ProgressPage)
│   │   │   │   └── ProgressPage.tsx
│   │   │   ├── hooks/useTheme.ts    # 主题响应(根据 hostContext.theme 切换 .dark)
│   │   │   └── App.tsx              # 根组件(直接渲染 ProgressPage，无路由)
│   │   └── README.md                # 本文件
│   └── host/                     # 简化版测试控制台
│       └── src/mockData.ts          #   mock 数据(改成你的业务)
├── simple-ui/                    # 兄弟模板（simple_tool）
├── review-ui/                    # 兄弟模板（review_tool）
└── server_example/               # 服务端最小示例(Python FastMCP，三个工具各绑定一个 ui:// 资源)
    ├── server.py
    └── README.md
```

> **标有"请勿修改"的是通用层，直接用即可；标有"改成你的业务"的是示例，需要替换成你的业务逻辑。**
> 业务专属逻辑放 `patterns/<领域>/`，不要塞进 core。

## 改成你的业务(5 步)

### Step 1: 定义结果类型

在 `src/pages/ProgressPage.tsx` 中定义你的工具返回结构：

```typescript
interface YourResult {
  // 改成你的工具返回字段
  items?: Array<{ id: string; name: string; value: string }>;
  warnings?: string[];
}
```

### Step 2: 对齐进度步骤与中间数据

打开工具函数体，逐条对齐 `ctx.report_progress()` 调用：

- **步骤数**：`ProgressPage` 中渲染的进度阶段数 == 工具中 `report_progress` 的调用次数
- **每步 message**：对齐工具实际推送的 `message` 字符串（用于 `TaskProgress` 时间线展示）
- **中间数据**：工具是否在 `progress` 中携带 `uiEvent` 扩展字段？若有，在 `ProgressPage` 中通过 `useMcpApp().progressEvents` 读取并展示

### Step 3: 改页面渲染

把 `ProgressPage.tsx` 改名为 `YourPage.tsx`（或直接在原文件改），替换：
- 结果类型(`ProgressResult` -> `YourResult`)
- 渲染逻辑(展示你的业务数据，含进度阶段 + 结果区)

### Step 4: 改 App.tsx 指向你的页面

`src/App.tsx` 默认 import `ProgressPage`，改名后更新 import 即可：

```typescript
// 默认：
import { ProgressPage } from '@/pages/ProgressPage';
// 改成：
import { YourPage } from '@/pages/YourPage';

// 渲染处：<ProgressPage /> 改成 <YourPage />
```

> **无 `PAGES` / `resolveToolName`。** 单工具单项目，UI 直接渲染该工具的页面。

### Step 5: 改 mock 数据

编辑 `../host/src/mockData.ts`，按工具真实执行流程调整：
- `toolName` 改成你的工具名
- `steps` 数量 / `progress` / `total` / `message` 对齐工具的 `report_progress` 调用
- `uiEvent` 字段对齐工具实际推送的扩展数据
- `finalResult` 改成你的工具真实返回结构
- `skipReview: true`（progress 工具无需审核）

### Step 6: 服务端注册 UI 资源

在 MCP server 中为该工具注册独立的 `ui://` 资源并绑定（参考 `../server_example/server.py` 中 `progress_tool` 的写法）：

```python
@mcp.resource("ui://mcp-app-ui/your_tool/index.html")
def get_your_tool_ui_html() -> str:
    with open("path/to/your_tool_ui/dist/index.html") as f:
        return f.read()

@mcp.tool(app=AppConfig(resource_uri="ui://mcp-app-ui/your_tool/index.html"))
async def your_tool(ctx: Context, ...):
    # 按需调用 ctx.report_progress(progress, total, message)
    ...
```

## 核心 API 参考

### core 层(`@/core/mcpApp`)

| Hook | 用途 |
|---|---|
| `useMcpApp()` | 订阅全局状态(status / hostContext / toolInput / toolResult / progress / progressEvents / error / callToolLoading) |
| `useToolInput()` | 获取工具入参(toolName + args) |
| `useNormalizedToolResult()` | 获取归一化工具结果(toolResult 优先) |
| `useCallToolLoading()` | 反向工具调用(tools/call)是否进行中 |
| `useMcpInitialize()` | 启动时握手(放 App 根组件) |

单例方法(`import { mcpApp } from '@/core/mcpApp'`)：`callTool(name, args)` 反向调用工具、`cancelCurrentTool()` 请求取消当前工具。

扩展点：`registerProgressEventResolver(fn)` 注册 progress 事件名解析器（业务模式在模块加载时调用，无需改 core；返回注销函数）。审核模式已内置 `patterns/review` 的注册，自定义事件名示例：

```typescript
import { registerProgressEventResolver } from '@/core/mcpApp';
registerProgressEventResolver((uiEvent) => (uiEvent?.my_flag ? 'my_event' : null));
```

### patterns/review(`@/patterns/review`) — 审核模式专用

本模板未直接使用，但 `components/ProgressBanner.tsx` import 了 `useReviewStatus` 用于派生 `SessionStatus`（间接通过 `patterns/review/reviewEvents` 注册事件名解析器）。无需手动 import，构建时已通过 `ProgressBanner` 的引用自动注册。

| Hook/函数 | 用途 |
|---|---|
| `useReviewStatus()` | 派生审核状态(SessionStatus) |
| `useReviewId()` | 获取当前审核会话 id |
| `useReviewResult()` | 获取结果(toolResult 为 null 时用 progress.uiEvent.final_result 兜底，审核阻塞期也能拿到数据) |
| `submitReview(id, body)` | 提交审核(反向调用 submit_review 工具，唤醒阻塞的审核工具) |

### patterns/image(`@/patterns/image`) — 图片读取专用

| Hook | 用途 |
|---|---|
| `useToolImage()` | 通过 read_image 工具读取工具相关图片为 data URL |
| `useSourceImage(path)` | 读取单张图片 |

### hooks/ 通用 hook

| Hook | 用途 |
|---|---|
| `useEditedData(initial)` | 管理编辑态数据(原始 + 编辑副本 + dirty 标记 + 路径更新)。**注意：initial 必须引用稳定，见"常见坑"** |
| `useDirtyGuard(dirty)` | 离开页面前拦截(脏数据警告) |
| `useHistory(initial)` | undo/redo 历史栈 |

### 通用组件

| 组件 | 用途 |
|---|---|
| `Layout` | 页面外壳(全屏布局) |
| `LoadingScreen` | 加载动画(带消息) |
| `PageHeader` | 页面标题栏(图标 + 标题 + 副标题) |
| `ProgressBanner` | 进度横幅(状态 + 事件流，页面顶部) |
| `TaskProgress` | 执行进度面板(状态 + 事件时间线) |
| `TaskWithImage` | 执行阶段布局(左侧源图 + 右侧进度) |
| `ReviewToolbar` | 审核工具栏(通过/驳回 + 状态提示) |
| `ErrorBanner` | 错误提示(含重试) |
| `ErrorBoundary` | 渲染错误兜底(防白屏) |
| `Toast` | 轻提示 |
| `JsonDrawer` | JSON 抽屉(查看原始数据) |
| `JsonViewer` | JSON 查看器 |
| `common/SectionCard` | 区块卡片(标题 + 内容) |
| `common/InfoCell` | 元信息格(label + value + 高亮色) |

## 后端协议约定

progress_tool 通过标准 MCP progress 协议推送进度（无需 `review_pending` 扩展）：

```
工具执行中 -> 通过 ctx.report_progress() 或 send_progress_with_data 推送:
  progress: 0..N       # 当前进度
  total: N              # 总步数
  message: "..."        # 阶段描述
  uiEvent?: { ... }     # 可选业务扩展字段（如 { event_type: 'parsed', item_count: 3 }）
```

UI 通过 `useMcpApp().progressEvents` 读取所有 progress 事件（含 `uiEvent`），在 `TaskProgress` 中按时间线展示。

## 常见坑

1. **改 UI 不生效** -> 先 `npm run build` 再刷新控制台。
2. **进度步骤数与工具不一致** -> 没有通读工具函数体就写 UI。检查 `report_progress` 的实际调用次数，mock 数据的 `steps` 数量必须一致。
3. **`useEditedData` 的 initial** -> 已内置深比较防护，每次渲染传新对象字面量也不会死循环。但为减少每次渲染的深比较开销，建议用 `useMemo` 保持引用稳定：
   ```typescript
   const initial = useMemo(() => transform(result), [result]);
   const { data, updatePath } = useEditedData(initial);
   ```
4. **页面显示空白或错页面** -> `src/App.tsx` 的 import 指向了已删除的页面文件；检查 import 是否指向当前 `pages/` 下实际存在的文件。
5. **反向调用报"未实现工具 xxx"** -> host 未实现该工具的 `tools/call`。demo host 只 mock 了 `read_image` / `submit_review`，新增反向调用需在 `../host/src/hostProtocol.ts` 加 mock 分支，并在服务端实现。
6. **`structuredContent` 是可选字段** -> 工具未返回结构化结果时为 undefined，渲染前必须判空。
7. **UI 与工具实际逻辑脱节** -> 没有通读工具函数体就写 UI，导致进度步骤/中间数据/结果展示与工具实际行为不一致。**必须先完成"改成你的业务"Step 1-2 的执行逻辑映射再写代码。**
8. **服务端 resource_uri 不匹配** -> 工具的 `AppConfig(resource_uri=...)` 必须与对应的 `@mcp.resource("ui://...")` 完全一致，且指向该工具对应的 ui 项目构建产物。

## MCP Apps 协议简介

UI 作为 iframe 被 host 加载，通过 postMessage(JSON-RPC 2.0)通信：

```
host                          ui (iframe)
 │                              │
 │  1. ui/initialize 握手        │
 │ ──────────────────────────>   │
 │  <─────────────────────────   │
 │  2. initialized 通知          │
 │ <──────────────────────────   │
 │                              │
 │  3. tool-input(工具入参)     │
 │ ──────────────────────────>   │
 │  4. progress(进度,多次)      │
 │ ──────────────────────────>   │
 │  5. tool-result(最终结果)    │
 │ ──────────────────────────>   │
```

- **步骤 1-2**：握手，UI 发起 `ui/initialize`，host 返回 HostContext(含 theme)，UI 回发 `initialized`
- **步骤 3**：host 发送工具入参(toolName + args)，UI 开始显示"执行中"
- **步骤 4**：host 逐步发送 progress 通知(含进度 + 业务扩展字段 uiEvent)，UI 实时渲染 `TaskProgress`
- **步骤 5**：host 发送 tool-result(最终结果)，UI 显示完成

核心：所有数据通过 postMessage 流转，无 HTTP 端点。UI 用 `useMcpApp()` 订阅状态，用 `useNormalizedToolResult()` 获取结果。本模板无 `submit_review` 反向调用（无审核环节）。
