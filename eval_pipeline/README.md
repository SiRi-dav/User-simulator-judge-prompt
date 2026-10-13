# 用户模拟器评测程序

读取本地模拟对话和案例，调用当前仓库的评测 prompt，保存逐项 JSON、结果表和运行摘要。可选提供真人对话：程序先用统一 prompt 标注两组 USER 消息，再按相同交流条件计算表达特征比例及分布距离，最后交给 Batch prompt 解释 B3 差异。

实机测试前请阅读 [审查报告与风险清单](PRE_LIVE_REVIEW.md)。程序成功运行不等于评分经过人工验证；目前适合小规模校准试跑。

## 运行环境

- Python 3.10 或更新版本；只使用标准库，无须安装 Python 包。
- 一个兼容 OpenAI `POST /v1/chat/completions` 的评测模型服务。程序不会启动或部署模型。
- 模型应能返回 prompt 要求的 JSON。模型服务地址、模型名及 API key 由本机配置或运行时环境变量读取，不写进任务结果。

当前机器已设置 `eval_pipeline/.local_api.json`：截图中的服务地址、模型名与密钥均由程序自动读取。该文件权限为仅文件所有者可读写，并已列入 `eval_pipeline/.gitignore`。不要把它复制到公开仓库或结果目录。环境变量和命令行中的模型/地址会覆盖本机配置；`EVAL_API_KEY` 会覆盖本机密钥。换机器时可用 `--api-config /path/to/private-config.json` 指定另一份同格式文件。

2026-10-13 在当前机器尝试连接该内网服务，TCP 端口超时；请求未到达模型。请在能访问该内网地址的网络环境下再做短请求验证，然后运行示例评测。此时无法判断密钥、模型名和服务返回格式是否有效。

## 输入目录

```text
my_data/
  dialogues/        # 必填：模拟对话
    CASE001#1.md
    CASE002#1.json
  cases/            # 必填：案例原文
    CASE001.md
    CASE002.json
  real_dialogues/   # 可选：真人对话，仅用于 Batch B3
    REAL001#1.md
```

模拟对话支持每文件一个或多个 Markdown 会话，也支持 `.json` / `.jsonl` 中的单对象、对象数组或真正逐行 JSON。扩展名为 `.jsonl` 但内容是 JSON 数组的历史文件也能读。会递归读取子目录。

Markdown 格式：

```markdown
## CASE001#1
- case_id: CASE001
- session_index: 1

**User:** 电脑无法联网。
**Assistant:** 重连 Wi-Fi 后结果怎样？
**User:** 重连后还是不行。
```

如真人与模拟对话来自相同任务情境，可在两边都添加同一个 `- scenario_id: 网络故障A`；JSON 使用同名字段。若未提供，程序尝试用完全相同的 `case_id` 配对。存在共同情境时，只比较配对成功的对话；完全没有共同情境时，退回按表达任务和助手要求分层，并在结果中标为 `context_only`。情境编号相同仍需在 `--comparison-setup` 中说明助手类型、开头等是否一致。

JSON 格式：

```json
{
  "case_id": "CASE001",
  "session_index": 1,
  "messages": [
    {"role": "user", "content": "电脑无法联网。"},
    {"role": "assistant", "content": "重连 Wi-Fi 后结果怎样？"},
    {"role": "user", "content": "重连后还是不行。"}
  ]
}
```

每个会话必须有唯一 `dialogue_id`，可由 `CASE001#1` 标题或 `case_id` 加 `session_index` 生成。真人对话也需要一个唯一编号，避免与模拟会话混淆。

可选 `persona_id`、`group_id`、`user_context` 会传给评测 prompt，供 B2 理解既定人群与分组；复杂 user_context 请用 JSON。它们目前不会自动加入 B3 的统计分层。实机导出优先使用 JSON messages，避免 Markdown 正文中的角色标记或标题被误切。

案例支持 `CASE001.md`、单个 JSON 案例、案例数组、案例 ID 到内容对象的 JSON 字典或 JSONL。标准案例对象示例：

```json
{"case_id": "CASE001", "case_name": "网络连接", "text": ["问题现象：无法联网。", "处理步骤：……"]}
```

案例通过 `case_id` **精确匹配**模拟对话。找不到案例或案例正文为空时，信息泄漏任务会记录为跳过，其他可评任务继续。历史 `case_analysis_debug710.jsonl` 的 `target_case` 也能读取，但它是归一化快照，不能自动代表已核验的原始案例。

请将原始对话与案例放在单独的输入目录，输出目录不要建在输入目录内。图片链接只会作为文字传入，不下载或读取图片。

## 最快运行方式

在工作区根目录先预览任务，不调用模型：

```bash
python3 -m eval_pipeline.runner \
  --dialogues eval_pipeline/examples/dialogues \
  --cases eval_pipeline/examples/cases \
  --output /tmp/simulator_eval_preview \
  --model your-judge-model \
  --dry-run
```

正式调用时设置模型服务：

```bash
export EVAL_BASE_URL='https://your-service.example/v1'
export EVAL_MODEL='your-judge-model'
export EVAL_API_KEY='your-secret-key'

python3 -m eval_pipeline.runner \
  --dialogues /path/to/my_data/dialogues \
  --cases /path/to/my_data/cases \
  --output /path/to/results
```

在当前机器上直接省略这三个环境变量即可使用已配置接口，例如：

```bash
python3 -m eval_pipeline.runner \
  --dialogues eval_pipeline/examples/dialogues \
  --cases eval_pipeline/examples/cases \
  --output /tmp/simulator_eval_live
```

本地不需要鉴权的服务可以不设 `EVAL_API_KEY`。也可以通过 `--base-url`、`--model` 指定。若密钥在别的环境变量中，使用 `--api-key-env 变量名`，不要把密钥放进命令行参数。

加入真人对照：

```bash
python3 -m eval_pipeline.runner \
  --dialogues /path/to/my_data/dialogues \
  --cases /path/to/my_data/cases \
  --real-dialogues /path/to/my_data/real_dialogues \
  --output /path/to/results \
  --target-population '预期覆盖简短与详细说明型用户；分组条件已提供' \
  --comparison-setup '案例类别相近；真人客服与 AI 助手不同'
```

不清楚目标群体时省略 `--target-population`，B2 只描述所见风格。无法确认两组条件完全一致时，诚实写入 `--comparison-setup`。

## 处理流程

1. 为每个模拟会话运行自然流畅 Dialogue、真实性 Dialogue；为每个符合条件的用户回复运行自然流畅 Turn。Turn 包含截至目标 USER 的前缀。各入口均不传结果元数据。
2. 找到同 ID 且正文非空的案例时，运行信息泄漏 Dialogue。
3. 按 `--batch-size` 切分模拟对话，运行真实性 Batch。未给真人记录时，Batch 仍执行 B1/B2，B3 为未评估。
4. 若有真人记录，先对两组使用同一份标注 prompt，记录对助手的反馈类型及五种表达特征。优先用共同的 `scenario_id`／`case_id` 配对，再按表达任务、助手是否要求详细说明／固定格式分组；没有共同情境时只按这些交流条件分组，并明确标记。直接引用消息不参与统计。对每组用同一规则计算标签出现率、回复长度中位数和分布距离，将统计写入 `feature_statistics.json`。Batch 请求会收到本批次的全部模拟标签、展示的真人样例标签，以及由完整真人集合计算的统计；B3 据此核对并解释差异。

每个 batch 的统计只针对**该 batch 的模拟对话**，真人参照使用提供的真人集合。模型输入只展示部分真人原文及对应逐条标签，完整真人标签保存在结果文件中，完整真人集合用于外部统计。`--min-comparison-n` 默认 5，某个分组两边都达到这个可判消息数才生成统计。每条统计同时报告消息数和不同会话编号数（不保证独立）；同一对话的多条消息不能当作独立对话。这个门槛只用于避免输出过小样本的数值，并不代表统计显著性。标注缺失或失败时，Batch 不会继续做真人对照。

距离计算参考 [RealSim 的分布比较方法](https://arxiv.org/html/2605.02624#S3.S3)，但使用的是本项目的标签体系：正式程度、组织方式等单选特征用总变差距离（0–1）；对助手反馈、情绪、礼貌、复述等多选特征用**固定标签词表中各标签出现率**的平均绝对差（0–1），并列出每个标签的方向；回复长度用逐条字符数的经验 Wasserstein-1 距离（单位：字符），而非仅比较中位数。距离越小表示该条件下分布越接近。这些量不能相加，也不是单条对话的真人概率或 0–100 真实性评分。案例、助手和样本组成不一致时，差异不能全部归因于模拟器。

## 输出

```text
results/
  manifest.json             # 任务、prompt hash、跳过原因；不含 API key
  requests/<job_id>.txt      # 实际发送的完整请求，供复核和重现
  results/<job_id>.json      # 各任务模型 JSON、API 用量或失败原因
  results.csv               # 每任务一行，方便筛选人工复核
  summary.json              # 完成／失败／跳过计数
  annotations_simulated.json # 完整模拟用户逐条标签，提供真人数据时产生
  annotations_real.json      # 完整真人逐条标签，提供真人数据时产生
  feature_statistics.json   # 提供真人数据时产生；每个 batch 的统计
  feature_statistics.csv    # 同一批分项距离的表格，方便筛选和汇报
  comparison_coverage.json  # 两组哪些消息被使用、排除或未形成统计；分特征统计覆盖
```

同一服务地址、同一模型、同一输入和同一 prompt 的有效成功结果会复用；失败或缓存结果不符合当前输出规则时会重新调用。改变输入、prompt 或模型后会生成不同任务 ID，改变服务地址会使原成功缓存失效。模型返回缺字段、分数与状态矛盾、B3 引用不存在的统计等情况会写为 `failed`，原因保存在对应结果 JSON 和 `results.csv`。`results.csv` 保存各模块原始分数、状态、checks、summary 和 limitations，**不把不同模块相加**。Batch B1/B2/B3 是诊断状态，不是三个分数；没有足够统计时 B3 应为 `insufficient_evidence`。

`--force` 可忽略成功缓存重跑。重复性实验或服务模型版本更新建议使用新的输出目录，以保留每次结果。请勿让两个进程共用输出目录。每次启动会刷新当前汇总文件，单任务缓存仍保留；历史任务可能仍在 results 子目录中，**只按当前 manifest.json 中的任务清单汇总**。dry-run 仅预览，不把缓存结果列为本次已完成。

标注必须通过逐条对应和字段检查才会记为成功；失败原始响应会保存在结果 JSON 的 raw_response 中。现有校验不能证明标签语义和所有评分证据均正确，仍需人工抽查。`feature_statistics.json` 中的 batch_messages/reference_messages 可定位贡献统计的原始消息；这些完整编号只保存在审计文件中，避免重复加入 Batch 请求扩大输入。

summary 同时提供执行状态、评分状态和 B1/B2/B3 状态计数。退出码 0 不代表所有项目可评或合格；partial、insufficient_evidence、not_applicable、descriptive_only、跳过项需要分别报告。查看 comparison_coverage 时不能把不同条件组的消息数相加作为独立样本量。

`requests/` 和 `results/` 会保存原始对话及案例片段，应放在适当的本地目录并按项目数据要求管理。

真实模型服务尚需在使用环境中配置并接通。本地自动测试用模拟模型响应验证从输入到结果文件的完整流程，并检查 HTTP 请求格式；这不代表真实服务已连通或评测质量已通过人工验证。

## 当前仓库数据直接试跑

仓库的 `710数据/all_simulation_transcripts.jsonl` 虽有 `.jsonl` 后缀，内容实际是 JSON 数组。请先将它**单独复制**到一个 `dialogues/` 目录，不要把 `710数据/` 整个目录作为对话输入，否则其他日志会被当作对话读入。案例可将 `710数据/case_analysis_debug710.jsonl` 单独放入 `cases/`，但其内容属于历史归一化快照；信息泄漏结论仍应以核验后的原始案例复核。
