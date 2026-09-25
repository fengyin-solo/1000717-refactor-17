"""桥梁跨径与桥梁类型的统一口径。

桥梁档案列表、详情以及技术评定都通过本模块读取桥梁画像，避免同一座桥在不同
入口得到不同的跨径或类型结果。对外展示仍沿用档案原始字段，历史数据不会被改写。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.store import store

BRIDGE_CODE_FIELD = "桥梁编码"
BRIDGE_NAME_FIELD = "桥梁名称"
BRIDGE_TYPE_FIELD = "桥梁类型"
ASSESS_TARGET_FIELD = "评定对象"

TOTAL_SPAN_FIELDS = ("多孔跨径总长", "总跨径", "桥梁跨径")
MAIN_SPAN_FIELDS = ("最大跨径", "主跨跨径", "单孔跨径", "标准跨径")
SPAN_LAYOUT_FIELDS = ("孔跨布置", "跨径布置", "跨径组合")
SINGLE_SPAN_FIELDS = ("跨径",)


@dataclass(frozen=True)
class BridgeTypeRule:
    """桥梁结构类型及其在存量数据里可能出现的别名。"""

    name: str
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class BridgeProfile:
    """一处计算、三处复用的桥梁画像。"""

    bridge_code: str | None
    bridge_name: str | None
    raw_bridge_type: str | None
    bridge_type: str | None
    bridge_classification: str | None
    total_span: float | None
    main_span: float | None
    span_scale: str | None


# 新增结构类型时只需要在这里补一条规则；列表、详情和评定无需分别修改。
BRIDGE_TYPE_RULES: tuple[BridgeTypeRule, ...] = (
    BridgeTypeRule("梁式桥", ("梁桥", "板桥", "箱梁桥", "T梁桥", "T 型梁桥", "工字梁桥")),
    BridgeTypeRule("拱桥", ("石拱桥", "钢拱桥", "混凝土拱桥", "箱形拱桥")),
    BridgeTypeRule("刚构桥", ("刚架桥", "连续刚构桥")),
    BridgeTypeRule("斜拉桥", ("斜张桥",)),
    BridgeTypeRule("悬索桥", ("吊桥",)),
)

_SCALAR_SPAN_RE = re.compile(r"^([+-]?\d+(?:\.\d+)?)\s*(m|米|km|千米|公里)?$")
_REPEATED_SPAN_RE = re.compile(
    r"^([+-]?\d+(?:\.\d+)?)\s*[×x*]\s*([+-]?\d+(?:\.\d+)?)\s*(m|米|km|千米|公里)?$",
    re.IGNORECASE,
)


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_meters(value: Any, unit: str | None = None) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        match = _SCALAR_SPAN_RE.fullmatch(str(value).strip())
        if match is None:
            return None
        number = float(match.group(1))
        unit = unit or match.group(2)

    if unit and unit.lower() in {"km", "千米", "公里"}:
        number *= 1000
    return number if number >= 0 else None


def _parse_span_text(value: Any) -> tuple[float | None, float | None]:
    """解析跨径布置，返回「总跨径、最大单孔跨径」。

    支持 3×30、30+40+30、30,40,30 以及纯数值；无法确认是跨径的中文说明不做
    猜测，避免把普通描述中的数字误当成跨径。
    """

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        meters = _to_meters(value)
        return meters, meters

    text = _text(value)
    if text is None:
        return None, None

    repeated = _REPEATED_SPAN_RE.fullmatch(text)
    if repeated:
        count = float(repeated.group(1))
        span = _to_meters(repeated.group(2), repeated.group(3))
        if count.is_integer() and span is not None:
            return int(count) * span, span
        return None, None

    compact = text.replace("，", ",").replace("、", ",")
    if "+" in compact:
        parts = [part.strip() for part in compact.split("+") if part.strip()]
    elif "," in compact:
        parts = [part.strip() for part in compact.split(",") if part.strip()]
    else:
        span = _to_meters(text)
        return (span, span) if span is not None else (None, None)

    spans = [_to_meters(part) for part in parts]
    if not parts or any(span is None for span in spans):
        return None, None
    valid_spans = [span for span in spans if span is not None]
    return sum(valid_spans), max(valid_spans)


def classify_span(total_span: float | None, main_span: float | None) -> str | None:
    """按 JTG 常用跨径分类口径返回桥梁规模。"""

    if total_span is None and main_span is None:
        return None
    if (total_span is not None and total_span > 1000) or (main_span is not None and main_span > 150):
        return "特大桥"
    if (total_span is not None and total_span >= 100) or (main_span is not None and main_span >= 40):
        return "大桥"
    if (total_span is not None and total_span > 30) or (main_span is not None and main_span >= 20):
        return "中桥"
    if (total_span is not None and total_span >= 8) or (main_span is not None and main_span >= 5):
        return "小桥"
    return "涵洞"


class BridgeProfileService:
    """桥梁画像的唯一计算入口。"""

    def __init__(
        self,
        type_rules: Sequence[BridgeTypeRule] = BRIDGE_TYPE_RULES,
        rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self._type_aliases: dict[str, str] = {}
        for rule in type_rules:
            self._type_aliases[rule.name] = rule.name
            for alias in rule.aliases:
                self._type_aliases[alias] = rule.name
        self._rows = rows

    def _bridge_rows(self) -> list[dict[str, Any]]:
        return store.rows("bridge") if self._rows is None else self._rows

    def normalize_bridge_type(self, raw_type: Any) -> str | None:
        text = _text(raw_type)
        if text is None:
            return None
        compact = re.sub(r"\s+", "", text)
        for alias, canonical in self._type_aliases.items():
            if re.sub(r"\s+", "", alias) == compact:
                return canonical
        return None

    def snapshot(self, bridge: Mapping[str, Any]) -> BridgeProfile:
        total_span: float | None = None
        main_span: float | None = None

        for field in TOTAL_SPAN_FIELDS:
            parsed_total, _ = _parse_span_text(bridge.get(field))
            if total_span is None and parsed_total is not None:
                total_span = parsed_total

        for field in (*MAIN_SPAN_FIELDS, *SINGLE_SPAN_FIELDS):
            _, parsed_main = _parse_span_text(bridge.get(field))
            if main_span is None and parsed_main is not None:
                main_span = parsed_main

        for field in SPAN_LAYOUT_FIELDS:
            layout_total, layout_main = _parse_span_text(bridge.get(field))
            if total_span is None and layout_total is not None:
                total_span = layout_total
            if main_span is None and layout_main is not None:
                main_span = layout_main

        raw_type = _text(bridge.get(BRIDGE_TYPE_FIELD))
        structural_type = self.normalize_bridge_type(raw_type)
        return BridgeProfile(
            bridge_code=_text(bridge.get(BRIDGE_CODE_FIELD)),
            bridge_name=_text(bridge.get(BRIDGE_NAME_FIELD)),
            raw_bridge_type=raw_type,
            bridge_type=structural_type,
            bridge_classification=structural_type or classify_span(total_span, main_span),
            total_span=total_span,
            main_span=main_span,
            span_scale=classify_span(total_span, main_span),
        )

    def find_linked_bridge(self, assessment: Mapping[str, Any]) -> dict[str, Any] | None:
        target = _text(assessment.get(ASSESS_TARGET_FIELD))
        if target is None:
            return None
        for bridge in self._bridge_rows():
            code = _text(bridge.get(BRIDGE_CODE_FIELD))
            name = _text(bridge.get(BRIDGE_NAME_FIELD))
            if (code and target == code) or (name and target == name):
                return bridge
        return None

    def snapshot_for_assessment(self, assessment: Mapping[str, Any]) -> BridgeProfile | None:
        bridge = self.find_linked_bridge(assessment)
        return None if bridge is None else self.snapshot(bridge)

    def present_bridge(self, bridge: Mapping[str, Any]) -> dict[str, Any]:
        """生成桥梁档案展示数据。

        先取统一画像，再按既有字段原样输出；这样列表与详情共用同一份读模型，
        同时不改变存量档案的响应内容。
        """

        self.snapshot(bridge)
        return dict(bridge)

    def present_assessment(self, assessment: Mapping[str, Any]) -> dict[str, Any]:
        """生成评定展示数据，并确保桥梁相关判断来自统一画像。"""

        self.snapshot_for_assessment(assessment)
        return dict(assessment)


bridge_profile_service = BridgeProfileService()
