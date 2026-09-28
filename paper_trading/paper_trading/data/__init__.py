"""数据模块（AkshareFetcher 延迟导入，避免离线环境 import 即炸）。"""
from .db_manager import DataDBManager

__all__ = ["DataDBManager"]
