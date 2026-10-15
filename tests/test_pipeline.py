"""Offline regression tests. Run: python3 -m unittest discover -s tests -v"""
import contextlib
import copy
import csv
import io
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from eval_pipeline import runner as r


def checks(prefix):
    return {f'{prefix}{i}': {'status': 'no_issue', 'score': 5, 'reason': '可见内容有依据',
                            ('event_ids' if prefix == 'L' else 'evidence'): []}
            for i in range(1, 5 if prefix == 'L' else 4)}


def judge(prompt, *args):
    if prompt.startswith('# 案例信息核验依据提取'):
        case = json.loads(prompt.split('<case>\n')[1].split('</case>')[0])
        cid, body = next(iter(case.items()))
        source = {'location': 'text[0]', 'quote': body['text'][0]}
        data = {'schema_version': 'case_evidence_v1', 'case_id': cid, 'assessment_status': 'ready',
                'facts': [{'fact_id': 'F1', 'content': '初始症状', 'source': source, 'level': 1,
                           'level_evidence': source, 'is_answer': False, 'importance': 'low',
                           'conditions': [], 'reason': '原文说明初始症状'}], 'limitations': []}
    elif prompt.startswith('# USER 信息泄漏'):
        evidence = json.loads(prompt.split('<case_evidence>\n')[1].split('</case_evidence>')[0])
        assert evidence['facts'][0]['fact_id'] == 'F1'
        data = {'assessment_status': 'assessed', 'score': 5, 'checks': checks('L'), 'violations': [], 'limitations': []}
    elif prompt.startswith('# USER 自然流畅'):
        prefix = 'T' if 'Turn 级' in prompt.splitlines()[0] else 'D'
        data = {'assessment_status': 'scored', 'score': 5, 'checks': checks(prefix), 'issues': []}
    elif prompt.startswith('# 用户表达真实性评测 单段'):
        data = {'assessment_status': 'scored', 'overall_score': 5, 'checks': checks('R'), 'findings': []}
    elif prompt.startswith('# 用户表达特征标注'):
        section = prompt.split('<dialogue_data>')[1].split('</dialogue_data>')[0]
        dialogues = []
        for block in re.split(r'(?m)(?=^## )', section):
            ids = re.findall(r'(?m)^##\s+(.+)$', block)
            if not ids:
                continue
            messages = []
            for i, text in enumerate(re.findall(r'(?m)^\*\*User:\*\* (.+)$', block), 1):
                messages.append({'message_id': f'U{i}', 'user_text': text, 'status': 'annotated',
                                 'context': {'tasks': ['描述问题'], 'detail_requested': False, 'format_requested': False,
                                             'has_quoted_material': False, 'quoted_spans': [], 'assistant_feedback': ['无反馈']},
                                 'features': {'emotion': ['无明显情绪'], 'formality': '中性', 'organization': '连续叙述',
                                              'politeness': ['无'], 'restatement': ['无']}, 'evidence': [], 'limitations': []})
            dialogues.append({'dialogue_id': ids[0], 'messages': messages, 'limitations': []})
        data = {'schema_version': 'expression_features_v1', 'dialogues': dialogues, 'excluded_dialogues': []}
    elif prompt.startswith('# 用户表达真实性评测 跨对话'):
        section = prompt.split('<evaluation_input>')[1].split('</evaluation_input>')[0]
        stats = json.loads(section.split('# 外部统计\n')[1]) if '# 外部统计\n' in section else []
        status = 'compared' if stats else 'insufficient_evidence' if '# 真实参照对话' in section else 'not_evaluated'
        data = {'scope': {}, 'B1': {'status': 'insufficient_evidence', 'findings': []},
                'B2': {'status': 'descriptive_only', 'findings': []},
                'B3': {'status': status, 'comparisons': [{'stat_id': stats[0]['stat_id']}] if stats else []}}
    else:
        raise AssertionError(prompt[:100])
    return '<think>测试思考</think>\n' + json.dumps(data, ensure_ascii=False), {'finish_reason': 'stop'}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sim, self.cases, self.real, self.out = [self.root / n for n in ('sim.json', 'cases.json', 'real.json', 'out')]
        messages = [{'role': 'user', 'content': '网络不通。'}, {'role': 'assistant', 'content': '重连后如何？'}, {'role': 'user', 'content': '还是不行。'}]
        r.write_json(self.sim, [{'case_id': 'CASE1', 'session_index': i, 'messages': messages} for i in (1, 2)])
        r.write_json(self.cases, [{'case_id': 'CASE1', 'text': ['用户初始知道网络不通。']}])
        r.write_json(self.real, [{'100': [{'用户': '网络不通。'}, {'客服': '重连后如何？'}, {'用户': '还是不行。'}], 'caseID': ['CASE1']},
                                 {'200': [{'用户': '无关案例不应标注'}], 'caseID': ['OTHER']}])
        self.args = ['--dialogues', str(self.sim), '--cases', str(self.cases), '--real-dialogues', str(self.real),
                     '--output', str(self.out), '--model', 'mock', '--base-url', 'http://mock.invalid/v1',
                     '--limit-dialogues', '1', '--min-comparison-n', '1', '--retries', '0']
        self.output = self.out / 'dialogues_00001-00001'

    def run_main(self, args=None):
        with contextlib.redirect_stdout(io.StringIO()):
            return r.main(self.args if args is None else args)

    def test_end_to_end_scores_evidence_labels_and_resume(self):
        with patch.object(r, 'call_model', side_effect=judge) as mock:
            self.assertEqual(self.run_main(), 0)
            calls = mock.call_count
            self.assertEqual(self.run_main(), 0)
            self.assertEqual(mock.call_count, calls)
        index = r.read_json(self.output / 'case_evidence_index.json')
        saved = r.read_json(Path(index['CASE1']['path']))['result']
        manifest = r.read_json(self.output / 'manifest.json')
        leak = next(j for j in manifest['jobs'] if j['kind'] == 'information_leakage_dialogue')
        prompt = (self.output / 'requests' / (leak['job_id'] + '.txt')).read_text()
        self.assertEqual(json.loads(prompt.split('<case_evidence>\n')[1].split('</case_evidence>')[0]), saved)
        self.assertEqual(manifest['real_reference_selection']['matched'], 1)
        self.assertTrue((self.output / 'annotations_simulated.json').exists())
        self.assertTrue((self.output / 'annotations_real.json').exists())
        batch = next(j for j in manifest['jobs'] if j['kind'] == 'expression_realism_batch')
        batch_prompt = (self.output / 'requests' / (batch['job_id'] + '.txt')).read_text()
        self.assertIn('# 表达特征标注', batch_prompt)
        self.assertNotIn('无关案例不应标注', batch_prompt)
        self.assertEqual(r.read_json(self.output / 'results' / (batch['job_id'] + '.json'))['result']['B3']['status'], 'compared')
        with (self.output / 'results.csv').open(encoding='utf-8-sig') as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(next(x for x in rows if x['kind'] == 'information_leakage_dialogue')['L1_score'], '5')
        self.assertEqual(next(x for x in rows if x['kind'] == 'natural_flow_turn')['T1_score'], '5')
        self.assertEqual(next(x for x in rows if x['kind'] == 'expression_realism_dialogue')['R1_score'], '5')

    def test_case_cache_cross_slice_and_source_invalidation(self):
        with patch.object(r, 'call_model', side_effect=judge) as mock:
            self.assertEqual(self.run_main(), 0)
            self.assertEqual(self.run_main(self.args + ['--start-dialogue', '2']), 0)
            count = lambda: sum(c.args[0].startswith('# 案例信息') for c in mock.call_args_list)
            self.assertEqual(count(), 1)
            r.write_json(self.cases, [{'case_id': 'CASE1', 'text': ['用户初始知道网络不通，无法登录。']}])
            self.assertEqual(self.run_main(), 0)
            self.assertEqual(count(), 2)

    def test_bad_case_evidence_blocks_leakage_only(self):
        def bad(prompt, *args):
            raw, meta = judge(prompt, *args)
            if prompt.startswith('# 案例信息'):
                data = r.parse_model_json(raw)
                data['facts'][0]['source']['quote'] = '伪造引文'
                return json.dumps(data), meta
            return raw, meta
        with patch.object(r, 'call_model', side_effect=bad) as mock:
            self.assertEqual(self.run_main(), 1)
        self.assertFalse(any(c.args[0].startswith('# USER 信息泄漏') for c in mock.call_args_list))
        self.assertTrue(any(c.args[0].startswith('# USER 自然流畅') for c in mock.call_args_list))
        self.assertIn('案例核验依据失败', r.read_json(self.output / 'summary.json')['skipped'][0]['reason'])

    def test_dry_run_dependencies_without_calls(self):
        with patch.object(r, 'call_model') as mock:
            self.assertEqual(self.run_main(self.args + ['--dry-run']), 0)
            mock.assert_not_called()
        manifest = r.read_json(self.output / 'manifest.json')
        self.assertEqual(manifest['pending_leakage'], ['CASE1#1'])
        self.assertFalse(any(j['kind'] == 'information_leakage_dialogue' for j in manifest['jobs']))

    def test_small_scores_and_event_deduction(self):
        data = {'assessment_status': 'partial', 'score': 2, 'checks': checks('L'), 'violations': [
            {'event_id': 'E1', 'category': 'L1', 'severity': 'low', 'deduction': 1},
            {'event_id': 'E2', 'category': 'L2', 'severity': 'medium', 'deduction': 2}]}
        for key, score, eid in [('L1', 4, 'E1'), ('L2', 3, 'E2')]:
            data['checks'][key].update(status='issue', score=score, event_ids=[eid])
        data['checks']['L3'].update(status='insufficient_evidence', score=None)
        data['checks']['L4'].update(status='not_applicable', score=None)
        r.validate_output('information_leakage_dialogue', data)
        for mutate in (lambda d: d['checks']['L3'].update(score=5),
                       lambda d: d['checks']['L1'].update(score=3),
                       lambda d: d['violations'][1].update(event_id='E1'),
                       lambda d: d.update(score=3)):
            bad = copy.deepcopy(data); mutate(bad)
            with self.assertRaises(ValueError):
                r.validate_output('information_leakage_dialogue', bad)

    def test_leakage_requires_existing_fact_and_condition(self):
        dialogues, cases = r.load_dialogues(self.sim), r.load_cases(self.cases)
        case_job = r.make_case_jobs(dialogues, cases, 'mock')[0]
        basis = r.parse_model_json(judge(case_job['prompt'])[0])
        basis['facts'][0]['conditions'] = [{'type': 'disclosure', 'description': '测试条件',
                                            'source': basis['facts'][0]['source']}]
        jobs, _ = r.make_core_jobs(dialogues[:1], cases, 'mock', {'CASE1': basis})
        item = next(j for j in jobs if j['kind'] == 'information_leakage_dialogue')
        data = {'assessment_status': 'assessed', 'score': 4, 'checks': checks('L'),
                'violations': [{'event_id': 'E1', 'category': 'L1', 'severity': 'low', 'deduction': 1, 'fact_ids': ['F1']}]}
        data['checks']['L1'].update(status='issue', score=4, event_ids=['E1'])
        r.validate_job_result(item, data)
        data['violations'][0]['fact_ids'] = ['F999']
        with self.assertRaisesRegex(ValueError, '事实编号'):
            r.validate_job_result(item, data)
        data['violations'][0]['fact_ids'] = ['F1']
        basis['facts'][0]['conditions'] = []
        with self.assertRaisesRegex(ValueError, '没有获知'):
            r.validate_job_result(item, data)

    def test_case_field_errors_identify_exact_field(self):
        item = r.make_case_jobs(r.load_dialogues(self.sim), r.load_cases(self.cases), 'mock')[0]
        data = r.parse_model_json(judge(item['prompt'])[0])
        for field, value in [('level', '一级'), ('is_answer', 'false'), ('importance', '高'), ('importance', [])]:
            broken = copy.deepcopy(data)
            broken['facts'][0][field] = value
            with self.assertRaisesRegex(ValueError, 'F1.' + field):
                r.validate_job_result(item, broken)

    def test_numeric_string_levels_recover_from_saved_raw_without_api_call(self):
        item = r.make_case_jobs(r.load_dialogues(self.sim), r.load_cases(self.cases), 'mock')[0]
        raw, meta = judge(item['prompt'])
        data = r.parse_model_json(raw)
        data['facts'][0]['level'] = '1'
        raw = '<think>已完成思考</think>\n```json\n' + json.dumps(data, ensure_ascii=False) + '\n```'
        dest = self.out / 'results' / (item['job_id'] + '.json')
        r.write_json(dest, {**{k: item[k] for k in ('job_id', 'kind', 'label', 'model', 'prompt_sha256')},
                            'status': 'failed', 'error': '案例事实分级无效', 'raw_response': raw, 'api': meta,
                            'service_sha256': r.digest(r.api_endpoint('http://mock.invalid/v1'))})
        with patch.object(r, 'call_model') as mock, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(r.execute_jobs([item], self.out, 'http://mock.invalid/v1', '', 1, 0, False), (1, 0))
            mock.assert_not_called()
        saved = r.read_json(dest)
        self.assertEqual(saved['result']['facts'][0]['level'], 1)
        self.assertTrue(saved['recovered_from_raw_response'])

    def test_unambiguous_formats_recover_across_other_modules(self):
        dialogues, cases = r.load_dialogues(self.sim), r.load_cases(self.cases)
        core, _ = r.make_core_jobs(dialogues[:1], cases, 'mock')
        turn = next(j for j in core if j['kind'] == 'natural_flow_turn')
        realism = next(j for j in core if j['kind'] == 'expression_realism_dialogue')
        ann = r.make_annotation_jobs(dialogues[:1], 'mock', 1, 'sim')[0]
        records = []
        for item in (turn, realism, ann):
            data = r.parse_model_json(judge(item['prompt'])[0])
            if item is turn:
                data['score'] = '5'
                data['checks']['T1']['score'] = '5'
            elif item is realism:
                data['overall_score'] = '5'
                data['checks']['R1']['score'] = '5'
            else:
                first = data['dialogues'][0]['messages'][0]
                first['context']['detail_requested'] = 'false'
                first['context']['tasks'] = '描述问题'
                first['context']['assistant_feedback'] = '无反馈'
                first['features']['emotion'] = '无明显情绪'
                first['features']['formality'] = ['中性']
            dest = self.out / 'results' / (item['job_id'] + '.json')
            r.write_json(dest, {**{k: item[k] for k in ('job_id', 'kind', 'label', 'model', 'prompt_sha256')},
                                'status': 'failed', 'raw_response': '<think>done</think>\n' + json.dumps(data, ensure_ascii=False),
                                'api': {'finish_reason': 'stop'},
                                'service_sha256': r.digest(r.api_endpoint('http://mock.invalid/v1'))})
            records.append((item, dest))
        with patch.object(r, 'call_model') as mock, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(r.execute_jobs([item for item, _ in records], self.out,
                                            'http://mock.invalid/v1', '', 1, 0, False), (3, 0))
            mock.assert_not_called()
        for _, dest in records:
            self.assertTrue(r.read_json(dest)['recovered_from_raw_response'])

    def test_unknown_label_is_not_silently_converted(self):
        data = {'schema_version': 'case_evidence_v1', 'case_id': 'CASE1', 'assessment_status': 'ready',
                'facts': [{'fact_id': 'F1', 'content': 'x', 'source': {'location': 'text[0]', 'quote': 'x'},
                           'level': '大概一级', 'level_evidence': None, 'is_answer': 'maybe',
                           'importance': '大概高', 'conditions': [], 'reason': 'x'}], 'limitations': []}
        normalized = r.normalize_model_result('case_information_evidence', data)
        self.assertEqual(normalized['facts'][0]['level'], '大概一级')
        with self.assertRaisesRegex(ValueError, 'F1.level'):
            r.validate_output('case_information_evidence', normalized)

    def test_realism_null_not_full_score(self):
        data = {'assessment_status': 'scored', 'overall_score': 5, 'checks': checks('R'), 'findings': []}
        data['checks']['R3'].update(status='insufficient_evidence', score=None)
        r.validate_output('expression_realism_dialogue', data)
        data['checks']['R3']['score'] = 5
        with self.assertRaises(ValueError):
            r.validate_output('expression_realism_dialogue', data)

    def test_qwen_and_failed_raw_cache_recovery(self):
        dialogues, cases = r.load_dialogues(self.sim), r.load_cases(self.cases)
        item = r.make_case_jobs(dialogues, cases, 'mock')[0]
        raw, meta = judge(item['prompt'])
        dest = self.out / 'results' / (item['job_id'] + '.json')
        r.write_json(dest, {**{k: item[k] for k in ('job_id', 'kind', 'label', 'model', 'prompt_sha256')},
                           'status': 'failed', 'error': 'old parser', 'raw_response': raw, 'api': meta,
                           'service_sha256': r.digest(r.api_endpoint('http://mock.invalid/v1'))})
        with patch.object(r, 'call_model') as mock, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(r.execute_jobs([item], self.out, 'http://mock.invalid/v1', '', 1, 0, False), (1, 0))
            mock.assert_not_called()
        self.assertTrue(r.read_json(dest)['recovered_from_raw_response'])
        with self.assertRaises(ValueError):
            r.parse_model_json('<think>未闭合 {"score":5}')


if __name__ == '__main__':
    unittest.main()
