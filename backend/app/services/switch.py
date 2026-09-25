"""转辙机业务规则：动作电流分级、阈值口径维护、状态流转与筛选都收在这里。

分级口径（班组统一标准）：
- 阈值按「设备型号 + 所属区段」分别配置动作电流上限与转换时间上限；
- 实测值超上限即判「超限」，设备置动作异常并列入待处理，判定依据同时给出
  动作电流与转换时间的比对结果；达到上限 90% 但未超的判「预警」；
- 同一区段多台同时超限，按安装道岔号从小到大排风险序，号小的排在最前；
- 已更换的老设备不参与分级；缺型号/缺区段/缺阈值/测量值无法解析的判「无法判定」，
  并在分级报告里点名是哪一条。
"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "switch"
REQUIRED_FIELDS = ["设备编号", "安装道岔", "所属区段"]
ENTRY_FIELDS = [
    "设备编号", "设备型号", "安装道岔", "动作电流", "转换时间",
    "所属区段", "上次检修日", "设备状态",
]
STATUS_ORDER = ["待检修", "运用正常", "动作异常", "已更换"]
ACTION_RULES = {"确认检修": "运用正常", "登记动作异常": "动作异常", "更换设备": "已更换"}
NEGATIVE_ACTIONS = ["登记动作异常"]

# 阈值配置允许保存的物理范围，超出范围的阈值输入一律不允许保存。
THRESHOLD_RANGES: dict[str, tuple[float, float]] = {
    "动作电流上限A": (0.5, 5.0),
    "转换时间上限s": (1.0, 30.0),
}
WARN_RATIO = 0.9  # 达到上限 90% 进预警，不超上限就不列为待处理

GRADE_NORMAL = "正常"
GRADE_WARN = "预警"
GRADE_OVER = "超限"
GRADE_SKIP = "不参评"
GRADE_UNKNOWN = "无法判定"

# 出厂默认口径：型号 + 区段 → 动作电流上限(A)、转换时间上限(s)
DEFAULT_THRESHOLDS: list[dict[str, Any]] = [
    {"设备型号": "ZD6", "所属区段": "一号线东段", "动作电流上限A": 2.5, "转换时间上限s": 2.0},
    {"设备型号": "ZD6", "所属区段": "二号线西段", "动作电流上限A": 2.5, "转换时间上限s": 2.0},
    {"设备型号": "ZYJ7", "所属区段": "一号线东段", "动作电流上限A": 3.0, "转换时间上限s": 3.0},
    {"设备型号": "S700K", "所属区段": "二号线西段", "动作电流上限A": 4.0, "转换时间上限s": 6.6},
]

_GRADE_FIELDS = ["电流分级", "判定依据", "动作电流上限A", "转换时间上限s", "超限项", "风险序号"]


def _to_float(value: Any) -> float | None:
    """把界面/示例里的测量值解析成数字；空串、文字、日期等解析不了的返回 None。"""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _turnout_key(value: Any) -> tuple[int, int, str]:
    """安装道岔排序键：取道岔号数字升序；取不到数字的文字道岔号排后面。"""
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if digits:
        return (0, int(digits), text)
    return (1, 0, text)


class SwitchService:
    def __init__(self) -> None:
        self._thresholds: dict[tuple[str, str], dict[str, Any]] = {}
        for item in DEFAULT_THRESHOLDS:
            self._thresholds[(item["设备型号"], item["所属区段"])] = dict(item)

    # ------------------------------------------------------------------ 阈值口径
    def list_thresholds(self) -> list[dict[str, Any]]:
        """按型号、区段稳定顺序返回当前阈值口径。"""
        return [
            dict(self._thresholds[key])
            for key in sorted(self._thresholds, key=lambda k: (k[0], k[1]))
        ]

    def upsert_threshold(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """新增或更新一条阈值；超出允许保存范围等问题整单拒绝，不落库。"""
        model = str(values.get("设备型号") or "").strip()
        section = str(values.get("所属区段") or "").strip()
        errors: list[str] = []
        if not model:
            errors.append("设备型号不能为空")
        if not section:
            errors.append("所属区段不能为空")

        parsed: dict[str, float] = {}
        for field_name in ("动作电流上限A", "转换时间上限s"):
            number = _to_float(values.get(field_name))
            low, high = THRESHOLD_RANGES[field_name]
            unit = "A" if field_name.endswith("A") else "s"
            if number is None:
                errors.append(f"{field_name}必须填写数字")
            elif number <= 0:
                errors.append(f"{field_name}必须大于 0")
            elif not low <= number <= high:
                errors.append(f"{field_name}允许保存范围为 {low}~{high}{unit}，当前输入不在范围内")
            else:
                parsed[field_name] = number

        if errors:
            return None, errors
        entry = {
            "设备型号": model,
            "所属区段": section,
            "动作电流上限A": parsed["动作电流上限A"],
            "转换时间上限s": parsed["转换时间上限s"],
        }
        self._thresholds[(model, section)] = entry
        return dict(entry), []

    def _threshold_for(self, row: dict[str, Any]) -> dict[str, Any] | None:
        model = str(row.get("设备型号") or "").strip()
        section = str(row.get("所属区段") or "").strip()
        if not model or not section:
            return None
        return self._thresholds.get((model, section))

    # ------------------------------------------------------------------ 分级
    def _evaluate_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """只做判定、不改状态：返回分级、判定依据与结构化超限信息。"""
        result: dict[str, Any] = {
            "电流分级": GRADE_UNKNOWN,
            "判定依据": "",
            "动作电流上限A": None,
            "转换时间上限s": None,
            "超限项": [],
            "无法判定原因": "",
            "_current": None,
            "_time": None,
        }

        if row.get("status") == "已更换":
            result["电流分级"] = GRADE_SKIP
            result["判定依据"] = "设备已更换，按老设备处理，不参与动作电流分级"
            return result

        model = str(row.get("设备型号") or "").strip()
        section = str(row.get("所属区段") or "").strip()
        if not model:
            result["无法判定原因"] = "设备型号未填写，无法按型号+区段匹配阈值"
            result["判定依据"] = f"设备编号 {row.get('设备编号', '?')}：设备型号未填写，判无法判定"
            return result
        if not section:
            result["无法判定原因"] = "所属区段未填写，无法按型号+区段匹配阈值"
            result["判定依据"] = f"设备编号 {row.get('设备编号', '?')}：所属区段未填写，判无法判定"
            return result

        threshold = self._threshold_for(row)
        if threshold is None:
            result["无法判定原因"] = f"型号 {model} 在区段 {section} 尚未配置阈值"
            result["判定依据"] = result["无法判定原因"] + "，判无法判定"
            return result

        upper_current = float(threshold["动作电流上限A"])
        upper_time = float(threshold["转换时间上限s"])
        result["动作电流上限A"] = upper_current
        result["转换时间上限s"] = upper_time

        current = _to_float(row.get("动作电流"))
        convert_time = _to_float(row.get("转换时间"))
        result["_current"] = current
        result["_time"] = convert_time
        if current is None or convert_time is None:
            bad = "动作电流" if current is None else ""
            bad = "、".join(part for part in (bad, "转换时间" if convert_time is None else "") if part)
            result["无法判定原因"] = f"{bad}测量值无法解析为数字"
            result["判定依据"] = f"设备编号 {row.get('设备编号', '?')}：{bad}测量值无法解析，判无法判定"
            return result

        over_items: list[str] = []
        if current > upper_current:
            over_items.append("动作电流")
        if convert_time > upper_time:
            over_items.append("转换时间")
        result["超限项"] = over_items

        scope = f"阈值口径：型号 {model} + 区段 {section}"
        current_compare = (
            f"动作电流 {current:g}A ＞ 上限 {upper_current:g}A"
            if current > upper_current
            else f"动作电流 {current:g}A ≤ 上限 {upper_current:g}A"
        )
        time_compare = (
            f"转换时间 {convert_time:g}s ＞ 上限 {upper_time:g}s"
            if convert_time > upper_time
            else f"转换时间 {convert_time:g}s ≤ 上限 {upper_time:g}s"
        )

        if over_items:
            result["电流分级"] = GRADE_OVER
            result["判定依据"] = (
                f"{current_compare}；{time_compare}；{scope}。"
                f"超上限项：{'、'.join(over_items)}，判超限，列待处理"
            )
            return result

        near_items: list[str] = []
        if current >= upper_current * WARN_RATIO:
            near_items.append("动作电流")
        if convert_time >= upper_time * WARN_RATIO:
            near_items.append("转换时间")
        if near_items:
            result["电流分级"] = GRADE_WARN
            result["判定依据"] = (
                f"{current_compare}；{time_compare}；{scope}。"
                f"{'、'.join(near_items)}已达上限 {WARN_RATIO:.0%}，判预警（未超上限，暂不待处理）"
            )
            return result

        result["电流分级"] = GRADE_NORMAL
        result["判定依据"] = f"{current_compare}；{time_compare}；{scope}，判正常"
        return result

    def _apply_grading(self) -> list[dict[str, Any]]:
        """对全量转辙机重新分级，写回分级字段并校正设备状态，使两者对得上。"""
        rows = store.rows(MODULE)
        evaluated: list[tuple[dict[str, Any], dict[str, Any]]] = [
            (row, self._evaluate_row(row)) for row in rows
        ]

        # 同一区段的超限台按安装道岔号排风险序，号最小的排最前、风险最高。
        over_rows = [(row, info) for row, info in evaluated if info["电流分级"] == GRADE_OVER]
        section_ranks: dict[int, int] = {}
        over_rows.sort(key=lambda pair: (str(pair[0].get("所属区段") or ""), _turnout_key(pair[0].get("安装道岔"))))
        current_section: str | None = None
        rank = 0
        for row, _info in over_rows:
            section = str(row.get("所属区段") or "")
            if section != current_section:
                current_section = section
                rank = 1
            else:
                rank += 1
            section_ranks[id(row)] = rank

        for row, info in evaluated:
            for field in _GRADE_FIELDS:
                row[field] = info.get(field)
            row["风险序号"] = section_ranks.get(id(row))

            grade = info["电流分级"]
            if grade == GRADE_SKIP:
                # 已更换设备沿用更换动作写入的状态，分级不再改动它
                continue
            if grade == GRADE_OVER:
                # 超过上限：统一标动作异常并列入待处理，不允许挂在运用正常上
                row["status"] = "动作异常"
                row["pending"] = True
                row["abnormal"] = True
            elif grade == GRADE_UNKNOWN:
                # 口径判不出来的不能当运用正常，先挂待检修等班组补口径/补数据
                if row.get("status") == "运用正常":
                    row["status"] = "待检修"
                row["pending"] = row.get("status") != "已更换"
                row["abnormal"] = row.get("status") == "动作异常"
            else:
                # 正常 / 预警：人工登记的动作异常保留，其余状态不回退
                row["abnormal"] = row.get("status") == "动作异常"
                row["pending"] = row.get("status") not in ("运用正常", "已更换")
        return rows

    @staticmethod
    def _risk_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
        grade = row.get("电流分级")
        group = {
            GRADE_OVER: 0,
            GRADE_WARN: 1,
            GRADE_UNKNOWN: 2,
            GRADE_NORMAL: 3,
            GRADE_SKIP: 4,
        }.get(grade, 5)
        if group == 0:
            return (0, str(row.get("所属区段") or ""), int(row.get("风险序号") or 0), _turnout_key(row.get("安装道岔")))
        return (group, str(row.get("所属区段") or ""), _turnout_key(row.get("安装道岔")))

    def grading_report(self) -> dict[str, Any]:
        """执行一次分级，返回带统计、缺型号点名与区段风险排序的完整报告。"""
        rows = self._apply_grading()
        ordered = sorted(rows, key=self._risk_sort_key)
        over_rows = [row for row in ordered if row.get("电流分级") == GRADE_OVER]
        missing_model = [
            {
                "id": row.get("id"),
                "设备编号": row.get("设备编号"),
                "安装道岔": row.get("安装道岔"),
                "所属区段": row.get("所属区段"),
            }
            for row in rows
            if not str(row.get("设备型号") or "").strip()
        ]
        section_order = [
            {
                "所属区段": row.get("所属区段"),
                "风险序号": row.get("风险序号"),
                "设备编号": row.get("设备编号"),
                "设备型号": row.get("设备型号"),
                "安装道岔": row.get("安装道岔"),
                "动作电流": row.get("动作电流"),
                "转换时间": row.get("转换时间"),
                "超限项": row.get("超限项"),
            }
            for row in over_rows
        ]
        summary = {
            "总数": len(rows),
            "超限": sum(1 for row in rows if row.get("电流分级") == GRADE_OVER),
            "预警": sum(1 for row in rows if row.get("电流分级") == GRADE_WARN),
            "正常": sum(1 for row in rows if row.get("电流分级") == GRADE_NORMAL),
            "无法判定": sum(1 for row in rows if row.get("电流分级") == GRADE_UNKNOWN),
            "不参评": sum(1 for row in rows if row.get("电流分级") == GRADE_SKIP),
            "运用正常": sum(1 for row in rows if row.get("status") == "运用正常"),
        }
        return {
            "summary": summary,
            "items": ordered,
            "超限排序": section_order,
            "缺型号": missing_model,
            "阈值口径": self.list_thresholds(),
        }

    # ------------------------------------------------------------------ 列表/明细
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        grade: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._apply_grading()
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("设备编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if grade:
            rows = [row for row in rows if row.get("电流分级") == grade]
        rows = sorted(rows, key=self._risk_sort_key)
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        self._apply_grading()
        return store.find(MODULE, entry_id)

    # ------------------------------------------------------------------ 登记/动作
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry: dict[str, Any] = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in ENTRY_FIELDS:
            if field in values and str(values.get(field) or "").strip():
                entry[field] = values.get(field)
        # 型号允许先空着：分级时会点名缺型号的这一条，等补齐后再判
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        self._apply_grading()
        return store.find(MODULE, int(entry["id"])) or entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"转辙机 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于转辙机可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"

        if action == "更换设备":
            # 老的更换设备动作照旧：直接置已更换，不参与后续分级
            entry["status"] = target
            entry["pending"] = False
            entry["abnormal"] = False
            self._apply_grading()
            return entry, "转辙机已更换设备"

        if action == "确认检修":
            grade = self._evaluate_row(entry)
            if grade["电流分级"] == GRADE_OVER:
                return None, (
                    f"动作电流/转换时间超上限（{ '、'.join(grade['超限项']) }），"
                    "动作异常设备不能确认为运用正常，请先处置或更换设备"
                )
            if grade["电流分级"] == GRADE_UNKNOWN:
                return None, f"分级口径不完整（{grade['无法判定原因']}），补齐后才能确认为运用正常"
            entry["status"] = target
            entry["pending"] = False
            entry["abnormal"] = False
            self._apply_grading()
            return entry, "转辙机已确认检修，运用正常"

        # 登记动作异常
        entry["status"] = target
        entry["pending"] = True
        entry["abnormal"] = True
        self._apply_grading()
        return entry, "转辙机已登记动作异常"
