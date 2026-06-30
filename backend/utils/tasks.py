"""后台任务管理工具

修复 Q1：fire-and-forget 任务未保存引用，可能被 GC 回收。
提供 create_background_task 函数，自动保存任务引用并在完成时清理。
"""
import asyncio
from typing import Set
from utils.logger import get_logger

logger = get_logger("tasks")

# 模块级任务集合，保存所有正在运行的后台任务引用
# 任务完成后通过 done callback 自动从集合中移除，防止内存泄漏
_background_tasks: Set[asyncio.Task] = set()


def create_background_task(coro, name: str = "") -> asyncio.Task:
    """创建后台任务并保存引用，防止被 GC 回收

    Args:
        coro: 协程对象
        name: 任务名称（用于日志）

    Returns:
        创建的 Task 对象
    """
    task = asyncio.ensure_future(coro)
    _background_tasks.add(task)

    def _on_done(t: asyncio.Task):
        _background_tasks.discard(t)
        # 记录未捕获的异常，防止静默丢失
        if t.cancelled():
            return
        exc = t.exception()
        if exc:
            logger.warning("后台任务异常 (%s): %s", name or "unnamed", exc)

    task.add_done_callback(_on_done)
    return task
