# fanno-work

面向专业工作流的环境、任务与轨迹运行框架。

这个项目从自己的协议出发，不继承任何现有 benchmark 的目录结构、任务 schema 或 runner。以后接入外部资源时，先通过独立适配器把它转换为 fanno-work 的原生 `EnvironmentSpec`、`TaskSpec`、动作和验证结果；核心运行时不感知来源名称。

## 当前架构

```text
TaskSpec + Environment Profile + Action Catalog
                       │
                       ▼
        Environment Runtime / Gateway
                       │
       Agent ── Action ↔ Observation
                       │
                       ▼
      Snapshot + Trace + Artifact + Audit
                       │
                       ▼
          Independent Reward Engine
```

统一生命周期为：

```text
prepare → observe ↔ act → verify → preserve artifacts → reset → close
```

统一的是生命周期、输入输出和审计记录。Docker、桌面 VM、真实软件、API 服务、代码世界模型等运行方式由环境插件自己实现。任务分类保留为可选 `taxonomy`，当前不冻结领域本体。

## 目录

```text
src/fanno_work/
  agents/          Agent、回放和参考动作接口
  environments/    环境生命周期协议与最小参考环境
  evaluation/      Checker、Reward 聚合与逐项证据
  gateway/         Agent 与受信控制端的独立入口
  integrations/    Harbor 等外部 Harness 适配器
  observability/   Trace 和运行事件
  runtime/         执行循环与轨迹记录
  sandbox/         容器工作区与受控执行
  tasking/         Canonical Task Bundle 构建与校验
  verifiers/       核心框架的最小验证接口
  models.py        项目原生数据契约
  specs.py         EnvironmentSpec、TaskSpec 加载与配对校验
legal/
  src/fanno_legal/ 法律办公环境插件、能力与真实软件后端
  environments/    Legal Office 的本地和容器 Profile
  tasks/           任务源、初始状态与合法来源材料
  evaluators/      任务级独立评测配置
  tests/           法律能力的单元、集成与端到端测试
examples/          只依赖核心协议的最小冒烟示例
tests/             核心协议、Bundle、Reward 与 Harbor 编译测试
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

## 生成目录

下面的目录不属于源码，均已加入 `.gitignore`，可以从任务源重新生成：

```text
build/       Canonical Bundle 和 Harbor Task Package
jobs/        Harbor 运行结果
legal/runs/  法律环境演示和集成运行产物
.harbor/     单次 Harbor 启动使用的临时配置
```

## 当前边界

核心包不包含法律领域规则。法律能力位于独立的 `fanno-legal` 插件中，
并通过相同的 Action/Observation、Task Bundle 和 Reward 协议接入核心框架。
任务特有事实、素材与验证条件留在任务和 evaluator 中，不写入通用运行内核。
