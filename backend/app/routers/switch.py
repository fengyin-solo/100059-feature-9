"""转辙机接口：动作电流分级、阈值口径管理、状态流转与数据录入。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.switch import SwitchService

router = APIRouter(prefix="/api/switch", tags=["转辙机"])

service = SwitchService()

LIST_FIELDS = ["设备编号", "设备型号", "安装道岔", "动作电流", "转换时间", "所属区段", "上次检修日", "设备状态"]
STATUSES = ["待检修", "运用正常", "动作异常", "已更换"]
LEVELS = ["正常", "关注", "超限待处理", "动作异常", "待判定", "已更换"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按设备编号检索"),
    status: str | None = Query(default=None, description="待检修、运用正常、动作异常、已更换"),
    level: str | None = Query(default=None, description="正常、关注、超限待处理、动作异常、待判定、已更换"),
    section: str | None = Query(default=None, description="按所属区段检索"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按设备编号/区段/状态/分级过滤；返回前统一重算分级，保证分级结果与设备状态对得上。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(keyword=keyword, status=status, level=level, section=section, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size, summary=service.summary())


@router.get("/thresholds")
def list_thresholds() -> dict[str, Any]:
    """列出全部生效的动作电流阈值口径（型号默认 + 区段覆盖）。"""
    return {"module": "switch", "items": service.list_thresholds()}


@router.post("/thresholds", response_model=ActionResult)
def save_threshold(payload: EntryPayload) -> ActionResult:
    """按设备型号与所属区段设定阈值；数值项不合法或阈值顺序不成立时拒绝保存。"""
    values = payload.values
    saved, error = service.save_threshold(str(values.get("设备型号") or ""), str(values.get("所属区段") or ""), values)
    if error:
        return ActionResult(ok=False, message=error)
    return ActionResult(ok=True, message="阈值口径已保存", entry=saved)


@router.delete("/thresholds", response_model=ActionResult)
def delete_threshold(
    model: str = Query(..., description="设备型号"),
    section: str = Query(..., description="所属区段"),
) -> ActionResult:
    """删除一条区段专属口径（型号默认口径不允许删除）。"""
    error = service.delete_threshold(model, section)
    if error:
        return ActionResult(ok=False, message=error)
    return ActionResult(ok=True, message="区段专属口径已删除")


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出转辙机清单：返回分级后的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "switch", "total": total, "items": items, "summary": service.summary()}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条转辙机明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        return {"ok": False, "message": f"转辙机 {entry_id} 不存在或已归档"}
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条转辙机；型号未配置口径或动作电流超出可录范围时拒绝保存并说明原因。"""
    entry, error = service.create_entry(payload.values)
    if error:
        return ActionResult(ok=False, message=error)
    return ActionResult(ok=True, message="转辙机已登记", entry=entry)


@router.post("/{entry_id}/measurements", response_model=ActionResult)
def save_measurement(entry_id: int, payload: EntryPayload) -> ActionResult:
    """录入/修改动作电流与转换时间；阈值范围之外的电流不允许保存，保存后立即重新分级。"""
    entry, error = service.save_measurement(entry_id, payload.values)
    if error:
        return ActionResult(ok=False, message=error)
    return ActionResult(ok=True, message="动作电流与转换时间已保存", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单台转辙机执行确认检修、登记动作异常、更换设备；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
