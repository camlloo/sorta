"""统一异常层次：调用方只需捕获 SortaError 即可区分业务失败与程序缺陷。"""


class SortaError(Exception):
    """SORTA 业务异常基类。"""


class PathSafetyError(SortaError, ValueError):
    """路径越界、绝对路径、非法路径段等安全问题（开发文档 §7.2 / §10.2）。"""


class ScanError(SortaError, ValueError):
    """扫描参数非法或根目录不可用。"""


class ScanSessionError(SortaError, RuntimeError):
    """scan_id 不存在、已过期或与当前授权根目录不符。"""


class UnknownFileError(SortaError, ValueError):
    """file_id 未在当前扫描会话中注册。"""
