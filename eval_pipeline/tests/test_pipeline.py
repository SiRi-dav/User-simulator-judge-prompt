import csv
import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from eval_pipeline import runner

REAL_CALL_MODEL = runner.call_model


class MockJudge:
    prompts = []

    @classmethod
    def call(cls, prompt, model, base_url, api_key, timeout, retries):
        cls.prompts.append(prompt)
        if "# 用户表达特征标注" in prompt:
            section = prompt.split("<dialogue_data>", 1)[1].split("</dialogue_data>", 1)[0]
            dialogues = []
            for block in re.split(r"(?m)(?=^## )", section):
                ids = re.findall(r"(?m)^##\s+(.+)$", block)
                if not ids:
                    continue
                messages = []
                users = re.findall(r"(?m)^\*\*User:\*\* (.+)$", block)
                for i, text in enumerate(users, 1):
                    is_real = ids[0].startswith("REAL")
                    messages.append({"message_id": f"U{i}", "user_text": text, "status": "annotated", "context": {"tasks": ["描述问题"], "detail_requested": False, "format_requested": False, "has_quoted_material": False, "quoted_spans": [], "assistant_feedback": ["无反馈"]}, "features": {"emotion": ["担忧"] if is_real else ["无明显情绪"], "formality": "正式" if is_real else "中性", "organization": "连续叙述", "politeness": ["无"], "restatement": ["无"]}, "evidence": [], "limitations": []})
                dialogues.append({"dialogue_id": ids[0], "messages": messages, "limitations": []})
            data = {"schema_version": "expression_features_v1", "dialogues": dialogues, "excluded_dialogues": []}
        elif "# 用户表达真实性评测 跨对话批量诊断" in prompt:
            section = prompt.split("<evaluation_input>", 1)[1].split("</evaluation_input>", 1)[0]
            status = "compared" if re.search(r"(?m)^# 外部统计\s*$", section) else "insufficient_evidence" if re.search(r"(?m)^# 真实参照对话\s*$", section) else "not_evaluated"
            comparisons = []
            if status == "compared":
                stats = json.loads(section.split("# 外部统计\n", 1)[1])
                comparisons = [{"stat_id": stats[0]["stat_id"], "conclusion": "按统计比较", "limitations": []}]
            data = {"scope": {"evaluated_dialogue_ids": ["DEMO001#1"], "excluded_dialogues": []}, "B1": {"status": "insufficient_evidence", "summary": "测试只含一段", "findings": []}, "B2": {"status": "descriptive_only", "summary": "", "findings": []}, "B3": {"status": status, "summary": "", "comparisons": comparisons}, "summary": "", "limitations": []}
        elif "# 用户表达真实性评测 单段对话" in prompt:
            data = {"assessment_status": "scored", "overall_score": 5, "findings": [], "summary": "", "limitations": []}
        elif "# USER 自然流畅评测 Turn 级" in prompt:
            data = {"assessment_status": "scored", "score": 5, "checks": {f"T{i}": {"status": "no_issue", "reason": "测试占位理由"} for i in range(1, 4)}, "issues": [], "summary": "", "limitations": []}
        elif "# USER 自然流畅评测 Dialogue 级" in prompt:
            data = {"assessment_status": "scored", "score": 5, "checks": {f"D{i}": {"status": "no_issue", "reason": "测试占位理由"} for i in range(1, 4)}, "issues": [], "summary": "", "limitations": []}
        elif "# USER 信息泄漏评测" in prompt:
            data = {"assessment_status": "assessed", "score": 5, "violations": [], "uncertain_cases": [], "limitations": [], "summary": ""}
        else:
            raise AssertionError("unknown prompt")
        return json.dumps(data, ensure_ascii=False), {"model": "mock", "usage": {"total_tokens": 1}}


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.sim, self.case, self.real, self.output = [self.root / x for x in ("sim", "cases", "real", "out")]
        for p in (self.sim, self.case, self.real):
            p.mkdir()
        (self.sim / "DEMO001#1.md").write_text("## DEMO001#1\n- case_id: DEMO001\n- session_index: 1\n- solution_status: not_solved\n\n**User:** 网络不通。\n**Assistant:** 重连后如何？\n**User:** 还是不行。\n", encoding="utf-8")
        (self.case / "DEMO001.md").write_text("用户初始知道网络不通。", encoding="utf-8")
        (self.real / "REAL001#1.md").write_text("## REAL001#1\n- case_id: REAL001\n- session_index: 1\n\n**User:** 网络不通。\n**Assistant:** 重连后如何？\n**User:** 没好。\n", encoding="utf-8")
        MockJudge.prompts = []
        self.patcher = patch.object(runner, "call_model", MockJudge.call)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_end_to_end_real_comparison_and_resume(self):
        args = ["--dialogues", str(self.sim), "--cases", str(self.case), "--real-dialogues", str(self.real), "--output", str(self.output), "--model", "mock", "--base-url", "http://mock.invalid/v1", "--min-comparison-n", "1", "--retries", "0"]
        self.assertEqual(runner.main(args), 0)
        manifest = runner.read_json(self.output / "manifest.json")
        self.assertEqual(manifest["feature_stat_count"], 7)
        stats = runner.read_json(self.output / "feature_statistics.json")["batch_001"]
        self.assertTrue(any(s["feature"] == "assistant_feedback" for s in stats))
        self.assertTrue(any(s["feature"] == "reply_length" for s in stats))
        emotion = next(s for s in stats if s["feature"] == "emotion")
        self.assertEqual(emotion["batch_value"], {"无明显情绪": 1.0})
        self.assertEqual(emotion["reference_value"], {"担忧": 1.0})
        self.assertEqual(emotion["distance"], {"metric": "mean_absolute_prevalence_gap", "value": 0.3333, "unit": "0-1", "direction": "越小越接近；仅为本条件下的分布差异"})
        self.assertEqual(emotion["category_gaps"]["担忧"], -1.0)
        self.assertEqual(emotion["category_gaps"]["无明显情绪"], 1.0)
        self.assertEqual((emotion["batch_dialogue_n"], emotion["reference_dialogue_n"]), (1, 1))
        self.assertEqual(emotion["matching_level"], "context_only")
        formality = next(s for s in stats if s["feature"] == "formality")
        self.assertEqual(formality["distance"]["metric"], "total_variation")
        self.assertEqual(formality["distance"]["value"], 1.0)
        length = next(s for s in stats if s["feature"] == "reply_length")
        self.assertEqual(length["distance"]["metric"], "wasserstein_1")
        self.assertEqual(length["distance"]["value"], 1.0)
        with (self.output / "feature_statistics.csv").open(encoding="utf-8-sig", newline="") as file:
            table = list(csv.DictReader(file))
        self.assertEqual(len(table), 7)
        self.assertEqual(next(row for row in table if row["feature"] == "reply_length")["distance_value"], "1.0")
        self.assertEqual(runner.read_json(self.output / "annotations_simulated.json")[0]["dialogue_id"], "DEMO001#1")
        self.assertEqual(runner.read_json(self.output / "annotations_real.json")[0]["dialogue_id"], "REAL001#1")
        batch = next(j for j in manifest["jobs"] if j["kind"] == "expression_realism_batch")
        self.assertEqual(runner.read_json(self.output / "results" / f"{batch['job_id']}.json")["result"]["B3"]["status"], "compared")
        batch_request = (self.output / "requests" / f"{batch['job_id']}.txt").read_text()
        batch_input = batch_request.split("<evaluation_input>", 1)[1].split("</evaluation_input>", 1)[0]
        labels = json.loads(batch_input.split("# 表达特征标注\n", 1)[1].split("\n# 外部统计", 1)[0])
        self.assertEqual(labels["batch"][0]["messages"][0]["features"]["emotion"], ["无明显情绪"])
        self.assertEqual(labels["reference_sample"][0]["messages"][0]["features"]["emotion"], ["担忧"])
        turn_prompts = [p for p in MockJudge.prompts if "# USER 自然流畅评测 Turn 级" in p]
        self.assertEqual(len(turn_prompts), 1)
        self.assertNotIn("- solution_status: not_solved", turn_prompts[0].split("<evaluation_input>", 1)[1])
        called = len(MockJudge.prompts)
        self.assertEqual(runner.main(args), 0)
        self.assertEqual(len(MockJudge.prompts), called)
        batch_path = self.output / "results" / f"{batch['job_id']}.json"
        damaged = runner.read_json(batch_path)
        damaged["result"]["B3"]["comparisons"] = []
        runner.write_json(batch_path, damaged)
        self.assertEqual(runner.main(args), 0)
        self.assertEqual(len(MockJudge.prompts), called + 1)
        self.assertEqual(len(runner.read_json(batch_path)["result"]["B3"]["comparisons"]), 1)

    def test_b3_without_comparable_statistics_is_insufficient(self):
        args = ["--dialogues", str(self.sim), "--cases", str(self.case), "--real-dialogues", str(self.real), "--output", str(self.output), "--model", "mock", "--base-url", "http://mock.invalid/v1", "--min-comparison-n", "5", "--retries", "0"]
        self.assertEqual(runner.main(args), 0)
        batch = next(j for j in runner.read_json(self.output / "manifest.json")["jobs"] if j["kind"] == "expression_realism_batch")
        result = runner.read_json(self.output / "results" / f"{batch['job_id']}.json")["result"]
        self.assertEqual(result["B3"]["status"], "insufficient_evidence")
        request_text = (self.output / "requests" / f"{batch['job_id']}.txt").read_text()
        body = request_text.split("<evaluation_input>", 1)[1].split("</evaluation_input>", 1)[0]
        self.assertIn("# 表达特征标注", body)
        self.assertNotIn("# 外部统计", body)

    def test_missing_case_is_explicit_skip(self):
        (self.case / "DEMO001.md").unlink()
        self.assertEqual(runner.main(["--dialogues", str(self.sim), "--cases", str(self.case), "--output", str(self.output), "--model", "mock", "--dry-run"]), 0)
        manifest = runner.read_json(self.output / "manifest.json")
        self.assertEqual(manifest["skipped"][0]["kind"], "information_leakage_dialogue")
        self.assertEqual(len(MockJudge.prompts), 0)

    def test_same_scenario_matching_excludes_unmatched_cases(self):
        def message(content, style):
            return {"message_id": "U1", "user_text": content, "status": "annotated", "context": {"tasks": ["描述问题"], "detail_requested": False, "format_requested": False, "has_quoted_material": False, "assistant_feedback": ["无反馈"]}, "features": {"emotion": ["无明显情绪"], "formality": style, "organization": "连续叙述", "politeness": ["无"], "restatement": ["无"]}}

        batch = [{"dialogue_id": "SIM1", "messages": [message("短", "中性")]}, {"dialogue_id": "SIM2", "messages": [message("不应纳入", "正式")]}]
        real = [{"dialogue_id": "REAL1", "messages": [message("较长回答", "正式")]}, {"dialogue_id": "REAL2", "messages": [message("不应纳入", "中性")]}]
        scenarios = {"SIM1": "same", "REAL1": "same", "SIM2": "other-a", "REAL2": "other-b"}
        stats = runner.feature_statistics(batch, real, 1, "助手条件相同", scenarios)
        self.assertEqual(len(stats), 7)
        self.assertTrue(all(s["condition"].startswith("情境=same；") for s in stats))
        self.assertTrue(all(s["matching_level"] == "same_scenario" for s in stats))
        self.assertTrue(all(s["batch_dialogue_n"] == s["reference_dialogue_n"] == 1 for s in stats))
        length = next(s for s in stats if s["feature"] == "reply_length")
        self.assertEqual(length["distance"]["value"], 3.0)

    def test_wasserstein_uses_whole_distribution(self):
        self.assertEqual(runner.wasserstein_1([0, 10], [5]), 5.0)
        self.assertEqual(runner.wasserstein_1([2, 2], [2, 2]), 0.0)

    def test_missing_feedback_label_is_rejected(self):
        dialogue = runner.load_dialogues(self.sim)[0]
        messages = []
        for n, item in enumerate((m for m in dialogue.messages if m["role"] == "user"), 1):
            messages.append({"message_id": f"U{n}", "user_text": item["content"], "status": "annotated", "context": {}, "features": {}})
        with self.assertRaisesRegex(ValueError, "assistant_feedback"):
            runner.validate_annotation_set({dialogue.dialogue_id: messages}, [dialogue])

    def test_markdown_headings_and_quoted_roles_remain_in_message(self):
        path = self.sim / "EDGE001#1.md"
        path.write_text(
            "## EDGE001#1\n- case_id: EDGE001\n- session_index: 1\n\n"
            "**User:** 请说明步骤。\n"
            "**Assistant:**\n## 排查步骤\n第一步先重启。\n"
            "```text\n**User:** 这是代码块中的文字\n```\n"
            "> **User:** 这是引用中的文字\n"
            "**User:** 已重启，接下来呢？\n",
            encoding="utf-8",
        )
        dialogue = next(d for d in runner.load_dialogues(self.sim) if d.dialogue_id == "EDGE001#1")
        self.assertEqual([m["role"] for m in dialogue.messages], ["user", "assistant", "user"])
        self.assertIn("## 排查步骤", dialogue.messages[1]["content"])
        self.assertIn("这是代码块中的文字", dialogue.messages[1]["content"])
        self.assertIn("这是引用中的文字", dialogue.messages[1]["content"])

    def test_invalid_model_result_is_not_accepted_as_score(self):
        with self.assertRaisesRegex(ValueError, "评分状态"):
            runner.validate_output("natural_flow_turn", {"assessment_status": "scored", "score": None, "checks": {}, "issues": []})
        batch = {"scope": {}, "B1": {"status": "no_issue", "findings": []}, "B2": {"status": "descriptive_only", "findings": []}, "B3": {"status": "compared", "comparisons": [{"stat_id": "MADE_UP"}]}}
        with self.assertRaisesRegex(ValueError, "不存在的统计编号"):
            runner.validate_output("expression_realism_batch", batch, ["B3_0001"], True)

    def test_unscored_requires_null_and_checks_cannot_be_empty(self):
        for score in (0, 99, "5", False):
            with self.subTest(score=score), self.assertRaisesRegex(ValueError, "评分状态"):
                runner.validate_output("natural_flow_turn", {"assessment_status": "insufficient_evidence", "score": score, "checks": {}, "issues": []})
        with self.assertRaisesRegex(ValueError, "checks"):
            runner.validate_output("natural_flow_turn", {"assessment_status": "scored", "score": 5, "checks": {}, "issues": []})
        checks = {f"T{i}": {"status": "insufficient_evidence", "reason": "缺少上下文"} for i in range(1, 4)}
        with self.assertRaisesRegex(ValueError, "全部小项不可评"):
            runner.validate_output("natural_flow_turn", {"assessment_status": "scored", "score": 5, "checks": checks, "issues": []})

    def test_leakage_deductions_are_checked(self):
        data = {"assessment_status": "assessed", "score": 4, "violations": [{"severity": "medium", "deduction": 2}]}
        with self.assertRaisesRegex(ValueError, "扣分总和"):
            runner.validate_output("information_leakage_dialogue", data)
        data["score"] = 3
        runner.validate_output("information_leakage_dialogue", data)
        data["violations"][0]["deduction"] = 1
        with self.assertRaisesRegex(ValueError, "deduction"):
            runner.validate_output("information_leakage_dialogue", data)

    def test_annotation_context_types_and_evidence_are_checked(self):
        dialogue = runner.load_dialogues(self.sim)[0]
        item = runner.make_annotation_jobs([dialogue], "mock", 4, "sim")[0]
        raw, _ = MockJudge.call(item["prompt"], "mock", "", "", 1, 0)
        original = json.loads(raw)
        mutations = [
            lambda m: m["context"].update(detail_requested="false"),
            lambda m: m["context"].update(tasks=["未知标签"]),
            lambda m: m["features"].pop("emotion"),
            lambda m: m.update(status="success"),
            lambda m: m["features"].update(emotion=None),
            lambda m: m.update(evidence=[{"message_id": "U1", "quote": "不在原文中的内容"}]),
            lambda m: m.update(evidence=[{"message_id": "U2", "quote": "还是不行。"}]),
        ]
        for mutate in mutations:
            data = copy.deepcopy(original)
            mutate(data["dialogues"][0]["messages"][0])
            with self.assertRaises(ValueError):
                runner.validate_job_result(item, data)

    def test_failed_annotation_can_be_retried_and_bad_cache_is_replaced(self):
        item = runner.make_annotation_jobs(runner.load_dialogues(self.sim), "mock", 4, "sim")[0]
        raw, meta = MockJudge.call(item["prompt"], "mock", "", "", 1, 0)
        data = json.loads(raw)
        data["dialogues"][0]["messages"][0]["context"]["detail_requested"] = "false"
        bad_raw = json.dumps(data)
        with patch.object(runner, "call_model", return_value=(bad_raw, meta)):
            self.assertEqual(runner.execute_jobs([item], self.output, "http://mock", "", 1, 0, False), (0, 1))
        dest = self.output / "results" / (item["job_id"] + ".json")
        self.assertEqual(runner.read_json(dest)["raw_response"], bad_raw)
        self.assertEqual(runner.execute_jobs([item], self.output, "http://mock", "", 1, 0, False), (1, 0))
        cached = runner.read_json(dest)
        cached["result"] = data
        runner.write_json(dest, cached)
        calls = len(MockJudge.prompts)
        self.assertEqual(runner.execute_jobs([item], self.output, "http://mock", "", 1, 0, False), (1, 0))
        self.assertEqual(len(MockJudge.prompts), calls + 1)

    def test_force_service_change_and_truncated_completion(self):
        item = runner.make_core_jobs(runner.load_dialogues(self.sim), {}, "mock")[0][0]
        args = ([item], self.output, "http://service-a", "", 1, 0, False)
        self.assertEqual(runner.execute_jobs(*args), (1, 0))
        count = len(MockJudge.prompts)
        runner.execute_jobs(*args)
        self.assertEqual(len(MockJudge.prompts), count)
        runner.execute_jobs(*args, force=True)
        self.assertEqual(len(MockJudge.prompts), count + 1)
        runner.execute_jobs([item], self.output, "http://service-b", "", 1, 0, False)
        self.assertEqual(len(MockJudge.prompts), count + 2)
        raw, _ = MockJudge.call(item["prompt"], "mock", "", "", 1, 0)
        with patch.object(runner, "call_model", return_value=(raw, {"finish_reason": "length"})):
            self.assertEqual(runner.execute_jobs(*args, force=True), (0, 1))
        self.assertEqual(runner.read_json(self.output / "results" / (item["job_id"] + ".json"))["raw_response"], raw)

    def test_stale_aggregates_are_removed_and_preview_is_not_completed(self):
        args = ["--dialogues", str(self.sim), "--cases", str(self.case), "--output", str(self.output), "--model", "mock", "--base-url", "http://mock"]
        self.assertEqual(runner.main(args + ["--real-dialogues", str(self.real), "--min-comparison-n", "1"]), 0)
        coverage = runner.read_json(self.output / "comparison_coverage.json")["batch_001"]
        self.assertEqual(coverage["groups"]["batch"]["used_in_any_stat_message_n"], 2)
        self.assertEqual(runner.main(args + ["--dry-run"]), 0)
        self.assertFalse((self.output / "feature_statistics.json").exists())
        self.assertFalse((self.output / "annotations_real.json").exists())
        summary = runner.read_json(self.output / "summary.json")
        self.assertTrue(all(key.endswith(":not_run") for key in summary["status_counts"]))

    def test_real_sample_matches_batch_and_core_omits_outcome(self):
        sim = runner.load_dialogues(self.sim)
        real = runner.load_dialogues(self.real)
        matched = runner.Dialogue("MATCH#1", sim[0].case_id, 1, real[0].messages, "test", {})
        item = runner.make_batch_jobs(sim, real + [matched], "mock", 1, {}, "未提供", "测试")[0]
        section = item["prompt"].split("<evaluation_input>", 1)[1]
        self.assertIn("## MATCH#1", section)
        self.assertNotIn("## REAL001#1", section)
        cases = {sim[0].case_id: {"case_name": "测试", "text": ["现象"], "source_note": "不完整快照"}}
        jobs, _ = runner.make_core_jobs(sim, cases, "mock")
        self.assertTrue(all("- solution_status: not_solved" not in j["prompt"].split("## DEMO001#1")[-1] for j in jobs))
        self.assertIn("不完整快照", next(j["prompt"] for j in jobs if j["kind"] == "information_leakage_dialogue"))

    def test_inline_and_unclosed_fences(self):
        path = self.sim / "FENCE#1.md"
        path.write_text("**User:** 看一下\n**Assistant:** ```text\n**User:** 引用而非消息\n```\n**User:** 好的", encoding="utf-8")
        result = runner.parse_markdown_dialogues(path)[0]
        self.assertEqual(len(result.messages), 3)
        self.assertIn("引用而非消息", result.messages[1]["content"])
        path.write_text("**User:** 看一下\n**Assistant:** ```text\n**User:** 引用", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "未闭合"):
            runner.parse_markdown_dialogues(path)

    def test_coverage_deduplicates_tasks_and_records_unmatched_samples(self):
        ds = runner.load_dialogues(self.sim)
        item = runner.make_annotation_jobs(ds, "mock", 4, "sim")[0]
        raw, _ = MockJudge.call(item["prompt"], "mock", "", "", 1, 0)
        batch = json.loads(raw)["dialogues"]
        batch[0]["messages"][0]["context"]["tasks"] = ["描述问题", "回答问题"]
        extra = copy.deepcopy(batch[0])
        extra["dialogue_id"] = "UNMATCHED"
        reference = copy.deepcopy(batch)
        reference[0]["dialogue_id"] = "REFERENCE"
        batch.append(extra)
        mapping = {ds[0].dialogue_id: "same", "REFERENCE": "same", "UNMATCHED": "other"}
        stats = runner.feature_statistics(batch, reference, 1, "test", mapping)
        coverage = runner.comparison_coverage(batch, reference, stats, mapping)
        self.assertEqual(coverage["groups"]["batch"]["input_user_message_n"], 4)
        self.assertEqual(coverage["groups"]["batch"]["excluded_by_scenario_n"], 2)
        self.assertEqual(coverage["groups"]["batch"]["used_in_any_stat_message_n"], 2)
        empty_stats = runner.feature_statistics(batch, reference, 5, "test", mapping)
        self.assertEqual(empty_stats, [])
        limited = runner.comparison_coverage(batch, reference, empty_stats, mapping)
        self.assertEqual(limited["groups"]["batch"]["eligible_but_no_stat_n"], 2)

    def test_population_metadata_routing_and_batch_guards(self):
        d = runner.make_dialogue({"case_id": "TEST", "session_index": 1, "persona_id": "p1", "group_id": "g1", "user_context": {"style": "简短"}, "messages": [{"role": "user", "content": "你好"}]}, Path("test.json"))
        item = runner.make_batch_jobs([d], [], "mock", 8, {}, "未提供", "未知")[0]
        for value in ("persona_id", "group_id", "简短"):
            self.assertIn(value, item["prompt"].split("<evaluation_input>", 1)[1])
        raw, _ = MockJudge.call(item["prompt"], "mock", "", "", 1, 0)
        result = json.loads(raw)
        result["B1"]["status"] = "no_issue"
        with self.assertRaisesRegex(ValueError, "B1"):
            runner.validate_job_result(item, result)
        result["B1"]["status"] = "insufficient_evidence"
        result["B2"]["status"] = "issue"
        with self.assertRaisesRegex(ValueError, "B2"):
            runner.validate_job_result(item, result)

    def test_local_api_config_reaches_model_without_entering_results(self):
        config = self.root / "api.json"
        runner.write_json(config, {"base_url": "http://test.invalid/v1", "model": "configured-judge", "api_key": "temporary-test-key"})
        supplied = []

        def checked_call(prompt, model, base_url, api_key, timeout, retries):
            supplied.append((model, base_url, api_key))
            return MockJudge.call(prompt, model, base_url, api_key, timeout, retries)

        with patch.object(runner, "call_model", checked_call):
            result = runner.main(["--dialogues", str(self.sim), "--cases", str(self.case), "--output", str(self.output), "--api-config", str(config)])
        self.assertEqual(result, 0)
        self.assertTrue(supplied)
        self.assertTrue(all(x == ("configured-judge", "http://test.invalid/v1", "temporary-test-key") for x in supplied))
        self.assertNotIn("temporary-test-key", (self.output / "manifest.json").read_text())
        self.assertNotIn("temporary-test-key", (self.output / "summary.json").read_text())

    def test_http_adapter_uses_chat_completions_without_exposing_key(self):
        class Reply:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                pass

            def read(self):
                return json.dumps({"choices": [{"message": {"content": "{\"score\":5}"}}], "model": "mock"}).encode()

        def fake_open(req, timeout):
            self.assertEqual(req.full_url, "https://example.invalid/v1/chat/completions")
            self.assertEqual(req.get_header("Authorization"), "Bearer secret")
            sent = json.loads(req.data)
            self.assertEqual(sent["messages"][0]["content"], "hello")
            return Reply()

        with patch("eval_pipeline.runner.request.urlopen", fake_open):
            result, meta = REAL_CALL_MODEL("hello", "mock", "https://example.invalid/v1", "secret", 1, 0)
        self.assertEqual(result, '{"score":5}')
        self.assertEqual(meta["model"], "mock")


if __name__ == "__main__":
    unittest.main()
