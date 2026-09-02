# 写作约束

生成报告叙述、确定性正文或审查章节草稿时使用本约束。

## 事实边界

- 报告正文只使用确认后的 Engineering Facts、已校验 Research Evidence、确定性计算结果和用户确认输入。
- 章节写作阶段不得读取原始算法文件。
- 不写入未经确认的工程数字。
- 不改变固定章节结构。

## 内部词屏蔽

报告正文不得暴露 Evidence ID、URL、JSON、Skill、Provider、PA、EA、FA、内部拓扑、worker、cache 或其他实现词。

## 重点章节约束

- 1.2 是决策摘要。
- 第 26 章是全报告的最终综合判断。
- 避免 1.2 与第 26 章大段重复。
- 4.1.3 只写工程方案比较和推荐理由，不写候选得分、验证优先级、算法排序或内部拓扑推理。
- 4.2.1 按物料流和工艺段描述流程，不机械串联设备节点。
- 4.2.4 遵守 `references/engineering_rules/equipment_rules.md`。
- 10.5 遵守 `references/engineering_rules/energy_rules.md`。

## 生产安全

生产模式交付物拒绝测试 Evidence、测试正文和测试标记。
