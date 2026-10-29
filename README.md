# fanno-work

面向专业工作流的环境、任务与轨迹运行框架。

这个项目从自己的协议出发，不继承任何现有 benchmark 的目录结构、任务 schema 或 runner。以后接入外部资源时，先通过独立适配器把它转换为 fanno-work 的原生 `EnvironmentSpec`、`TaskSpec`、动作和验证结果；核心运行时不感知来源名称。

## 当前骨架

```text
environment.json ──> Environment adapter ──┐
                                           │
task.json ─────────> TaskSpec ─────────────┼─> WorkflowRunner ─> run.json
                                           │                   trajectory.jsonl
agent adapter ─────> observe / act loop ───┤                   artifacts/
                                           │
verifier adapter ──> VerificationResult ───┘
```

统一生命周期为：

```text
prepare → observe ↔ act → verify → preserve artifacts → reset → close
```

统一的是生命周期、输入输出和审计记录。Docker、桌面 VM、真实软件、API 服务、代码世界模型等运行方式由环境插件自己实现。任务分类保留为可选 `taxonomy`，当前不冻结领域本体。

## 目录

```text
src/fanno_work/
  agents/          agent、回放或参考解的决策接口
  environments/    环境生命周期接口及最小参考实现
  runtime/         执行循环与轨迹记录
  verifiers/       确定性或模型评分接口
  models.py        项目原生数据契约
  specs.py         manifest 加载、资产与配对校验
  registry.py      插件注册表
examples/          只依赖本项目协议的冒烟示例
tests/             生命周期、隔离、reset 与产物测试
docs/              架构决策和接入规范
```

## 运行

项目当前只依赖 Python 3.11 标准库。源码布局尚未安装时使用 `PYTHONPATH=src`：

```bash
cd /data1/rjj/fanno-work
PYTHONPATH=src python -m fanno_work plugins
PYTHONPATH=src python -m fanno_work validate \
  --task examples/file_delivery/task.json \
  --environment examples/file_delivery/environment.json
PYTHONPATH=src python -m fanno_work run \
  --task examples/file_delivery/task.json \
  --environment examples/file_delivery/environment.json \
  --actions examples/file_delivery/actions.json \
  --run-dir runs/file-delivery-001
```

也可执行 `uv pip install -e .` 安装命令。运行测试：

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

## 当前边界

最小 `workspace` 环境和 `required_artifacts` 验证器用于验证框架契约，不代表任何专业领域环境。下一阶段应实现第一个真实环境插件，并保持核心 schema 不含其来源特有字段。
