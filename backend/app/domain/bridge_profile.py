"""桥梁跨径、结构类型与按跨径分级的共用规则。

所有入口（桥梁档案列表、桥梁详情、技术评定）都调用这里的规则；
规则变更或新增桥梁结构类型时，只在本文件调整。

该模块只做纯计算，不读写数据仓库，因此历史记录不会被重算或覆盖。
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable

# 可能记录“最大单孔跨径”的字段。
MAX_SPAN_FIELDS = ("最大跨径", "最大单孔跨径", "单孔跨径", "标准跨径", "计算跨径")
# 分孔信息，例如：3×30m 或 20m+25m+20m。
LAYOUT_FIELDS = ("跨径布置", "孔跨布置", "桥跨布置", "孔径布置")
# 桥梁多孔跨径总长。
TOTAL_SPAN_FIELDS = ("多孔跨径总长", "桥跨总长", "跨径总长")
# 当分孔信息只有“数量×跨径”时，用这些字段确定孔数。
SPAN_COUNT_FIELDS = ("跨数", "孔数")
# 没有更精确的跨径字段时的兜底字段；桥梁全长不等于跨径总长，仅用于保持算法可解释。
FALLBACK_LENGTH_FIELDS = ("桥梁全长", "全长", "总长")
# 只有这些字段承载明确的桥梁引用；“评定对象”文本不做模糊匹配，避免普通设施被误判为桥梁。
BRIDGE_REFERENCE_FIELDS = ("桥梁编码", "桥梁名称", "关联桥梁编码", "关联桥梁名称", "评定对象")
EXACT_BRIDGE_REFERENCE_FIELDS = ("桥梁编码", "桥梁名称", "关联桥梁编码", "关联桥梁名称")

PROFILE_FIELD = "桥梁技术指标"

_NUMBER_RE = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?)")
_LAYOUT_REPEAT_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(m|米)?\s*[×x*]\s*(\d+(?:\.\d+)?)\s*(m|米)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class BridgeTypeRule:
    """一类桥梁结构类型的登记信息。

    新增结构类型时在 ``BRIDGE_TYPE_RULES`` 中增加一条即可，三个入口无需改动。
    """

    canonical: str
    aliases: tuple[str, ...]

    def matches(self, value: str) -> bool:
        normalized = _normalize_text(value)
        candidates = (self.canonical, *self.aliases)
        return any(_normalize_text(alias) in normalized for alias in candidates if alias.strip())


BRIDGE_TYPE_RULES: tuple[BridgeTypeRule, ...] = (
    BridgeTypeRule("板桥", ("实心板桥", "空心板桥", "钢筋混凝土板桥", "预应力混凝土板桥")),
    BridgeTypeRule("梁桥", ("简支梁桥", "连续梁桥", "连续刚梁", "T梁桥", "箱梁桥", "装配式梁桥")),
    BridgeTypeRule("拱桥", ("石拱桥", "双曲拱桥", "桁架拱桥", "钢管混凝土拱桥", "钢拱桥", "混凝土拱桥")),
    BridgeTypeRule("刚构桥", ("刚架桥", "连续刚构桥", "T型刚构", "斜腿刚构")),
    BridgeTypeRule("斜拉桥", ("双塔斜拉桥", "单塔斜拉桥", "矮塔斜拉桥", "部分斜拉桥")),
    BridgeTypeRule("悬索桥", ("吊桥", "地锚式悬索桥", "自锚式悬索桥")),
    BridgeTypeRule("组合体系桥", ("梁拱组合桥", "刚构-连续组合梁桥", "拱梁组合桥")),
)

# 桥涵分类以米为单位：多孔跨径总长（L）与单孔跨径（Lk）分别判定，再取较高等级。
TOTAL_SPAN_SCALE_BANDS: tuple[tuple[float, str], ...] = (
    (1000.0, "特大桥"),
    (100.0, "大桥"),
    (30.0, "中桥"),
    (8.0, "小桥"),
)
MAX_SPAN_SCALE_BANDS: tuple[tuple[float, str], ...] = (
    (150.0, "特大桥"),
    (40.0, "大桥"),
    (20.0, "中桥"),
    (5.0, "小桥"),
)
_SCALE_RANK = {"涵洞": 0, "小桥": 1, "中桥": 2, "大桥": 3, "特大桥": 4}


@dataclass(frozen=True)
class BridgeProfile:
    """桥梁跨径和类型的统一计算结果。"""

    span_length: float | None
    total_span_length: float | None
    bridge_type: str | None
    scale_type: str | None
    source: tuple[str, ...]

    def to_payload(self) -> dict[str, Any]:
        """转成接口中的嵌套技术指标，不覆盖任何既有顶层字段。"""
        return {
            "跨径": self.span_length,
            "多孔跨径总长": self.total_span_length,
            "桥梁类型": self.bridge_type,
            "按跨径分类": self.scale_type,
        }


def _should_attach_profile(profile: BridgeProfile, row: dict[str, Any]) -> bool:
    # 既有占位样例的“桥梁全长”等字段不是数字，保持其接口快照；
    # 真数据即使类型暂未登记，也会带出可计算的跨径。
    return profile.span_length is not None or profile.total_span_length is not None


def annotate_bridge_row(row: dict[str, Any]) -> dict[str, Any]:
    """在桥梁记录副本中追加统一技术指标；样例占位数据无指标时不新增字段。"""
    result = dict(row)
    profile = resolve_bridge_profile(row)
    if _should_attach_profile(profile, row):
        result[PROFILE_FIELD] = profile.to_payload()
    return result


def annotate_assess_row(row: dict[str, Any], bridges: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """技术评定记录能关联到桥梁时，追加同一份桥梁技术指标。"""
    result = dict(row)
    bridge = find_bridge_for_assessment(row, bridges)
    if bridge is not None:
        profile = resolve_bridge_profile(bridge)
        if _should_attach_profile(profile, bridge):
            result[PROFILE_FIELD] = profile.to_payload()
    return result


def find_bridge_for_assessment(
    assessment: dict[str, Any],
    bridges: Iterable[dict[str, Any]],
) -> dict[str, Any] | None:
    """按桥梁编码、桥梁名称或评定对象引用匹配桥梁档案。"""
    references = [
        (field, str(assessment.get(field) or "").strip())
        for field in BRIDGE_REFERENCE_FIELDS
        if str(assessment.get(field) or "").strip()
    ]
    if not references:
        return None

    bridge_rows = list(bridges)
    exact_references = [
        value for field, value in references if field in EXACT_BRIDGE_REFERENCE_FIELDS
    ]
    # 优先精确匹配编码/名称，避免“桥梁样例1”命中“桥梁样例10”。
    for reference in exact_references:
        for bridge in bridge_rows:
            if str(bridge.get("桥梁编码") or "").strip() == reference:
                return bridge
    for reference in exact_references:
        for bridge in bridge_rows:
            if str(bridge.get("桥梁名称") or "").strip() == reference:
                return bridge

    # “评定对象”可能是“BRID-0001 某某桥”，只在该字段中包含明确桥梁编码或名称时才关联。
    assessment_targets = [
        value for field, value in references if field == "评定对象"
    ]
    for reference in assessment_targets:
        for bridge in bridge_rows:
            code = str(bridge.get("桥梁编码") or "").strip()
            name = str(bridge.get("桥梁名称") or "").strip()
            if (code and code in reference) or (name and name in reference):
                return bridge
    return None


def resolve_bridge_profile(row: dict[str, Any]) -> BridgeProfile:
    """计算一座桥的跨径、规范化结构类型和按跨径分类。"""
    explicit_max, max_sources = _first_numeric(row, MAX_SPAN_FIELDS)
    layout, layout_sources = _first_present(row, LAYOUT_FIELDS)
    explicit_total, total_sources = _first_numeric(row, TOTAL_SPAN_FIELDS)
    span_count, count_sources = _first_numeric(row, SPAN_COUNT_FIELDS)

    layout_spans: list[float] = []
    if layout:
        layout_spans = parse_span_layout(str(layout))

    total_span = explicit_total
    total_span_sources = list(total_sources)

    if layout_spans:
        max_span = round(max(layout_spans), 3)
        if total_span is None:
            total_span = round(sum(layout_spans), 3)
            total_span_sources = list(layout_sources)
        max_sources = list(layout_sources)
    elif explicit_max is not None:
        max_span = round(explicit_max, 3)
        if total_span is None and span_count is not None and span_count > 0:
            total_span = round(explicit_max * int(span_count), 3)
            total_span_sources = [*max_sources, *count_sources]
    else:
        fallback, fallback_sources = _first_numeric(row, FALLBACK_LENGTH_FIELDS)
        max_span = round(fallback, 3) if fallback is not None else None
        max_sources = list(fallback_sources)
        if total_span is None and fallback is not None:
            total_span = round(fallback, 3)
            total_span_sources = list(fallback_sources)

    bridge_type = normalize_bridge_type(row.get("桥梁类型"))
    scale_type = classify_bridge_scale(max_span=max_span, total_span=total_span)
    return BridgeProfile(
        span_length=max_span,
        total_span_length=total_span,
        bridge_type=bridge_type,
        scale_type=scale_type,
        source=tuple(max_sources or total_span_sources),
    )


def normalize_bridge_type(value: Any) -> str | None:
    """把档案中的结构类型别名归一到统一名称；无法识别时保留原始口径。"""
    text = str(value or "").strip()
    if not text:
        return None
    for rule in BRIDGE_TYPE_RULES:
        if rule.matches(text):
            return rule.canonical
    return text


def parse_span_layout(value: Any) -> list[float]:
    """解析常见分孔写法，返回每个桥孔的跨径，单位米。

    支持：``3×30m``、``3*30``、``20m+25m+20m``、``2-20`` 等。
    """
    text = str(value or "").strip()
    if not text:
        return []

    def expand(match: re.Match[str]) -> str:
        left = float(match.group(1))
        right = float(match.group(3))
        if match.group(2) and not match.group(4):
            span, count = left, right
        elif match.group(4) and not match.group(2):
            count, span = left, right
        else:
            count, span = (left, right) if left < right else (right, left)
        if count <= 0 or span <= 0 or count > 1000:
            return ""
        return " ".join([str(span)] * int(round(count)))

    expanded = _LAYOUT_REPEAT_RE.sub(expand, text)
    spans = [meters for token in re.split(r"[\s,，;；+＋-]+", expanded)
             if (meters := _parse_length(token)) is not None and meters > 0]
    return [round(span, 3) for span in spans]


def classify_bridge_scale(*, max_span: float | None, total_span: float | None) -> str | None:
    """按《公路工程技术标准》的单孔与多孔跨径阈值分类，取两套指标中的较高等级。"""
    total_scale = _match_scale_band(total_span, TOTAL_SPAN_SCALE_BANDS)
    max_scale = _match_scale_band(max_span, MAX_SPAN_SCALE_BANDS)
    candidates = [scale for scale in (total_scale, max_scale) if scale is not None]
    if not candidates:
        return None
    return max(candidates, key=lambda scale: _SCALE_RANK[scale])


def _match_scale_band(value: float | None, bands: tuple[tuple[float, str], ...]) -> str | None:
    if value is None or value <= 0:
        return None
    for lower_bound, label in bands:
        if value >= lower_bound:
            return label
    return "涵洞"


def _first_present(row: dict[str, Any], fields: Iterable[str]) -> tuple[Any, tuple[str, ...]]:
    for field in fields:
        value = row.get(field)
        if value is not None and str(value).strip():
            return value, (field,)
    return None, ()


def _first_numeric(row: dict[str, Any], fields: Iterable[str]) -> tuple[float | None, tuple[str, ...]]:
    for field in fields:
        value = _parse_length(row.get(field))
        if value is not None:
            return value, (field,)
    return None, ()


def parse_length(value: Any) -> float | None:
    """对外暴露的长度解析，兼容 m、km、厘米、毫米等常见单位。"""
    return _parse_length(value)


def _parse_length(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if number >= 0 else None

    text = str(value).strip().lower()
    if not text:
        return None
    match = _NUMBER_RE.search(text)
    if not match:
        return None
    unit = text.removeprefix(match.group()).strip()
    number = float(match.group())
    if number < 0:
        return None
    if unit in {"km", "k/m", "千米", "公里"}:
        number *= 1000
    elif unit in {"cm", "厘米"}:
        number /= 100
    elif unit in {"mm", "毫米"}:
        number /= 1000
    return number


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()
