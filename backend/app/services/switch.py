"""转辙机业务规则：动作电流分级、阈值校验、状态流转与风险排序都收在这里。

分级口径（阈值见 ``switch_thresholds``，按设备型号 + 所属区段两级生效）：
- 正常：动作电流 < 关注线，转换时间也不超标；
- 关注：关注线 ≤ 动作电流 ≤ 上限（仅提示，不算异常），或仅转换时间超标；
- 超限待处理：动作电流 > 上限，置待处理并给出电流与转换时间两项判定依据；
- 动作异常：人工登记的动作异常，未确认检修前不能计入运用正常；
- 待判定：设备型号缺失/未配置阈值口径、电流无法解析，明确指出是哪一条；
- 已更换：更换设备动作照旧，不再参与分级与统计。

排序：超限设备整体排在最前，同一区段内按安装道岔排列（道岔号越小风险越高，
风险序从 1 开始），其后依次为待判定、关注、动作异常、正常，已更换沉底。
"""
from __future__ import annotations

import re
from typing import Any

from app.services import switch_thresholds as thresholds
from app.store import store

MODULE = "switch"
REQUIRED_FIELDS = ["设备编号", "设备型号", "安装道岔"]
EDITABLE_FIELDS = ["设备编号", "设备型号", "安装道岔", "动作电流", "转换时间", "所属区段", "上次检修日"]
STATUS_ORDER = ["待检修", "运用正常", "动作异常", "已更换"]
ACTION_RULES = {"确认检修": "运用正常", "登记动作异常": "动作异常", "更换设备": "已更换"}

LEVEL_NORMAL = "正常"
LEVEL_WATCH = "关注"
LEVEL_OVER = "超限待处理"
LEVEL_ABNORMAL = "动作异常"
LEVEL_UNKNOWN = "待判定"
LEVEL_REPLACED = "已更换"

# 风险从高到低的列表排序优先级
_LEVEL_RANK = {
    LEVEL_OVER: 0,
    LEVEL_UNKNOWN: 1,
    LEVEL_ABNORMAL: 2,
    LEVEL_WATCH: 3,
    LEVEL_NORMAL: 4,
    LEVEL_REPLACED: 5,
}


def parse_number(value: Any) -> float | None:
    """把 '2.6'、2.6 这类输入解析成数值；空值或无法解析返回 None。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("A", "").replace("安", "").replace("秒", "").replace("s", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def turnout_key(name: Any) -> tuple[int, tuple[int, ...]]:
    """安装道岔排序键：取道岔号，复式道岔 '1/3#' 按 (1,3) 参与比较；无法识别的排最后。"""
    match = re.search(r"\d+(?:/\d+)*", str(name or ""))
    if not match:
        return (1, ())
    return (0, tuple(int(part) for part in match.group(0).split("/")))


class SwitchService:
    # ---- 阈值口径 -------------------------------------------------------
    def list_thresholds(self) -> list[dict[str, Any]]:
        return thresholds.list_rules()

    def save_threshold(
        self,
        model: str,
        section: str,
        values: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str]:
        """新增/调整一条阈值口径。区段为空时调型号默认值，否则写区段覆盖。"""
        model = (model or "").strip()
        section = (section or "").strip()
        if not model:
            return None, "设备型号不能为空，阈值口径必须挂在具体型号上"
        if not section and model not in thresholds.MODEL_DEFAULTS:
            return None, f"型号 {model} 尚无默认口径，请先补齐型号默认阈值再配区段覆盖"

        numeric_fields = ["current_min", "current_warn", "current_upper", "current_max", "switch_time_upper"]
        updates: dict[str, float] = {}
        for field in numeric_fields:
            if values.get(field) is None or str(values.get(field)).strip() == "":
                continue
            number = parse_number(values.get(field))
            if number is None or number <= 0:
                return None, f"{field} 必须是正数，收到的是 {values.get(field)!r}"
            updates[field] = number

        current = dict(thresholds.MODEL_DEFAULTS.get(model, {}))
        if section:
            current.update(thresholds.SECTION_OVERRIDES.get((section, model), {}))
        current.update(updates)
        missing = [field for field in numeric_fields if field not in current]
        if missing:
            return None, f"还缺这些口径项才能保存：{'、'.join(missing)}"
        if not (current["current_min"] < current["current_warn"] < current["current_upper"] <= current["current_max"]):
            return None, "阈值必须满足 下限 < 关注线 < 上限 ≤ 最大可录，现有顺序不成立"

        if section:
            thresholds.SECTION_OVERRIDES[(section, model)] = {
                key: current[key]
                for key in numeric_fields
                if key in thresholds.MODEL_DEFAULTS.get(model, {}) or key in updates
            }
        else:
            thresholds.MODEL_DEFAULTS[model] = {key: current[key] for key in numeric_fields}
        saved = thresholds.describe_rule(model, section)
        assert saved is not None
        return saved, ""

    def delete_threshold(self, model: str, section: str) -> str:
        key = ((section or "").strip(), (model or "").strip())
        if key not in thresholds.SECTION_OVERRIDES:
            return f"区段 {section or '（空）'} 下型号 {model or '（空）'} 的专属口径不存在"
        del thresholds.SECTION_OVERRIDES[key]
        return ""

    # ---- 录入校验 -------------------------------------------------------
    def validate_current(self, model: str, section: str, raw: Any) -> tuple[float | None, str]:
        """动作电流保存前校验：型号要有口径，值要能解析，且落在可录区间内。"""
        model = (model or "").strip()
        if not model:
            return None, "设备型号没有填，无法套用阈值口径，不允许保存动作电流"
        rule = thresholds.resolve_rule(model, section)
        if rule is None:
            return None, f"设备型号 {model} 未配置动作电流阈值口径，不允许保存；请先在阈值口径中登记"
        current = parse_number(raw)
        if current is None:
            return None, f"动作电流 {raw!r} 无法解析为数值（单位 A），不允许保存"
        if not (rule["current_min"] <= current <= rule["current_max"]):
            return None, (
                f"动作电流 {current:g}A 超出 {model} 允许保存范围 "
                f"{rule['current_min']:g}~{rule['current_max']:g}A，不允许保存"
            )
        return current, ""

    # ---- 分级 -----------------------------------------------------------
    def _grade(self, entry: dict[str, Any]) -> None:
        """就地给单台转辙机打分：写入分级、判定依据，并把状态/pending/abnormal 对齐。"""
        entry["设备状态"] = entry.get("status", STATUS_ORDER[0])
        if entry.get("status") == "已更换":
            entry["level"] = LEVEL_REPLACED
            entry["判定依据"] = "已更换设备，按老口径不参与动作电流分级"
            entry["pending"] = False
            entry["abnormal"] = False
            entry["超限"] = False
            return

        ident = f"第 {entry.get('id')} 条（设备编号 {entry.get('设备编号') or '未填写'}）"
        model = str(entry.get("设备型号") or "").strip()
        section = str(entry.get("所属区段") or "").strip()
        current = parse_number(entry.get("动作电流"))
        switch_time = parse_number(entry.get("转换时间"))

        if not model:
            self._mark_unknown(entry, f"{ident} 的判定用设备型号没有填，无法套用阈值口径，请补全后再分级")
            return
        rule = thresholds.resolve_rule(model, section)
        if rule is None:
            self._mark_unknown(entry, f"{ident} 的设备型号 {model} 尚未配置阈值口径，暂时无法判定")
            return
        if current is None:
            self._mark_unknown(entry, f"{ident} 的动作电流 {entry.get('动作电流')!r} 无法解析为数值，暂时无法判定")
            return

        scope = f"{model} / {section or '未分区段'}"
        time_over = switch_time is not None and switch_time > rule["switch_time_upper"]
        time_text = (
            f"转换时间 {switch_time:g}s > 上限 {rule['switch_time_upper']:g}s，动作迟滞"
            if time_over
            else (
                f"转换时间 {switch_time:g}s ≤ 上限 {rule['switch_time_upper']:g}s"
                if switch_time is not None
                else f"转换时间未填写，上限 {rule['switch_time_upper']:g}s"
            )
        )
        entry["阈值口径"] = (
            f"{scope}：关注线 {rule['current_warn']:g}A，上限 {rule['current_upper']:g}A，"
            f"转换时间上限 {rule['switch_time_upper']:g}s"
        )

        manual_abnormal = entry.get("status") == "动作异常"
        if current > rule["current_upper"]:
            entry["level"] = LEVEL_OVER
            entry["判定依据"] = (
                f"动作电流 {current:g}A > 上限 {rule['current_upper']:g}A（{scope}），超过上限列为待处理；{time_text}"
            )
            entry["status"] = "动作异常"
            entry["设备状态"] = "动作异常"
            entry["pending"] = True
            entry["abnormal"] = True
            entry["超限"] = True
            entry["转换超时"] = time_over
        else:
            entry["超限"] = False
            entry["转换超时"] = time_over
            if current >= rule["current_warn"] or time_over:
                entry["level"] = LEVEL_WATCH
                entry["判定依据"] = (
                    f"动作电流 {current:g}A 处于关注线 {rule['current_warn']:g}A 与上限 {rule['current_upper']:g}A 之间（{scope}）；{time_text}"
                    if not time_over
                    else f"{time_text}（{scope}），电流 {current:g}A 未超上限 {rule['current_upper']:g}A，先列入关注"
                )
            else:
                entry["level"] = LEVEL_NORMAL
                entry["判定依据"] = (
                    f"动作电流 {current:g}A < 关注线 {rule['current_warn']:g}A（{scope}）；{time_text}"
                )
            if manual_abnormal:
                # 人工登记或超限转来的动作异常，即使电流回落，也得走「确认检修」才能恢复正常
                entry["level"] = LEVEL_ABNORMAL
                cause = "人工登记的动作异常" if entry.get("超限") is False else "动作电流曾超上限"
                entry["判定依据"] = f"{cause}尚未确认检修，不计入运用正常；{entry['判定依据']}"
                entry["pending"] = True
                entry["abnormal"] = True
            elif entry.get("status") == "运用正常":
                entry["pending"] = False
                entry["abnormal"] = False
            elif entry.get("status") == "待检修":
                entry["pending"] = True
                entry["abnormal"] = False

    @staticmethod
    def _mark_unknown(entry: dict[str, Any], reason: str) -> None:
        entry["level"] = LEVEL_UNKNOWN
        entry["判定依据"] = reason
        entry["pending"] = True
        entry["abnormal"] = False
        entry["超限"] = False
        entry["转换超时"] = False
        entry["设备状态"] = entry.get("status", STATUS_ORDER[0])

    def _graded_rows(self) -> list[dict[str, Any]]:
        rows = store.rows(MODULE)
        for row in rows:
            self._grade(row)

        over_rows = [row for row in rows if row.get("level") == LEVEL_OVER]
        # 同一区段同时超限：按区段分组，组内按安装道岔排列；道岔最靠前的风险序为 1
        over_rows.sort(key=lambda row: (str(row.get("所属区段") or ""), turnout_key(row.get("安装道岔"))))
        for index, row in enumerate(over_rows, start=1):
            row["风险序"] = index

        for row in rows:
            if row.get("level") != LEVEL_OVER:
                row["风险序"] = None
        rows.sort(key=lambda row: (
            _LEVEL_RANK.get(str(row.get("level")), 9),
            str(row.get("所属区段") or ""),
            turnout_key(row.get("安装道岔")),
        ))
        return rows

    def summary(self) -> dict[str, int]:
        """分级统计：各桶互斥，加总等于设备总数。

        正常级按是否已确认检修拆成「运用正常」与「正常待检修」两桶，
        避免动作异常设备借状态混进运用正常。
        """
        rows = self._graded_rows()
        return {
            "运用正常": sum(
                1 for row in rows
                if row.get("level") == LEVEL_NORMAL and row.get("status") == "运用正常"
            ),
            "正常待检修": sum(
                1 for row in rows
                if row.get("level") == LEVEL_NORMAL and row.get("status") != "运用正常"
            ),
            "超限待处理": sum(1 for row in rows if row.get("level") == LEVEL_OVER),
            "动作异常": sum(1 for row in rows if row.get("level") == LEVEL_ABNORMAL),
            "关注": sum(1 for row in rows if row.get("level") == LEVEL_WATCH),
            "待判定": sum(1 for row in rows if row.get("level") == LEVEL_UNKNOWN),
            "已更换": sum(1 for row in rows if row.get("level") == LEVEL_REPLACED),
        }

    # ---- 列表/明细 ------------------------------------------------------
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        level: str | None = None,
        section: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._graded_rows()
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("设备编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if level:
            rows = [row for row in rows if row.get("level") == level]
        if section:
            rows = [row for row in rows if section in str(row.get("所属区段") or "")]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        if entry is not None:
            self._grade(entry)
        return entry

    # ---- 登记/录入 ------------------------------------------------------
    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}"
        section = str(values.get("所属区段") or "").strip()
        model = str(values.get("设备型号") or "").strip()
        if model not in thresholds.MODEL_DEFAULTS:
            return None, f"设备型号 {model} 未配置阈值口径，不允许登记；请先在阈值口径中登记该型号"
        raw_current = values.get("动作电流")
        if raw_current is not None and str(raw_current).strip():
            _, error = self.validate_current(model, section, raw_current)
            if error:
                return None, error

        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        for field in EDITABLE_FIELDS:
            value = values.get(field)
            entry[field] = "" if value is None else str(value).strip()
        entry["status"] = STATUS_ORDER[0]
        rows.append(entry)
        self._grade(entry)
        return entry, ""

    def save_measurement(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """录入/修改动作电流与转换时间：阈值范围之外的电流不允许保存。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"转辙机 {entry_id} 不存在或已归档"
        if entry.get("status") == "已更换":
            return None, "该转辙机已更换，老设备不再录入动作电流"
        model = str(values.get("设备型号") or entry.get("设备型号") or "").strip()
        section = str(values.get("所属区段") or entry.get("所属区段") or "").strip()
        if values.get("设备型号") is not None and str(values.get("设备型号")).strip():
            if model not in thresholds.MODEL_DEFAULTS:
                return None, f"设备型号 {model} 未配置阈值口径，不允许保存"
            entry["设备型号"] = model
        if values.get("动作电流") is not None and str(values.get("动作电流")).strip():
            current, error = self.validate_current(model, section, values.get("动作电流"))
            if error:
                return None, error
            entry["动作电流"] = f"{current:g}"
        elif str(entry.get("动作电流") or "").strip():
            # 型号/区段变了，存量电流也要在新口径下重新过一遍可录范围
            current, error = self.validate_current(model, section, entry.get("动作电流"))
            if error:
                return None, error
        if values.get("转换时间") is not None and str(values.get("转换时间")).strip():
            switch_time = parse_number(values.get("转换时间"))
            if switch_time is None:
                return None, f"转换时间 {values.get('转换时间')!r} 无法解析为数值（单位 s），不允许保存"
            entry["转换时间"] = f"{switch_time:g}"
        if values.get("所属区段") is not None:
            entry["所属区段"] = section
        self._graded_rows()
        return entry, ""

    # ---- 状态动作 -------------------------------------------------------
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
            # 更换设备动作照旧：直接置已更换，不再做电流判定
            entry["status"] = target
            entry["pending"] = False
            entry["abnormal"] = False
            self._grade(entry)
            return entry, "转辙机已更换"

        self._grade(entry)
        # 风险序是在全量排序时赋的，先跑一遍全量分级再拦截
        self._graded_rows()
        if action == "确认检修":
            if entry.get("level") == LEVEL_OVER:
                return None, f"动作电流仍超上限（风险序 {entry.get('风险序')}），检修后复测合格才能计入运用正常"
            if entry.get("level") == LEVEL_UNKNOWN:
                return None, "该记录数据待判定（型号或电流缺失/无法识别），补全并复测后才能计入运用正常"
            if entry.get("level") == LEVEL_ABNORMAL and entry.get("转换超时"):
                return None, "转换时间仍超过上限，动作迟滞未消除，不能计入运用正常"
        entry["status"] = target
        self._grade(entry)
        return entry, f"转辙机已{action}"
