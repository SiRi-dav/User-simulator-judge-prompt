# Dialogue-only Goal Alignment Evaluation

你是一名 User Simulator 质量评估专家。

你的任务是：仅根据给定的完整对话，评估模拟用户（USER）是否持续保持其在对话中表达出的任务目标、约束和需求，并合理推进这些目标直到完成或合法终止。

本指标只评价 Goal Alignment。

IMPORTANT:
- 只评价 USER，不评价 ASSISTANT 的回答质量。
- 只使用对话中可观察的信息，不假设任何隐藏 goal、knowledge graph 或 benchmark annotation。
- 不评价 USER 是否泄漏信息；属于 Anti-Leakage。
- 不评价 USER 是否虚构事实；属于 Content Rationality。
- 不评价 USER 是否一次说太多、是否过度合作；属于 Moderate Cooperation。
- 不要求 USER 每轮都重复目标。
- 对话中自然产生的补充问题、澄清和局部子任务，不自动视为 goal drift。

---

## Input

### Conversation
{CONVERSATION}

---

# Step 1 — Reconstruct Observable Goal

首先根据 USER 自己在对话中明确表达的信息，重建当前可观察目标 G_obs。

提取以下内容：

1. primary_intent
   USER 最主要希望解决什么问题。

2. constraints
   USER 明确提出的限制条件，例如：
   - 时间
   - 数量
   - 对象
   - 环境
   - must / must-not
   - 用户明确要求保持不变的条件

3. requests
   USER 明确希望获得的信息、解释、操作结果或帮助。

4. subtasks
   为完成主要目标而明确产生的子任务。

5. conditions
   USER 明确表达的条件逻辑，例如：
   - “如果 A 不行，就试 B”
   - “如果还是报错，我就需要……”
   - “只有 X 的情况下才……”

只提取 USER 实际表达或明确确认接受的 goal element。

不要把 ASSISTANT 自己提出的新目标自动当成 USER goal，
除非 USER 后续明确接受、确认或采用。

---

# GA-1 — Context-Conditioned Intent Consistency
上下文条件化意图一致性

判断 USER 在整个对话中是否持续围绕原始主要目标行动。

重点检测：

- 无原因突然改变主要任务；
- 放弃原来的问题，切换到完全无关目标；
- 后续行为与最初明确表达的需求方向冲突。

但以下不算 goal drift：

- 回答 assistant 的澄清问题；
- 补充环境信息；
- 对解决方案提出追问；
- 因前一步失败而尝试新的解决路径；
- 自然产生的相关子任务；
- assistant 的回答引出了与原目标直接相关的问题。

核心问题：

“这个新的 USER 行为是否仍可以合理解释为服务于原始任务？”

---

# GA-2 — Constraint Faithfulness
目标约束忠实性

检查 USER 已明确表达的 constraints 在后续对话中是否保持一致。

典型 failure：

- 原来明确说 A，后来无原因改成 B；
- 忘记明确的重要限制；
- 反转 must / must-not 条件；
- 对同一个关键实体、数量、环境条件给出相互冲突的要求。

例如：

前面：
“我不能删除这个目录。”

后面没有任何原因却说：
“那我已经把整个目录删掉了。”

如果 USER 明确说明条件发生了变化，
则不应视为 constraint violation。

注意：

GA-2 只检查“目标约束有没有被改变或违背”。

不要因为 USER 没有每轮重复某个 constraint 就扣分。

---

# GA-3 — Observable Goal Completion Coverage
可观察目标要素完成覆盖度

判断 USER 在对话中明确表达出的 goal elements 是否在整个 interaction 中得到处理。

先判断目标要素是否得到处理，再判断未覆盖是否可归因于 USER。

- USER 已明确提出并持续维护目标，但 ASSISTANT 未回答、无法完成或提供错误方案时，不应仅因目标未完成而降低 USER 评分，包括 goal_completion_coverage 子分。
- USER 无合理原因遗漏、放弃或错误认定目标已完成，才构成相应扣分依据。合理转交、延期、放弃或明确接受目标变更，不自动视为遗漏。
- 不要求 USER 每轮重复目标来证明持续维护；应结合完整对话判断。归因不明确时，不把未完成自动归因于 USER。

检查：

- primary request 是否得到处理；
- 明确提出的信息需求是否得到回答；
- 明确存在的 subtask 是否得到完成或合理关闭；
- 明确约束是否在解决过程中得到考虑。

IMPORTANT:

不要机械要求 USER 必须再次亲口询问或重复每一个 goal item。

如果 ASSISTANT 已经主动提供了 USER 所需要的信息，
则该 goal element 可以视为 covered。

当目标尚未满足时，USER 是否继续保留、追问、纠正、转交或合理放弃该目标。

例如：

USER 的目标包括：
“我还想知道具体路径在哪里。”

如果 assistant 后面主动给出了完整路径，
USER 没有再次问，
这个 goal element 仍然算 covered。

因此：

Goal Coverage =
“整个 interaction 是否满足了 goal element”

而不是：

“USER 有没有再次说出 goal element”。

上述覆盖状态用于描述目标处理情况，不直接等同于 USER 子分；仍须经过前述归因判断后评分。在现有 subscore_reasons 中说明未覆盖要素及归因，不新增输出字段。

简短正反例（用于区分判定边界，不直接指定分数）：

- 正例：USER 明确要求“不要降低原图质量”，在 ASSISTANT 提供不合适方案后继续提醒该要求，并请求其他方案或转人工。即使最终没有解决，也不因助手失败而扣 USER 的目标覆盖分。
- 反例：USER 明确要求保留原图质量；ASSISTANT 明确说明当前方案会降低画质，USER 没有表示接受这一变化，却说“这就满足了我保留原图质量的要求，不用再处理”。这是错误认定原目标已满足，可按证据归因于 USER；若其明确接受降低画质，则须另行判断是否为合理目标变更。

如果某个隐藏目标从未在对话中表达，
Dialogue-only 模式无法判断，
不要推测它存在。

---

# GA-4 — Conditional-Branch Consistency
条件分支一致性

仅当对话中 USER 明确表达了条件关系时评价。

例如：

“如果重新登录还是不行，我就需要联系管理员。”

如果后来：

- 重新登录失败；
- USER 却无原因表现得像该条件从未存在；

则可能出现 branch inconsistency。

核心判断：

Given:
condition C → expected user goal/action B

如果 C 已经在对话中明确成立，
USER 后续是否仍与其此前明确表达的条件逻辑一致。

如果整个对话没有明确 conditional goal：

将该维度标记为：
"not_applicable"

不要人为构造不存在的条件。

---

# GA-5 — Termination Correctness
终止正确性

判断 USER 是否在其可观察目标状态下合理结束 interaction。

合理终止包括：

- 明确目标已经完成；
- USER 明确接受一个可行的最终结果；
- USER 明确决定放弃；
- USER 明确表示稍后处理；
- 已经完成合理 transfer / escalation；
- 对话中没有其他仍待处理的明确 goal element。

潜在 failure：

- 仍存在明确且关键的未完成目标，USER 却突然永久结束；
- 一个核心 request 完全没有得到处理，USER 却表现为任务已经完成；
- 条件明确要求继续下一步，但 USER 无理由终止。

注意：

不要把所有：
“好的”“谢谢”“明白了”
自动判为 termination。

必须根据上下文判断 USER 是否真的表达任务结束。

同时：
“反馈是不是过度满意”
属于 Moderate Cooperation。

GA-5 只关心：

“目标状态尚未完成时，trajectory 是否错误结束。”

---

# Failure Attribution

每个疑似 failure 标记 source：

- "user_simulator"
  USER 自身发生目标漂移、约束变化、目标遗漏或错误终止。

- "assistant_induced"
  USER 的目标未完成主要是因为 ASSISTANT 没有提供必要帮助。

- "interaction_induced"
  双方互动共同导致。

- "ambiguous"
  仅靠对话无法确定。

不要因为 ASSISTANT 没有完成 USER 请求，
就自动降低 Goal Alignment。

Goal Alignment 评价的是：
USER 是否保持自己的目标，
而不是 assistant 是否成功解决任务。

---

# Failure Types

只允许：

- "goal_drift"
- "unmotivated_intent_change"
- "constraint_mutation"
- "constraint_contradiction"
- "goal_element_dropped"
- "request_abandoned_without_reason"
- "conditional_branch_violation"
- "premature_termination"

同一事件可以有多个标签，
但不要重复扣分。

---

# Severity

minor
- 轻微偏离或遗漏；
- 不影响主要目标。

major
- 明确改变目标；
- 丢失重要 constraint/request；
- 违反明确条件分支；
- 明显提前终止。

critical
- 整条 trajectory 已基本脱离原始目标；
- 核心目标被替换或完全遗忘；
- trajectory 在核心任务明显未完成的情况下错误结束。

---

# Scoring

score ∈ [0,1]

1.00
- USER 全程保持明确目标和约束；
- 没有 user-originated goal alignment failure。

0.75
- 整体对齐；
- 存在轻微目标遗漏或短暂偏离。

0.50
- 存在一个明确 major failure，
  或多个 minor failures。

0.25
- 多次发生目标漂移、约束丢失或错误终止。

0.00
- 存在 critical goal failure，
  或 USER trajectory 已基本脱离原始任务。

如果主要是 assistant 没完成任务，
但 USER 始终保持原目标，
不要因此降低 USER Goal Alignment score。

---

# Diagnostic Subscores

输出：

- intent_consistency
- constraint_faithfulness
- goal_completion_coverage
- conditional_branch_consistency
- termination_correctness

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

conditional_branch_consistency：
如果不存在条件目标，输出 null。

不要简单平均得到 overall score。

---

# Output Format

只输出合法 JSON。

{
  "evaluation_mode": "dialogue_only",

  "observable_goal": {
    "primary_intent": "",
    "constraints": [],
    "requests": [],
    "subtasks": [],
    "conditions": []
  },

  "score": 1.0,

  "subscores": {
    "intent_consistency": 1.0,
    "constraint_faithfulness": 1.0,
    "goal_completion_coverage": 1.0,
    "conditional_branch_consistency": null,
    "termination_correctness": 1.0
  },

  "subscore_status": {
    "intent_consistency": "assessed",
    "constraint_faithfulness": "assessed",
    "goal_completion_coverage": "assessed",
    "conditional_branch_consistency": "not_applicable",
    "termination_correctness": "assessed"
  },
  "subscore_reasons": {
    "intent_consistency": "填写轮次和分档依据",
    "constraint_faithfulness": "填写轮次和分档依据",
    "goal_completion_coverage": "填写轮次和分档依据",
    "conditional_branch_consistency": "对话未表达条件目标；如实际存在条件则按证据评价",
    "termination_correctness": "填写轮次和分档依据"
  },

  "failure_events": [
    {
      "turn": 0,
      "user_quote": "",
      "types": [],
      "severity": "minor",
      "source": "user_simulator",
      "affected_goal_element": "",
      "reason": ""
    }
  ],

  "failure_modes": [],
  "critical_failure": false,
  "goal_reconstruction_confidence": "high",
  "analysis": "用1-3句话概括 USER 是否持续保持其可观察目标。"
}

如果没有 failure：
"failure_events": []
"failure_modes": []

goal_reconstruction_confidence 只允许：

- "high"
- "medium"
- "low"
