"""转辙机动作电流分级阈值。

阈值按「设备型号 + 所属区段」两级生效：先取型号默认值，区段里另有规定的
（SECTION_OVERRIDES）按区段口径覆盖。班组的统一口径都收在这里，不再凭耳朵听。

字段单位：动作电流 A（安培），转换时间 s（秒）。
- current_min/current_max：该型号物理上可接受的录入范围，超出一律不允许保存
- current_warn：关注线，达到（含）即定为「关注」
- current_upper：上限，超过（不含）即定为「超限待处理」
- switch_time_upper：转换时间上限，超过（不含）写进判定依据
"""
from __future__ import annotations

from typing import Any

# 型号默认阈值（不分区段时使用）
MODEL_DEFAULTS: dict[str, dict[str, float]] = {
    "ZD6-A": {"current_min": 0.5, "current_warn": 2.0, "current_upper": 2.6, "current_max": 5.0, "switch_time_upper": 6.0},
    "ZD6-D": {"current_min": 0.5, "current_warn": 2.0, "current_upper": 2.9, "current_max": 5.0, "switch_time_upper": 6.0},
    "ZD6-E": {"current_min": 0.5, "current_warn": 2.2, "current_upper": 3.2, "current_max": 6.0, "switch_time_upper": 6.5},
    "ZDJ9":  {"current_min": 0.5, "current_warn": 1.8, "current_upper": 2.5, "current_max": 5.0, "switch_time_upper": 8.0},
    "ZYJ7":  {"current_min": 0.5, "current_warn": 1.8, "current_upper": 2.5, "current_max": 5.0, "switch_time_upper": 8.0},
    "S700K": {"current_min": 0.3, "current_warn": 1.6, "current_upper": 2.2, "current_max": 4.0, "switch_time_upper": 7.0},
}

# 区段专属口径：(所属区段, 设备型号) -> 覆盖项
# 例如上行咽喉道岔动作密、负荷大，ZD6-D 的关注线与上限单独收紧
SECTION_OVERRIDES: dict[tuple[str, str], dict[str, float]] = {
    ("上行咽喉", "ZD6-D"): {"current_warn": 1.8, "current_upper": 2.6},
}


def known_models() -> list[str]:
    return sorted(MODEL_DEFAULTS)


def resolve_rule(model: str, section: str) -> dict[str, float] | None:
    """按型号 + 区段解析最终生效的阈值；型号未配置时返回 None。"""
    base = MODEL_DEFAULTS.get((model or "").strip())
    if base is None:
        return None
    rule = dict(base)
    rule.update(SECTION_OVERRIDES.get(((section or "").strip(), (model or "").strip()), {}))
    return rule


def describe_rule(model: str, section: str) -> dict[str, Any] | None:
    """给接口/页面用的阈值说明，带来源标记。"""
    if (model or "").strip() not in MODEL_DEFAULTS:
        return None
    rule = resolve_rule(model, section)
    assert rule is not None
    source = "区段专属" if ((section or "").strip(), (model or "").strip()) in SECTION_OVERRIDES else "型号默认"
    return {
        "设备型号": model.strip(),
        "所属区段": (section or "").strip(),
        "阈值来源": source,
        "电流下限": rule["current_min"],
        "关注线": rule["current_warn"],
        "电流上限": rule["current_upper"],
        "电流最大可录": rule["current_max"],
        "转换时间上限": rule["switch_time_upper"],
    }


def list_rules() -> list[dict[str, Any]]:
    """列出全部生效口径：型号默认值 + 区段覆盖。"""
    items: list[dict[str, Any]] = []
    for model in sorted(MODEL_DEFAULTS):
        items.append(describe_rule(model, ""))  # type: ignore[arg-type]
    for section, model in sorted(SECTION_OVERRIDES):
        items.append(describe_rule(model, section))  # type: ignore[arg-type]
    return items
