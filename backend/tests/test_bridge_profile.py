from __future__ import annotations

import unittest

from app.services.assess import AssessService
from app.services.bridge import BridgeService
from app.services.bridge_profile import (
    BridgeProfileService,
    BridgeTypeRule,
    classify_span,
)


class BridgeProfileTests(unittest.TestCase):
    def test_span_layout_parsing_and_classification(self) -> None:
        service = BridgeProfileService()
        profile = service.snapshot({"桥梁类型": "T梁桥", "跨径布置": "3×40m"})

        self.assertEqual(profile.bridge_type, "梁式桥")
        self.assertEqual(profile.total_span, 120.0)
        self.assertEqual(profile.main_span, 40.0)
        self.assertEqual(profile.span_scale, "大桥")

    def test_span_boundaries(self) -> None:
        self.assertEqual(classify_span(None, None), None)
        self.assertEqual(classify_span(1000, 150), "大桥")
        self.assertEqual(classify_span(1001, None), "特大桥")
        self.assertEqual(classify_span(None, 151), "特大桥")
        self.assertEqual(classify_span(30, 20), "中桥")
        self.assertEqual(classify_span(8, 5), "小桥")
        self.assertEqual(classify_span(7, 4), "涵洞")

    def test_explicit_total_span_does_not_become_main_span(self) -> None:
        service = BridgeProfileService()
        profile = service.snapshot({"桥梁类型": "梁桥", "多孔跨径总长": "120m"})

        self.assertEqual(profile.bridge_type, "梁式桥")
        self.assertEqual(profile.bridge_classification, "梁式桥")
        self.assertEqual(profile.total_span, 120.0)
        self.assertIsNone(profile.main_span)
        self.assertEqual(profile.span_scale, "大桥")

    def test_span_scale_is_used_when_structural_type_is_unknown(self) -> None:
        service = BridgeProfileService()
        profile = service.snapshot({"桥梁类型": "自定义新桥型", "跨径布置": "10+25"})

        self.assertIsNone(profile.bridge_type)
        self.assertEqual(profile.span_scale, "中桥")
        self.assertEqual(profile.bridge_classification, "中桥")

    def test_explicit_span_fields_take_precedence_over_layout(self) -> None:
        service = BridgeProfileService()
        profile = service.snapshot(
            {"桥梁类型": "梁桥", "多孔跨径总长": "1200m", "最大跨径": "50m", "跨径布置": "60+100+60"}
        )

        self.assertEqual(profile.total_span, 1200.0)
        self.assertEqual(profile.main_span, 50.0)
        self.assertEqual(profile.span_scale, "特大桥")

    def test_extending_type_registry_has_one_change_point(self) -> None:
        service = BridgeProfileService(
            (
                BridgeTypeRule("梁式桥"),
                BridgeTypeRule("人行天桥", ("天桥",)),
            )
        )

        self.assertEqual(service.normalize_bridge_type("天桥"), "人行天桥")
        self.assertEqual(service.normalize_bridge_type("梁式桥"), "梁式桥")

    def test_bridge_list_and_detail_share_profile_and_keep_payload(self) -> None:
        service = BridgeService()
        items, total = service.list_entries(page=1, size=100)
        self.assertEqual(total, 3)

        from app.store import store

        for index, item in enumerate(items, start=1):
            self.assertEqual(item, store.rows("bridge")[index - 1])
            self.assertEqual(service.get_entry(index), item)

    def test_assessment_uses_same_linked_bridge_profile_without_changing_history(self) -> None:
        bridge = {
            "id": 1,
            "桥梁编码": "BRID-X1",
            "桥梁名称": "统一口径桥",
            "桥梁类型": "斜拉桥",
            "跨径布置": "60+100+60",
        }
        assessment = {
            "id": 9,
            "评定编号": "ASSE-X1",
            "评定对象": "BRID-X1",
            "技术等级": "历史等级",
            "评定结论": "历史结论不变",
        }
        profile_service = BridgeProfileService(rows=[bridge])
        service = AssessService(profile_service)

        self.assertEqual(
            profile_service.snapshot_for_assessment(assessment),
            profile_service.snapshot(bridge),
        )
        self.assertIs(service.profile_service, profile_service)
        self.assertEqual(profile_service.present_assessment(assessment), assessment)
        self.assertEqual(assessment["技术等级"], "历史等级")
        self.assertEqual(assessment["评定结论"], "历史结论不变")


if __name__ == "__main__":
    unittest.main()
