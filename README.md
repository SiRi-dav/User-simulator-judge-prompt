# 用户模拟器评测：精简运行版

这个分支只保留运行评测所需的代码、六份评测 prompt、配置文件和 Windows 启动脚本。需要 Python 3.10 或更高版本，无需安装第三方包。

## 准备数据

在 [运行配置](eval_pipeline/run_config.json)中填写三个输入路径：

- `dialogues`：包含全部模拟对话的 JSON／JSONL 文件，必填。
- `cases`：包含全部案例的 JSON／JSONL 文件，必填。
- `real_dialogues`：包含全部真人对话的 JSON／JSONL 文件，可选；缺失时跳过 B3 真人比较。

可以用仓库外的绝对路径，例如 `D:/评测数据/所有模拟对话.jsonl`。Windows 路径请用 `/`。各文件可以是 JSON 对象数组，或每行一个 JSON 对象。每条对话需要 `case_id`、`session_index` 和 `messages`；每个案例需要 `case_id` 和 `text`。程序也兼容原先的目录输入。

运行配置默认选择前 10 段模拟对话。将 `limit_dialogues` 改为 `20` 可评前 20 段；设置 `start_dialogue: 11`、`limit_dialogues: 10` 可评第 11–20 段；将 `limit_dialogues` 设为 `null` 可评到文件结尾。这里的序号按文件中的记录顺序计算。`--batch-size` 控制每次 Batch 诊断使用多少段对话，并不限制总评测数量。

## 运行

公司电脑可双击 `run_eval.bat`。在终端中，从仓库根目录运行：

```bash
python3 -m eval_pipeline.runner
```

首次运行前，请核对 [API 配置](eval_pipeline/.local_api.json)中的接口地址和模型名，并确保电脑能访问公司内网服务。也可通过环境变量 `EVAL_BASE_URL`、`EVAL_MODEL`、`EVAL_API_KEY` 覆盖对应配置。只预览任务、不调用模型时，在命令后加 `--dry-run`。

结果保存在 `results/dialogues_00001-00010/` 等独立目录。每个任务完成后就保存结果；重跑同一组会复用已成功且输入未变的结果，失败和未完成任务会再执行。`summary.json` 是本组摘要，`results.csv` 是逐项结果；不同组的摘要不会自动合并。真人对照标注和分布统计也保存在该组结果目录。

程序实际读取的 prompt 在 `prompts/`；目录中的六个文件均为运行依赖。数据与结果目录已加入 `.gitignore`，不会随普通提交上传。接口配置包含公司提供的访问凭据，此分支应保持私有访问。
