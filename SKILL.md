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

用于将项目资料和算法输出整理为结构化 Engineering Facts。

主要用于：
- 项目基础信息整理
- 改造方案信息整理
- 工艺及设备事实整理
- 投资及经济评价输入整理


### report_prepare

用于基于 Engineering Facts 创建报告编制工作包。

主要生成：
- 报告章节任务
- 章节输入事实
- Research 任务
- Writing 任务
- Synthesis 任务


### report_finalize

用于基于章节生成结果完成报告交付。

主要输出：
- 可研报告 Markdown
- 可研报告 DOCX
- 报告产物信息


---

## 参考规则

执行报告生成任务时，根据需要读取：

| 文件 | 用途 |
| --- | --- |
| references/engineering_rules | 工程事实及专业语义规则 |
| references/report_rules | 可研报告编制规则 |
| references/chapter_rules | 章节生成规则 |
| references/templates | 报告结构模板 |


---

## 质量要求

报告生成过程中：

- 技术方案描述应基于工程事实和计算结果。
- 关键技术参数、投资数据、经济指标应具备来源。
- 章节内容应符合工程可研报告表达习惯。
- 输出报告应保持结构完整、内容一致。