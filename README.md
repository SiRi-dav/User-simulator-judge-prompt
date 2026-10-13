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

去掉 `--dry-run` 后调用评测模型。本项目的接口地址、模型名和密钥已放在 `eval_pipeline/.local_api.json`，程序会自动读取。请仅在获准访问公司接口和处理评测数据的环境中运行。若服务地址或密钥变化，更新该配置或设置 `EVAL_BASE_URL`、`EVAL_MODEL`、`EVAL_API_KEY` 环境变量。

Windows 公司电脑安装 Python 3.10+ 后，可双击 `run_example.bat` 运行示例；正式数据请按下方程序使用说明指定实际目录。

实际数据应分别放在模拟对话与案例目录。真人对话可通过 `--real-dialogues` 加入 B3 对照。完整输入格式、结果说明和风险清单见 [程序使用说明](eval_pipeline/README.md) 与 [实机测试前审查](eval_pipeline/PRE_LIVE_REVIEW.md)。

本机已用模拟模型完成流程测试；公司接口曾在当前机器上连接超时，实际模型评分仍需在可访问内网的机器上校验。
