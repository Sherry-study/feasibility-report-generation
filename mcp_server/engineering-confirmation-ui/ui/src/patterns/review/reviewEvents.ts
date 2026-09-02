/**
 * 审核模式事件名注册 -- 通过 core 的扩展点把 uiEvent -> 事件名 解析器
 * 注册到 core，事件名语义归属业务模式，改事件名无需动 core（"请勿修改"层）。
 *
 * 本模块只需被 import 一次（patterns/review/index.ts 已引入），
 * 注册发生在模块加载期，早于任何 progress 通知到达。
 */
import { registerProgressEventResolver } from '@/core/mcpApp';

registerProgressEventResolver((uiEvent) => {
  if (uiEvent?.review_pending) return 'waiting_for_review';
  if (uiEvent?.final_result) return 'task_completed';
  return null; // 不识别，交由其它解析器/默认 'progress'
});

export {};
