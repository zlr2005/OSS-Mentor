import unittest
from oss_mentor.task_features import extract_task_features


class ContentReviewRulesTests(unittest.TestCase):
    def features(self, title, body, labels=None):
        return extract_task_features({'title': title, 'body_text': body, 'labels': labels or [],
                                      'primary_language': 'Python', 'comment_count': 0})

    def quality(self, features):
        return features.feature_evidence['difficulty_assessment']['information_quality']['actionability']

    def test_explicit_repository_bug_label_aliases(self):
        for label in ['Issue-Bug', '>bug', '🐛 bug']:
            with self.subTest(label=label):
                self.assertIn('bug_fix', self.features('Parser case', 'Fix the parser result for nested values.', [label]).task_types)

    def test_template_proposal_heading_is_not_an_unresolved_design(self):
        f = self.features('Add parser option', '### Proposal\nAdd an optional strict flag. Expected behavior: reject empty input when enabled.')
        self.assertEqual(self.quality(f), 'actionable')

    def test_real_design_choices_remain_pending(self):
        f = self.features('Add parser option', '### Proposal\nWe have several alternative designs. Which approach should we use?')
        self.assertEqual(self.quality(f), 'design_pending')

    def test_discussion_label_is_not_overridden_by_template_fix(self):
        f = self.features('Add parser option', '### Proposal\nAdd an optional strict flag. Expected behavior: reject empty input.', ['needs discussion'])
        self.assertEqual(self.quality(f), 'design_pending')

    def test_tracker_reference_does_not_turn_bug_into_tracker(self):
        f = self.features('Fix parser crash', 'Steps to reproduce: parse an empty list. Expected behavior: return an empty result. Related tracking issue: #123.')
        self.assertEqual(self.quality(f), 'actionable')

    def test_actual_tracker_stays_non_actionable(self):
        f = self.features('Parser work', 'This issue tracks all parser migration tasks. Add cases listed in the child issues.')
        self.assertEqual(self.quality(f), 'non_actionable')

    def test_filled_report_can_establish_bug_type_and_actionability(self):
        body = ('### Describe the bug\nHeader names with different capitalization appear as separate keys.\n'
                '### To Reproduce\n```python\nheaders.add("X-Foo", "a"); headers.add("x-foo", "b"); print(list(headers))\n```\n'
                '### Expected behavior\nThe two header names should identify one case-insensitive key.')
        f = self.features('Header key enumeration', body)
        self.assertIn('bug_fix', f.task_types)
        self.assertEqual(self.quality(f), 'actionable')
        self.assertTrue(any(e['rule_id']=='bug.body.filled_reproduction_report'
                            for e in f.feature_evidence['task_type_evidence']['bug_fix']))

    def test_empty_or_instruction_only_templates_do_not_establish_a_report(self):
        for filler in ['_No response_', 'Please describe the problem and provide a reproduction below.']:
            f = self.features('Header key enumeration', f'### Describe the bug\n{filler}\n### To Reproduce\n{filler}')
            self.assertEqual(f.task_types, ('other',))
            self.assertNotEqual(self.quality(f), 'actionable')

    def test_named_exception_and_repeated_deoptimization_are_symptoms(self):
        for title in ['EmptyStreamReader.total_raw_bytes raises AttributeError on an empty body',
                      'readWord deoptimizes on every keyword token under JavaScriptCore']:
            self.assertIn('bug_fix', self.features(title, 'Fix the demonstrated runtime behavior.').task_types)

    def test_static_hypothetical_benchmark_risk_is_not_a_defined_fix(self):
        body = ('### 问题描述\n基准测试在线程结束后销毁，并不会引发实际内存泄漏。复制到另一个部署环境可能有风险。\n'
                '### 重现步骤\n静态代码结构观察：存在一个没有清理的 ThreadLocal 缓存，仅运行在隔离的基准测试进程。')
        self.assertEqual(self.quality(self.features('Benchmark 缓存风险', body, ['bug'])), 'unclear')

    def test_filled_chinese_report_has_an_explicit_expected_result(self):
        body = ('### 问题描述\n反序列化字符串数组时，只有一项的数组会丢失原有的方括号，多项数组保留。\n'
                '### 重现步骤\n```java\nString input = "[one]"; parse(input); printTheResultAndCompare();\n```\n'
                '### 期待的正确结果\n反序列化后应完整保留字符串中的方括号。')
        self.assertEqual(self.quality(self.features('字符串数组行为', body, ['bug'])), 'actionable')

    def test_chinese_expected_result_instruction_is_not_an_actual_result(self):
        body = ('### 问题描述\n反序列化字符串数组时，只有一项的数组会丢失原有的方括号，多项数组保留。\n'
                '### 重现步骤\n```java\nString input = "[one]"; parse(input); printTheResultAndCompare();\n```\n'
                '### 期待的正确结果\n*你期待的正确结果是什么？请填写在这里。*')
        self.assertEqual(self.quality(self.features('字符串数组行为', body, ['bug'])), 'unclear')

    def test_dependency_automation_configuration_is_tooling(self):
        f = self.features('Migrate to a less uptight Dependabot config', 'Change the update schedule to reduce noise.')
        self.assertIn('build_tooling', f.task_types)


if __name__ == '__main__':
    unittest.main()
