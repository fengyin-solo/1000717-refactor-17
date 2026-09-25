import unittest

from app.domain.bridge_profile import (
    annotate_assess_row,
    annotate_bridge_row,
    classify_bridge_scale,
    find_bridge_for_assessment,
    parse_span_layout,
    resolve_bridge_profile,
)
from app.services.assess import AssessService
from app.services.bridge import BridgeService
from app.store import store


class BridgeProfileRulesTest(unittest.TestCase):
    def test_parses_repeated_span_layout(self):
        spans = parse_span_layout("3×30m")
        self.assertEqual(spans, [30.0, 30.0, 30.0])
        self.assertEqual(parse_span_layout("30m×3"), spans)

    def test_preserves_original_type_when_span_is_unknown(self):
        annotated = annotate_bridge_row({"id": 1, "桥梁类型": "未来新型桥"})
        self.assertEqual(annotated, {"id": 1, "桥梁类型": "未来新型桥"})

    def test_parses_unequal_span_layout_and_units(self):
        spans = parse_span_layout("20m+0.04km+20m")
        self.assertEqual(spans, [20.0, 40.0, 20.0])
        profile = resolve_bridge_profile({"桥梁类型": "装配式预应力混凝土连续梁桥", "跨径布置": "20m+0.04km+20m"})
        self.assertEqual(profile.span_length, 40.0)
        self.assertEqual(profile.total_span_length, 80.0)
        self.assertEqual(profile.bridge_type, "梁桥")
        self.assertEqual(profile.scale_type, "大桥")

    def test_uses_explicit_values_before_fallback(self):
        profile = resolve_bridge_profile({
            "桥梁类型": "钢管拱桥",
            "最大跨径": "120m",
            "多孔跨径总长": "240m",
            "桥梁全长": "1.2km",
        })
        self.assertEqual(profile.span_length, 120.0)
        self.assertEqual(profile.total_span_length, 240.0)
        self.assertEqual(profile.bridge_type, "拱桥")
        self.assertEqual(profile.scale_type, "大桥")

    def test_classification_uses_single_and_total_span_thresholds(self):
        self.assertEqual(classify_bridge_scale(max_span=150.0, total_span=500.0), "特大桥")
        self.assertEqual(classify_bridge_scale(max_span=120.0, total_span=300.0), "大桥")
        self.assertEqual(classify_bridge_scale(max_span=20.0, total_span=30.0), "中桥")
        self.assertEqual(classify_bridge_scale(max_span=8.0, total_span=10.0), "小桥")
        self.assertEqual(classify_bridge_scale(max_span=4.0, total_span=7.0), "涵洞")
        self.assertIsNone(classify_bridge_scale(max_span=None, total_span=None))

    def test_unknown_type_keeps_existing_wording(self):
        profile = resolve_bridge_profile({"桥梁类型": "未来新型桥", "最大跨径": 20})
        self.assertEqual(profile.bridge_type, "未来新型桥")
        self.assertEqual(profile.scale_type, "中桥")

    def test_assessment_uses_same_profile_as_bridge_list_and_detail(self):
        bridge = {
            "id": 999901,
            "桥梁编码": "BRID-TEST-001",
            "桥梁名称": "统一规则测试桥",
            "桥梁类型": "双塔斜拉桥",
            "跨径布置": "3×80m",
            "桥梁全长": "300m",
        }
        assessment = {
            "id": 999901,
            "评定编号": "ASSE-TEST-001",
            "评定对象": "BRID-TEST-001 统一规则测试桥",
            "技术等级": "2类",
            "评定结论": "历史结论保持不变",
        }
        rows = store.rows("bridge")
        assess_rows = store.rows("assess")
        rows.append(bridge)
        assess_rows.append(assessment)
        try:
            list_profile = next(
                item["桥梁技术指标"]
                for item in BridgeService().list_entries(keyword="BRID-TEST-001", page=1, size=20)[0]
            )
            detail_profile = BridgeService().get_entry(bridge["id"])["桥梁技术指标"]
            assessment_list_profile = next(
                item["桥梁技术指标"]
                for item in AssessService().list_entries(keyword="ASSE-TEST-001", page=1, size=20)[0]
            )
            assessment_detail_profile = AssessService().get_entry(assessment["id"])["桥梁技术指标"]

            self.assertEqual(list_profile, detail_profile)
            self.assertEqual(list_profile, assessment_list_profile)
            self.assertEqual(list_profile, assessment_detail_profile)
            self.assertEqual(list_profile["跨径"], 80.0)
            self.assertEqual(list_profile["多孔跨径总长"], 240.0)
            self.assertEqual(list_profile["桥梁类型"], "斜拉桥")
            self.assertEqual(list_profile["按跨径分类"], "大桥")
            self.assertEqual(AssessService().get_entry(assessment["id"])["评定结论"], "历史结论保持不变")
        finally:
            rows.remove(bridge)
            assess_rows.remove(assessment)

    def test_annotation_does_not_mutate_source_rows(self):
        bridge = {"id": 999902, "桥梁类型": "T梁桥", "最大跨径": 25}
        assessment = {"id": 999902, "评定对象": "不相关桥梁"}
        annotated = annotate_assess_row(assessment, [bridge])
        self.assertNotIn("桥梁技术指标", bridge)
        self.assertNotIn("桥梁技术指标", assessment)
        self.assertNotIn("桥梁技术指标", annotated)
        self.assertIsNone(find_bridge_for_assessment(assessment, [bridge]))

    def test_seed_placeholders_keep_existing_payload_shape(self):
        bridge_items, bridge_total = BridgeService().list_entries(page=1, size=20)
        assess_items, assess_total = AssessService().list_entries(page=1, size=20)
        self.assertEqual(bridge_total, 3)
        self.assertEqual(assess_total, 3)
        self.assertTrue(all("桥梁技术指标" not in item for item in bridge_items))
        self.assertTrue(all("桥梁技术指标" not in item for item in assess_items))
        self.assertIsNone(BridgeService().get_entry(999999))
        self.assertIsNone(AssessService().get_entry(999999))

    def test_invalid_span_values_are_ignored(self):
        profile = resolve_bridge_profile({"桥梁全长": "样例文字", "最大跨径": -3})
        self.assertIsNone(profile.span_length)
        self.assertIsNone(profile.total_span_length)
        self.assertIsNone(profile.scale_type)


if __name__ == "__main__":
    unittest.main()
