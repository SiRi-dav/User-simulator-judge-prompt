# 用户模拟器评测

仓库根目录的六份 Markdown 是当前评测 prompt。`eval_pipeline/` 提供可运行程序；程序读取 `docs/` 下对应的六份 prompt。两处内容保持一致。

## 运行

需要 Python 3.10 或更新版本。从仓库根目录运行：

```bash
python3 -m eval_pipeline.runner \
  --dialogues eval_pipeline/examples/dialogues \
  --cases eval_pipeline/examples/cases \
  --output /tmp/user_simulator_eval \
  --dry-run
```

正式运行时，把包含全部记录的大文件分别放在 `data/simulated_dialogues.jsonl`（模拟对话）、`data/cases.jsonl`（案例）和可选的 `data/real_dialogues.jsonl`（真人对话）。整个 `data/` 已加入 `.gitignore`，数据不会随普通 Git 提交上传。文件名或存放位置不同也没关系，只需在 `eval_pipeline/run_config.json` 中填写一次实际路径。程序默认读取该配置，不用每次传路径：

```bash
python3 -m eval_pipeline.runner
```

输入既支持单个大文件，也支持目录。真人文件不存在时会提示并跳过 B3 真人比较。默认 `start_dialogue=1`、`limit_dialogues=10`，先评文件中的前 10 段；改成 20 可评前 20 段，`start_dialogue=11, limit_dialogues=10` 可评第 11–20 段。每组结果放在 `results/dialogues_起始-结束/`，重跑同一组会复用已完成任务。将 `limit_dialogues` 设为 `null` 可评从起点到文件末尾的全部对话。

公司电脑上的文件也可以在仓库外，例如 `D:/评测数据/所有模拟对话.jsonl`。在 `run_config.json` 中填写完整路径即可；Windows 路径建议使用 `/`，避免 JSON 中反斜杠的转义问题。`run_eval.bat` 只读取配置，不会额外创建仓库内的 `data` 目录。模拟对话和案例文件须在运行前存在。

本项目的接口地址、模型名和密钥已放在 `eval_pipeline/.local_api.json`，程序会自动读取。请仅在获准访问公司接口和处理评测数据的环境中运行。若服务地址或密钥变化，更新该配置或设置 `EVAL_BASE_URL`、`EVAL_MODEL`、`EVAL_API_KEY` 环境变量。

Windows 公司电脑安装 Python 3.10+ 后，可双击 `run_example.bat` 运行示例；放好实际数据后，双击 `run_eval.bat` 做正式评测。

实际数据可以分别放在三个大文件中，不必拆成每段对话或每个案例一个文件。真人对话可通过配置文件或 `--real-dialogues` 加入 B3 对照。完整输入格式、结果说明和风险清单见 [程序使用说明](eval_pipeline/README.md) 与 [实机测试前审查](eval_pipeline/PRE_LIVE_REVIEW.md)。

本机已用模拟模型完成流程测试；公司接口曾在当前机器上连接超时，实际模型评分仍需在可访问内网的机器上校验。
