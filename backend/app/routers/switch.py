"""转辙机接口：维护转辙机，覆盖动作电流分级、阈值口径、确认检修、登记动作异常、更换设备等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.switch import SwitchService

router = APIRouter(prefix="/api/switch", tags=["转辙机"])

service = SwitchService()

LIST_FIELDS = ["设备编号", "设备型号", "安装道岔", "动作电流", "转换时间", "所属区段", "上次检修日", "设备状态"]
STATUSES = ["待检修", "运用正常", "动作异常", "已更换"]
GRADES = ["正常", "预警", "超限", "无法判定", "不参评"]


@router.get("/thresholds")
def list_thresholds() -> dict[str, Any]:
    """读取当前按设备型号 + 所属区段配置的告警阈值口径。"""
    return {"items": service.list_thresholds()}


@router.post("/thresholds", response_model=ActionResult)
def save_threshold(payload: EntryPayload) -> ActionResult:
    """新增或调整一条阈值；超出允许保存范围的输入整单拒绝，不落库。"""
    entry, errors = service.upsert_threshold(payload.values)
    if errors:
        return ActionResult(ok=False, message=f"阈值不允许保存：{'；'.join(errors)}")
    return ActionResult(ok=True, message="阈值已保存并重新分级", entry=entry)


@router.post("/grading/run", response_model=ActionResult)
def run_grading() -> ActionResult:
    """按当前口径对全部转辙机执行一次分级，并返回报告（含缺型号点名、同区段风险排序）。"""
    report = service.grading_report()
    summary = report["summary"]
    return ActionResult(
        ok=True,
        message=(
            f"分级完成：超限 {summary['超限']} 台、预警 {summary['预警']} 台、"
            f"正常 {summary['正常']} 台、无法判定 {summary['无法判定']} 台"
        ),
        entry=report,
    )


@router.get("/grading/report")
def grading_report() -> dict[str, Any]:
    """读取分级报告而不修改入参：结果与设备状态始终按当前数据即时重算。"""
    return service.grading_report()


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按设备编号检索"),
    status: str | None = Query(default=None, description="待检修、运用正常、动作异常、已更换"),
    grade: str | None = Query(default=None, description="正常、预警、超限、无法判定、不参评"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按设备编号、状态、分级过滤转辙机列表；超限设备按区段/道岔风险序排在最前。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, grade=grade, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出转辙机清单：返回当前过滤条件下的全量数据（含分级字段）。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "switch", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条转辙机明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"转辙机 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条转辙机，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="转辙机已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条转辙机执行确认检修、登记动作异常、更换设备；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
