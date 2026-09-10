---
name: feasibility-report-generation
title: 可研报告生成
description: 用于基于工业改造项目工程事实生成可行性研究报告。当已有工程输入、算法结果或项目资料，需要生成设备级、装置级、系统级或全厂级改造项目可研文件时使用。适用于炼化、化工装置改造类项目。不用于专业计算、设备设计或单纯报告审阅。
tools:
  - engineering_facts
  - report_prepare
  - report_finalize
---

# 可研报告生成

## 能力总览

| 场景 | 用户需求 | 核心能力 | 产出 |
| --- | --- | --- | --- |
| 工程事实整理 | 将项目输入转换为结构化工程信息 | 提取、整理工程事实 | Engineering Facts |
| 报告编制准备 | 基于工程事实组织报告编制任务 | 创建章节工作包 | Report Work Package |
| 报告生成交付 | 汇总章节结果生成最终报告 | 报告合成与格式化 | 可研报告文件 |

---

## 全局工作流

工程输入  
→ Engineering Facts 工程事实整理  
→ Report Work Package 报告任务拆解  
→ Research / Writing / Synthesis 章节生成  
→ Report Finalize 报告汇总交付

本 Skill 负责协调报告生成过程，专业计算、仿真分析及设备设计由对应专业能力提供结果。

---

## 核心原则

1. 报告内容应基于工程事实、计算结果和已确认项目输入生成。
2. 工程事实、章节内容和最终报告之间保持来源可追溯。
3. Skill 负责报告编制过程组织，不替代专业计算工具。
4. 对于缺失工程信息，应明确缺失内容及影响，并保留待补充状态。
5. 报告章节结构、编制要求和专业写作规则遵循 references 中定义的规则。

---

## 场景路由

| 用户需求 | 调用能力 |
| --- | --- |
| 提供项目资料、工程数据，需要形成结构化项目事实 | engineering_facts |
| 已具备工程事实，需要组织可研章节编制任务 | report_prepare |
| 已完成章节生成，需要输出完整报告文件 | report_finalize |

---

## 工具层

### engineering_facts

职责：
将项目输入、算法结果和工程资料整理为统一 Engineering Facts，作为后续报告生成的事实基础。

主要用于：
- 项目基础信息整理
- 改造方案信息整理
- 工艺及设备事实整理
- 投资及经济评价输入整理


### report_prepare

职责：
基于 Engineering Facts 创建报告编制工作包，明确章节任务、研究任务和写作任务。

主要生成：
- 报告章节任务
- 章节输入事实
- Research 任务
- Writing 任务
- Synthesis 任务


### report_finalize

职责：
基于章节生成结果完成报告汇总和交付。

主要输出：
- 可研报告 Markdown
- 可研报告 DOCX
- 报告产物信息

详细输入输出契约见：
references/io-contract.md

---

## Research / Writing / Synthesis

### Research

根据章节任务获取和整理支撑报告编制所需的信息。

信息来源包括：
- 工程事实；
- 项目资料；
- 历史案例；
- 可用知识资源。

研究结果需要与项目事实区分，并保留来源信息。

### Writing

基于 Engineering Facts 和 Research 结果生成章节内容。

### Synthesis

汇总章节结果形成完整报告，并检查结构和数据一致性。

---

## 参考规则

执行报告生成任务时，根据需要读取：

| 文件 | 用途 |
| --- | --- |
| references/io-contract.md | 输入 / 输出契约（工具入参、阶段交接字段、产物落点） |
| references/engineering_rules/ | 工程事实及专业语义规则 |
| references/report_rules/ | 可研报告编制规则 |
| references/runtime_policy.md | worker、cache、运行时 artifact 等宿主运行策略 |
| prompts/ | 章节撰写、研究、汇总的契约与系统提示 |

报告结构模板的单一事实源是 `src/report_shared/templates/report_template.json`，由 `report_prepare` 运行时加载校验，skill 目录下不保留副本。

---

## 质量要求

报告生成过程中：

- 技术方案描述应基于工程事实和计算结果。
- 关键技术参数、投资数据、经济指标应具备来源。
- 章节内容应符合工程可研报告表达习惯。
- 输出报告应保持结构完整、内容一致。

---

## 执行规则

报告生成过程中： 主agent需要总结一份执行概要，并交由子agent，完成报告的生成。
