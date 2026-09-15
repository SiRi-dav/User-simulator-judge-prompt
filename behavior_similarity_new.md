# Behavior Similarity — Distributional Behavior Annotation

你是一名多轮客服对话用户行为分析专家。

你的任务不是判断单个 USER “像不像真人”，
也不是直接给 Simulated Dialogue 一个 similarity score。

你的任务是：

使用完全相同的标注标准，
对输入的一条【待标注对话】中的 USER 行为进行结构化标注。模拟对话与真实历史对话分开调用本模板，使用相同标准；当前输入不说明来源。

标注结果随后由外部程序聚合，
比较模拟用户群体与真实用户群体的行为分布。

IMPORTANT:
- 只标注 USER 行为。
- ASSISTANT 仅作为理解 USER 行为的上下文。
- 对模拟对话和真实对话必须使用完全相同的 taxonomy。
- 标注单条 dialogue 时，不应根据来源调整标准。
- 如果系统实现允许，应对 dialogue source 做 blind processing。
- 不评价行为“好不好”。
- 不评价单条 dialogue 是否像真人；属于 Behavior Realism。
- 不评价内容是否正确、目标是否完成或是否信息泄漏。
- 只标注实际观察到的用户行为，不推测未表现的人格特征。

---

## Input

### 待标注对话
{DIALOGUE}

仅标注当前这一条对话，不编造或补齐另一种来源的对话。USER turn 使用输入中的连续编号，从 1 开始。
turn_annotations 必须覆盖每个 USER 轮次，各出现一次。dialogue_id 使用输入的 dialogue_id（若有）或 case_id。
本次标注不代表已完成真实与模拟用户的分布比较。

---

# Dimension 1 — Intent

对每个 USER turn 标注当前 communicative intent。

允许多标签，但通常不超过 2 个。

只允许：

- problem_or_status_report
  报告初始问题、当前异常、报错或状态变化。

- information_provision
  回答问题或补充设备、账号、环境、限制条件等信息。

- clarification_request
  请求解释、澄清或确认。

- guidance_request
  请求下一步操作或具体指导。

- action_result_report
  报告执行操作及其可观察结果。

- alternative_or_escalation_request
  请求替代方案、绕行方法、人工支持或升级处理。

- other

- none

规则：

- none 不能与其他 intent 共存。
- problem_or_status_report 强调状态本身。
- action_result_report 强调“执行动作 + 结果”。
- Intent 描述 USER 当前在做什么；
  Feedback 描述 USER 是否在回应上一轮 ASSISTANT。

---

# Dimension 2 — Feedback

判断 USER 是否对 ASSISTANT 前一轮提供反馈。

只允许：

- no_feedback
- explicit_positive
- explicit_negative
- regeneration_request
- continuation_request
- clarification_request
- clarification_provision

允许多个标签，但：

no_feedback 不能与任何其他 feedback 标签共存。

---

# Dimension 3 — Emotion

标注主要显式情绪：

- anger
- disgust
- fear
- joy
- sadness
- surprise
- neutral

只有存在充分文本证据时才使用非 neutral。

---

# Dimension 4 — Personal Context / Identity Disclosure

只允许：

- demographic
- physical
- interpersonal_relationship
- past_event
- future_plan
- worldview
- none

none 不能与其他标签共存。

普通：

“我的电脑”
“我的账号”

不自动算 personal context。

---

# Dimension 5 — Knowledge State

根据完整 dialogue 提取有明确证据的 USER knowledge statement。

每条包含：

- concept
- state:
    - knows
    - partial
    - does_not_know
- evidence_turns
- evidence

不要推断 dialogue 没有表现出的知识。

---

# Dimension 6 — Interaction Length

记录：

- number_of_user_turns

- message_length_pattern:
    - mostly_short
    - mixed
    - mostly_long

这里只做粗粒度描述。

精确：

- character count
- word count
- token count
- mean message length

由外部程序计算。

---

# Dimension 7 — Linguistic Style

dialogue-level 标注：

formality:
- informal
- mixed
- formal

sentence_structure:
- fragmented
- mixed
- complete

interaction_style:
- concise
- verbose
- conversational
- report_like
- highly_structured
- none

interaction_style 可以多选。

不要判断哪一种风格更好。

---

# Dimension 8 — Error / Disfluency

对 USER turn 标注：

- typo
- grammatical_error
- incomplete_sentence
- self_correction
- repetition
- hesitation_or_filler
- unclear_reference
- none

只有实际观察到时标注。

none 不能与其他标签共存。

---

# Output Format

对于每条 dialogue 只输出合法 JSON：

{
  "dialogue_id": "",

  "turn_annotations": [
    {
      "turn": 1,
      "intent": [],
      "feedback": [],
      "emotion": "neutral",
      "personal_context": [],
      "error_disfluency": []
    }
  ],

  "knowledge_statements": [
    {
      "concept": "",
      "state": "knows",
      "evidence_turns": [],
      "evidence": ""
    }
  ],

  "dialogue_level": {
    "number_of_user_turns": 0,
    "message_length_pattern": "mixed",
    "formality": "mixed",
    "sentence_structure": "mixed",
    "interaction_style": []
  },

  "analysis": "用1-2句话客观描述 USER 的主要行为模式，不评价其好坏或是否像真人。"
}
