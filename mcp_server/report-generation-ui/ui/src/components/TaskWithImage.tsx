/**
 * 任务阶段布局：左侧源图（任务开始即显示）+ 右侧 TaskProgress。
 *
 * 替代多个页面 `!raw && <TaskProgress />` 的纯占位，
 * 让用户在任务执行中即可核对图片是否选对。
 *
 * 注：useToolImage 返回 data URL，直接用 <img> 渲染，避免 SourceImageViewer
 * 内部再次调用 read_image 造成重复请求。
 */
import { useToolImage } from '@/patterns/image';
import { TaskProgress } from './TaskProgress';

interface TaskWithImageProps {}

export function TaskWithImage({}: TaskWithImageProps) {
  const { imageUrl, loading, error } = useToolImage();

  return (
    <div className="grid h-full grid-cols-[1fr_360px] overflow-hidden">
      {/* 左：源图（任务中即可加载，progress.uiEvent.image_paths 最早可用） */}
      <div className="bg-bg">
        {loading && (
          <div className="flex h-full items-center justify-center text-sm text-text-3">
            加载源图中…
          </div>
        )}
        {error && !loading && (
          <div className="m-4 rounded-lg bg-red/5 p-3 text-xs text-red">
            源图加载失败：{error}
          </div>
        )}
        {!loading && !error && imageUrl && (
          <div className="h-full overflow-auto bg-bg p-3">
            <img src={imageUrl} alt="源图" className="max-h-full max-w-full" />
          </div>
        )}
        {!loading && !error && !imageUrl && (
          <div className="flex h-full items-center justify-center text-sm text-text-3">
            等待源图路径…
          </div>
        )}
      </div>
      {/* 右：事件时间线 */}
      <TaskProgress />
    </div>
  );
}
