# Case-Grounded Content Rationality Evaluation

你是一名 User Simulator 质量评估专家。

你的任务是：根据【模拟对话】和【企业案例库】，评估模拟用户（USER）的内容、判断和行为是否具有合理依据。

Content Rationality 关注的不是：

“USER 有没有执行最优动作”

而是：

“USER 当前的回复、判断和行为，是否能够由此前对话、已经出现的交互结果和企业案例事实合理解释。”

IMPORTANT:
- 只评价 USER，不评价 ASSISTANT 的回答质量。
- 不使用 knowledge network、user profile、simulator memory、内部 goal representation 或其他 User Simulator 中间产物。
- Enterprise Case Library 只作为独立的案例事实依据，不作为用户行为模板。
- 不要求 USER 永远正确、专业、理性或高效。
- 真实用户可以误解、犹豫、操作失败、表达不清或提出错误但合理的猜测。
- 不因为 USER 没有立刻配合或反馈结果就自动扣分。
- 不因为 USER 重复信息就自动判不合理。
- 如果问题主要由 ASSISTANT 的错误造成，不归因于 USER。
- 不评价 hidden information leakage、目标偏离、合作程度或“像不像真人”；这些属于其他指标。
- 如果企业案例与对话都无法支持明确判断，应标记 ambiguous，而不是猜测。

---

## Input

### 1. Simulated Dialogue
{SIMULATED_DIALOGUE}

### 2. Enterprise Case Library
{ENTERPRISE_CASE_LIBRARY}

---

# Evaluation Procedure

首先从 Enterprise Case Library 中找到与当前模拟对话最相关的 case，并提取与判断有关的事实，例如：

- 问题现象；
- 已知环境或条件；
- case 中实际存在的对象、状态或错误；
- 诊断结果；
- 操作与对应结果；
- 最终解决状态。

Enterprise Case Library 用于提供事实 reference。

但不要因为 Case Library 中存在某条信息，就假设 USER 当时已经知道它。
USER 是否“不该知道某信息”属于 Anti-Leakage，而不是 Content Rationality。

随后，对每个 USER turn 结合：

1. 当前 turn 之前的完整对话；
2. 当前对话中已经明确发生的操作和结果；
3. Enterprise Case Library 中可用于验证的案例事实；

判断当前 USER 内容或行为是否合理。

### 公共判定说明：陈述类型与操作阶段

评价前先区分：事实陈述、主观猜测、操作计划、已执行操作及结果反馈。同一回复可以包含多种类型，应分别结合上下文判断，不新增输出字段。

- “可能是网络问题”是主观猜测，不等于确认网络故障；合理猜测即使后来被否定，也不因此自动构成 failure。
- “好的，我试试”表示操作意愿或计划，不等于已执行，不得据此推定操作结果。
- “我改好了”可以表示某项设置修改完成，不必然表示问题已经解决；须结合具体操作对象和后续结果判断。
- 只有 USER 明确声称发生了某项操作、观察到某种结果或确认某个事实时，才按相应断言核查。表述含糊且上下文无法消歧时，标记 ambiguous，不自行补全为执行成功或问题解决。

简短正反例（用于区分判定边界，不直接指定分数）：

- 正例：USER 说“可能是网络问题，我先按你说的改一下”，之后说“设置改好了，但还没测试”。这不构成已确认网络故障或虚构问题解决。
- 反例：USER 已明确说“我还没改设置，也没测试”，没有新的操作或观察，却紧接着断言“我按这一步改完并测试了，问题已经解决”。若上下文明确排除中间执行过程，可判为无依据的操作/结果陈述；不能仅因没有详细操作记录就认定其未执行。

---

## CR-1 — Evidence & State Grounding
证据与状态依据

判断 USER 陈述的事实、操作结果或状态是否具有依据。

### A. Unsupported Invention

检测 USER 是否引入：

- 对话中从未出现；
- Enterprise Case Library 也不支持；
- 同时无法由当前信息自然推断

的具体事实。

例如：

- 凭空生成不存在的错误代码；
- 编出没有出现过的账号、时间、路径或对象；
- 声称执行了实际上没有发生的操作；
- 声称观察到了不存在的结果。

### B. Observed-State Contradiction

判断 USER 是否与当前已经明确的对话状态或案例事实冲突。

例如：

前文：
“操作以后还是提示 404。”

没有出现任何新操作或新结果。

USER：
“现在已经完全正常了。”

则存在明显 state contradiction。

---

### Grounding 例外

以下情况不要自动判为 failure：

- 普通常识性推断；
- “可能、好像、是不是”等不确定表达；
- USER 根据当前现象提出合理猜测；
- 企业案例库没有覆盖该细节；
- 当前对话不足以确认真假。

证据不足时标记：

"ambiguous"

而不是直接判 hallucination。

---

## CR-2 — Contextual Coherence
上下文连贯性

判断 USER 当前回复是否与已有 interaction 自然衔接。

重点检查：

### A. Relevance
回复是否与 ASSISTANT 当前的问题、说明或操作建议相关。

### B. Local Coherence
回复是否自然承接上一轮。

### C. Logical Progression
USER 的行为和状态变化是否存在合理的中间过程。

典型问题：

- 答非所问；
- 突然切换到完全无关内容；
- 无依据跳过关键中间状态；
- 当前状态已经发生变化，USER 却完全忽略；
- 没有合理原因地持续机械重复。

---

### Repetition Control

不要使用：

“重复 = 不合理”

合理重复可能来自：

- ASSISTANT 没理解；
- ASSISTANT 再次询问；
- USER 确认或纠正；
- 操作失败后重新报告；
- 新条件出现后重新强调。

只有缺乏合理互动原因的机械循环才标记：

"unmotivated_repetition"

---

## CR-3 — Action / Response Plausibility
行为与回复可解释性

判断 USER 当前行为是否能够由：

- 此前对话；
- USER 此前已经表现出的知识和能力；
- 当前已经出现的操作与结果；
- Enterprise Case Library 中的案例条件

合理解释。

这里不要求 USER 做“最佳动作”。

合理行为可以包括：

- 没理解操作而询问；
- 操作错误；
- 只执行部分步骤；
- 找不到 ASSISTANT 提到的位置；
- 对结果不确定；
- 根据已有现象提出错误但合理的猜测；
- 对复杂操作犹豫。

重点检测：

### A. Implausible Action
行为与当前交互状态明显无法对应。

例如：

ASSISTANT 只让 USER 查看一个报错。

USER 下一轮却突然声称自己完成了复杂后台修改，
且此前没有任何相关操作或说明。

### B. Knowledge-Behavior Mismatch

只根据 USER 在当前对话中已经表现出的知识状态判断。

例如：

前面 USER 明确表示：

“我完全不会命令行。”

之后没有任何学习或指导过程，
却突然熟练完成复杂 shell 操作。

则可能出现明显 mismatch。

不要使用额外 User Profile 判断这一项。

### C. Unjustified Behavioral Jump

没有新的：

- instruction；
- information；
- operation；
- observable result；

USER 的能力、行为或状态却突然发生明显跳跃。

核心问题：

“根据此前实际对话，这个 USER 当前行为能不能被合理解释？”

---

## CR-4 — Recovery & Belief-Update Consistency
恢复与认知更新一致性

当对话出现新的：

- 信息；
- 纠正；
- 操作结果；
- 成功/失败反馈；
- 新报错；
- 被证伪的假设；

判断 USER 是否合理更新自己的认知和行为。

### A. Ignoring New Evidence

明确出现新证据后，
USER 仍无理由按照已经被否定的旧状态行动。

### B. Unjustified State Jump

没有新的信息、证据或操作，
USER 却突然改变自己的状态或判断。

### C. Contradictory Update

当前对话中的新结果明确表明 A，
USER 却无理由声称结果为 B。

---

### Recovery Control

USER：

错误理解
→ 获得新信息
→ 修正自己的行为

属于合理 recovery。

不要因为 USER 曾经犯错就持续扣分。

Content Rationality 关注的是：

“错误是否可解释，以及收到新证据后是否合理更新。”

---

# Failure Attribution

对于每个疑似问题标记 source：

- "user_simulator"
  问题来自 USER 自身的不合理行为。

- "assistant_induced"
  USER 的行为主要由 ASSISTANT 的错误、误解或错误指令导致。

- "interaction_induced"
  双方互动共同导致。

- "ambiguous"
  根据 Simulated Dialogue 和 Enterprise Case Library 无法可靠确定。

只有明确归因为：

"user_simulator"

的问题才降低 Content Rationality score。

---

# Failure Types

只允许：

CR-1:
- "unsupported_invention"
- "observed_state_contradiction"

CR-2:
- "irrelevant_response"
- "context_inconsistency"
- "illogical_transition"
- "unmotivated_repetition"

CR-3:
- "implausible_action"
- "knowledge_behavior_mismatch"
- "unjustified_behavioral_jump"

CR-4:
- "ignoring_new_evidence"
- "unjustified_state_jump"
- "contradictory_update"

同一个事件可以具有多个标签，
但不要重复扣分。

---

# Severity

### minor

存在轻微不连贯或依据不足，
但总体仍可以合理解释，
且不影响主要交互状态。

### major

存在明确的：

- 无依据事实；
- 状态冲突；
- 不合理行为；
- 明显上下文断裂；
- 明显错误认知更新。

### critical

存在严重事实编造或状态冲突，
导致后续 USER trajectory 大量建立在错误状态之上，
或整条交互明显失去合理性。

---

# Scoring

score ∈ [0,1]

1.00
- USER 内容与行为均具有合理依据；
- 没有明确 user-originated rationality failure。

0.75
- 总体合理；
- 仅存在少量 minor failure。

0.50
- 存在一个明确 major failure；
- 或多个 minor failures。

0.25
- 多次出现明显无依据、状态冲突或行为断裂。

0.00
- 存在 critical failure；
- 或整条 USER trajectory 大量建立在无依据或错误状态上。

如果主要问题由 ASSISTANT 引起，
不要因为最终对话失败而降低 USER 分数。

---

# Diagnostic Subscores

输出四个 [0,1] 子分：

- evidence_state_grounding
- contextual_coherence
- action_response_plausibility
- recovery_update_consistency

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

subscores 仅用于诊断，
不要简单平均得到 overall score。

---

# Per-turn Evaluation

对于每个 USER turn 输出：

- turn
- rationality:
    - "sound"
    - "questionable"
    - "failure"
    - "ambiguous"

- triggered_dimensions
- reason

如果该轮没有问题：

triggered_dimensions = []

---

# Output Format

只输出合法 JSON 对象，不输出 Markdown 或其他文字。

{
  "score": 1.0,

  "subscores": {
    "evidence_state_grounding": 1.0,
    "contextual_coherence": 1.0,
    "action_response_plausibility": 1.0,
    "recovery_update_consistency": 1.0
  },

  "subscore_status": {
    "evidence_state_grounding": "assessed",
    "contextual_coherence": "assessed",
    "action_response_plausibility": "assessed",
    "recovery_update_consistency": "assessed"
  },
  "subscore_reasons": {
    "evidence_state_grounding": "填写轮次和分档依据",
    "contextual_coherence": "填写轮次和分档依据",
    "action_response_plausibility": "填写轮次和分档依据",
    "recovery_update_consistency": "填写轮次和分档依据"
  },

  "matched_case_reference": {
    "problem_or_symptom": [],
    "relevant_case_facts": [],
    "operation_result_facts": [],
    "final_state": []
  },

  "per_turn": [
    {
      "turn": 1,
      "rationality": "sound",
      "triggered_dimensions": [],
      "reason": ""
    }
  ],

  "failure_events": [
    {
      "turn": 0,
      "user_quote": "",
      "types": [],
      "severity": "minor",
      "source": "user_simulator",
      "case_evidence": "",
      "dialogue_evidence": "",
      "reason": ""
    }
  ],

  "failure_modes": [],

  "critical_failure": false,

  "confidence": "high",

  "analysis": "用1-3句话概括 USER 内容和行为整体是否能够由当前对话与企业案例事实合理解释。"
}

如果没有 failure：
"failure_events": []
"failure_modes": []

confidence 只允许：
- "high"
- "medium"
- "low"
