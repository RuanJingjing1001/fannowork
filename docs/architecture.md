# 架构与稳定边界

## 核心对象

| 对象 | 负责什么 | 不负责什么 |
|---|---|---|
| `EnvironmentSpec` | 选择环境插件、固定版本、提供运行配置 | 任务指令与评分规则 |
| `TaskSpec` | 初态、输入资产、交付物、验证器选择 | 软件如何启动 |
| `Agent` | 根据可见 observation 选择 action | 读取 evaluator 私有状态 |
| `WorkflowEnvironment` | prepare、observe、act、snapshot、reset、close | 决定任务是否成功 |
| `Verifier` | 对终态与交付物返回逐项结果 | 驱动 agent |
| `WorkflowRunner` | 编排生命周期并保存轨迹、产物和运行清单 | 包含领域逻辑 |

环境和任务分离：一个环境版本可以承载多个任务；同一任务明确绑定一个环境 ID。环境的 `snapshot` 只提供给运行控制器与评分器，agent 只能看到 `observe`。

## 原生规范与外部来源

外部 benchmark、开源软件或内部系统位于核心边界之外。接入过程固定为：

```text
外部格式/软件
  → source-specific importer 或 adapter
  → fanno-work 原生 environment.json + task.json
  → 原生校验
  → 统一 runner
```

导入器必须显式处理字段缺失、来源版本、许可和资产哈希。不能把上游 schema 直接塞入 `metadata` 后绕过原生契约，也不能让 runner 根据 benchmark 名称分支。

## 稳定产物

每次运行至少写出：

- `run.json`：任务、环境版本、状态摘要、验证与 reset 结果；
- `trajectory.jsonl`：带时间的 observation、action、action result 和 verification；
- `artifacts/`：在 reset 前保存的最终交付物。

环境插件还可以增加日志、截图或服务状态，但这些扩展不能替代上述公共产物。

## 后续扩展点

1. 容器环境：镜像 digest、挂载边界、网络策略和健康检查。
2. 桌面软件环境：VM/snapshot、CUA/MCP/CLI 动作和截图观测。
3. 服务型环境：数据库初态、API 动作、事务与 reset。
4. agent 适配：模型工具调用、人工操作、轨迹回放和参考解。
5. verifier：确定性断言、文件结构检查、领域 rubric 和人工复核。
