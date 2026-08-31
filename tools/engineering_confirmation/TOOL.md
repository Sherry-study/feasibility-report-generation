# Engineering Confirmation Tool（阶段②：人工确认）

## 用途
单一入口完成人工确认阶段：FA 年化计算并 merge 回写 facts -> 生成关键事实确认单（JSON + Markdown）-> 人工确认门槛 -> 应用确认结果。等价于 run_skill.py 阶段②（L110-139）的进程内编排。

## 独立调用
```bash
# 第一步：生成确认单（exit 12 表示等待人工确认）
python tools/engineering_confirmation/run.py \
  --facts <输出目录>/project_facts.json \
  --output-dir <输出目录>

# 第二步：确认后重跑（二选一）
python tools/engineering_confirmation/run.py --facts ... --output-dir ... --confirm-as-is
python tools/engineering_confirmation/run.py --facts ... --output-dir ... --confirmation-response <response.json>
```

## 参数
- `--facts`：必填，指向阶段①产出的 `project_facts.json`；注意该文件会被年化 merge 回写（与旧路径行为一致）；
- `--output-dir`：必填；
- `--confirm-as-is` / `--confirmation-response`：确认方式，二者均缺省时 exit 12。

## 产物
`annualization_result.json`、`report_confirmation.json`、`report_confirmation.md`；确认成功后另产出 `confirmed_project_facts.json`、`confirmed_report_confirmation.json`。

## 退出码
- `0`：confirmed，确认完成；
- `12`：needs_confirmation（未提供确认方式）或 confirmation_invalid（确认响应校验失败，summary 含 error）。
