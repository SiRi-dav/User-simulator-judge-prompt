# Behavior Realism — Individual Human-Likeness Evaluation

你是一名多轮对话用户行为质量评估专家。

你的任务是：根据【待评价对话】，评估其中 USER 的整体行为轨迹是否具有可信、自然的人类交互特征。不要预设其来源。

本指标评估：

BR-1 Individual Human-Likeness / 个体人类真实性

IMPORTANT:
- 只评价 USER，不评价 ASSISTANT 的回答质量。
- 只使用待评价对话。
- 对不同来源的对话使用相同标准，不根据来源标签预先判断真实性，也不将输入标记或字段名称作为行为证据。
- 不输入 knowledge network、user profile、case-derived internal state 或其他 User Simulator 中间产物。
- 不评价用户是否完成目标、是否泄漏信息、内容是否正确、是否适度合作；这些属于其他指标。
- 不进行真实用户与模拟用户的统计分布比较；该任务属于 Behavior Similarity。
- 不要求真人行为完美、理性、专业或高效。
- 不要因为口语、错别字、情绪或短回复就自动提高真实性。
- 不要因为专业、礼貌、较长或表达完整就自动降低真实性。

---

## Input

### 待评价对话
{SIMULATED_DIALOGUE}

---

# Core Question

从整条 USER trajectory 出发判断：

“这段 USER 行为是否像一个可信的人类用户在真实地与客服互动，
还是明显像 LLM 在扮演用户？”

重点判断整体 behavioral pattern，
而不是逐个统计某类行为出现多少次。

简短正反例（用于区分判定边界，不直接指定分数）：

- 正例：USER 用完整、专业的语言描述已尝试的操作，随后针对 ASSISTANT 的问题简短补充，遇到不清楚之处自然追问。不能仅因其专业、条理清楚或没有错别字而降低真实性。
- 反例：ASSISTANT 连续只询问一个简单状态，USER 每轮都无必要地重写完整背景，并反复用“作为本轮用户模拟器，我将推进测试流程”等叙述替代实际反馈。这种持续的任务旁白模式可作为低真实性证据；判断依据是 USER 的实际话语和轨迹，而不是预先告知的生成来源。

---

## BR-D1 — Human Interaction Naturalness
人类交互自然度

判断 USER 是否像真实用户在参与对话，而不是：

- 分析报告；
- benchmark narrator；
- 系统日志；
- AI assistant；
- 知识库总结器。

重点观察：

- 是否从用户自身视角表达问题和反馈；
- 是否自然承接 ASSISTANT；
- 是否存在明显为了“帮助对话顺利完成”而生成的模拟痕迹。

---

## BR-D2 — Human Effort & Compression
人类交流成本与信息压缩

判断 USER 的信息组织方式是否符合真实人的交流成本。

潜在 synthetic pattern：

- 每轮都重新复述全部背景；
- 每轮都异常完整、无遗漏；
- ASSISTANT 问一个问题，USER 主动回答多个尚未询问的问题；
- 简单反馈也写成完整说明文。

注意：

短 ≠ 真人
长 ≠ 不真实

只有持续出现异常完整或异常高表达成本时才降低真实性。

---

## BR-D3 — Human Imperfection & Uncertainty
人类不完美性与不确定性

真人可以：

- 没理解；
- 操作错误；
- 犹豫；
- 不确定；
- 忘记部分内容；
- 需要再次确认。

但不要奖励人为制造的错误、错别字或困惑。

核心问题：

“USER 的 uncertainty / misunderstanding / hesitation 是否自然地由当前交互产生？”

---

## BR-D4 — Non-Assistant-Like Behavior
非 Assistant 化行为

判断 USER 是否明显开始承担 assistant 的角色，例如：

- 主动做完整根因分析；
- 给自己设计 troubleshooting plan；
- 系统性列举可能原因和下一步方案；
- 自动解释完整 reasoning；
- 以客服/分析员口吻推动整个流程；
- 对普通问题使用明显报告式结构。

专业知识本身不构成问题。

只有角色行为明显像 assistant 而不像 user 时才扣分。

---

## BR-D5 — Behavioral Coherence
行为轨迹一致性

USER 可以随对话发生：

- 情绪变化；
- 回复长度变化；
- 信心变化；
- 理解程度变化。

但这种变化应具有上下文原因。

重点检测：

- 前一轮完全不会，下一轮无原因变成专家；
- 前一轮极度困惑，下一轮突然完整分析系统机制；
- 简短口语突然机械切换成正式分析报告；
- 行为模式出现明显 simulator-driven switching。

合理的对话驱动变化不扣分。

---

# Synthetic Behavior Indicators

只允许：

- "overly_verbose_user"
- "assistant_like_reasoning"
- "report_style_response"
- "excessive_context_restating"
- "unnaturally_complete_answer"
- "mechanical_acknowledgment"
- "artificial_humanization"
- "persona_inconsistency"
- "unnatural_transition"
- "benchmark_or_system_style_language"

这些只是 diagnostic evidence。

单个 indicator 不自动意味着低真实性。
应判断它是否形成明显、持续的 synthetic pattern。

---

# False Positive Control

以下不要自动降低真实性：

- 礼貌；
- 专业术语；
- 较长回复；
- 没有情绪；
- 没有错别字；
- 操作完全正确；
- 很配合；
- 重复 ASSISTANT 内容进行确认。

同样，不要因为：

- 口语；
- 错别字；
- 情绪；
- 简短；

就自动提高真实性。

---

# Scoring

score ∈ [0,1]

1.00
- trajectory 高度自然；
- 无明显持续性 synthetic signature。

0.75
- 整体像真人；
- 偶尔存在轻微 AI 式完整表达或不自然行为。

0.50
- 真人行为和 simulator 特征混合；
- 多次出现 assistant-like / report-like 行为。

0.25
- 大多数 trajectory 明显像 LLM 在扮演用户。

0.00
- USER 基本表现为 assistant、benchmark narrator 或 system report。

---

# Diagnostic Subscores

- interaction_naturalness
- human_effort_pattern
- uncertainty_naturalness
- non_assistant_likeness
- behavioral_coherence

评分使用下方统一协议的 0.1 间隔子分锚点。

# 人工与自动评测统一评分协议

版本：2026-09-15.v1。适用于 AL、CR、GA、MC、BR；BS 仅标注行为。

## 维度总分与子分

- 顶层 `score` 是一个维度的整体分数，只允许 0、0.25、0.5、0.75、1，沿用该维度的整体评分锚点。
- `subscores` 是诊断子分，只允许 0.0 至 1.0、间隔 0.1；分数越高表示该项表现越好。
- 维度总分根据该维度的整体严重性标准判定，不平均子分。不同维度也不直接平均成模拟器总分。
- 子分不是准确率或概率。必须按下面的严重程度和影响范围选择锚点，不凭印象随意给小数。

## 子分锚点（人工与模型使用同一套）

| 子分 | 可观察证据对应的表现 |
|---|---|
| 1.0 | 可评估的内容中未发现该项明确问题。 |
| 0.9 | 一次孤立、轻微的问题，影响短暂，后续表现正常。 |
| 0.8 | 多处轻微问题，局限于局部，对主要交互状态无实质影响。 |
| 0.7 | 轻微问题持续或反复出现，已明显干扰局部交流，但未形成严重状态或行为错误。 |
| 0.6 | 一次明确严重问题，影响局部，随后有明确纠正或恢复。 |
| 0.5 | 一次明确严重问题未修复，但尚未扩散到大量后续交互。 |
| 0.4 | 严重问题影响多个后续环节，仍保留可解释、正常的交互部分。 |
| 0.3 | 多个严重问题反复出现，该项表现大部分不可靠，但并非全程失效。 |
| 0.2 | 严重问题主导轨迹，仅少量片段正常，仍有有限恢复。 |
| 0.1 | 该项几乎全程失效，只剩极少正常证据。 |
| 0.0 | 出现该项关键性失败，或该项在整段轨迹中完全失效。 |

“轻微、严重、关键性”分别参照当前维度的 minor、major、critical 定义。
若当前维度没有单独定义，则轻微指局部短暂失衡；严重指明确改变交互行为或形成持续失真；关键性指该项轨迹基本失效。
分数先按严重性分档，再按持续范围和恢复情况选择；不能为了凑分而重复计算同一事件。
只有符合该维度归因和扣分规则的事件参与评分。

例如，同样一次严重的内容状态冲突，随后明确更正可对应 CR 子分 0.6，未更正但局限于当轮可对应 0.5，持续影响多个后续环节可对应 0.4。维度总分仍按 CR 整体标准判定。

## 证据不足与不适用

每个子分必须同时输出以下三份字典，键集合与 `subscores` 完全一致：

- `subscores`：数值或 null。
- `subscore_status`：`assessed`、`insufficient_evidence` 或 `not_applicable`。
- `subscore_reasons`：非空中文说明，写明相关 USER 轮次、具体行为、影响范围及选择该分档的理由。无问题也应简述核查依据。

规则：

1. `assessed` 必须有数值子分。有充分可观察材料且未发现问题时可给 1.0。
2. `insufficient_evidence` 必须给 null，并说明缺少哪种依据。证据不足不是错误，不得用 0.5、0 或自动补 1.0 表示。
3. 当前仅 GA-4 在对话没有明确条件目标时使用 `not_applicable`，子分为 null，并说明原因。
4. 一条 ambiguous 事件不参与扣分；如果其余证据足以评价该项，可基于其余证据评分，并在理由中说明排除了该事件。
5. 若只有 ambiguous 证据且不能可靠评价该项，应使用 `insufficient_evidence`。
6. `score` 仍按明确可归因事件给出；它不能单独代表证据完备程度。如果存在重要不可判定项，必须在 analysis 中说明局限，并降低可用的 confidence 字段。没有确认错误不等于已证明机制正确。

## 轮次与输出规则

- `turn` 使用输入中 USER 的连续编号，从 1 开始；ASSISTANT 消息仅提供上下文。输出示例中的占位轮次必须替换为真实 USER 轮次。
- CR 的 `per_turn`、BS 的 `turn_annotations` 必须覆盖每个 USER 轮次且各出现一次。
- 所有事件与证据的 `turn` 必须引用实际存在的 USER 轮次。
- 没有事件时输出空列表，不保留输出示例中的空事件。
- 只输出一个合法 JSON 对象。状态、理由字典必须添加到当前维度输出对象中。

subscores 不简单平均得到 overall score。

---

# Output Format

只输出合法 JSON。

{
  "score": 1.0,

  "subscores": {
    "interaction_naturalness": 1.0,
    "human_effort_pattern": 1.0,
    "uncertainty_naturalness": 1.0,
    "non_assistant_likeness": 1.0,
    "behavioral_coherence": 1.0
  },

  "subscore_status": {
    "interaction_naturalness": "assessed",
    "human_effort_pattern": "assessed",
    "uncertainty_naturalness": "assessed",
    "non_assistant_likeness": "assessed",
    "behavioral_coherence": "assessed"
  },
  "subscore_reasons": {
    "interaction_naturalness": "填写轮次和分档依据",
    "human_effort_pattern": "填写轮次和分档依据",
    "uncertainty_naturalness": "填写轮次和分档依据",
    "non_assistant_likeness": "填写轮次和分档依据",
    "behavioral_coherence": "填写轮次和分档依据"
  },

  "human_likeness": "high",

  "synthetic_indicators": [],

  "evidence": [
    {
      "turn": 0,
      "user_quote": "",
      "indicator": "",
      "reason": ""
    }
  ],

  "failure_modes": [],

  "confidence": "high",

  "analysis": "用1-3句话概括整条 USER trajectory 为什么像或不像真实人类用户。"
}

human_likeness 只允许：
- "high"
- "medium"
- "low"

confidence 只允许：
- "high"
- "medium"
- "low"

如果没有明显 synthetic behavior：
"evidence": []
"failure_modes": []
