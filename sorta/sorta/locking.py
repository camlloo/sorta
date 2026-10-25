"""跨存储与执行层共用的进程内锁，避免依赖执行器包造成循环导入。"""

import threading

_EXECUTION_LOCK = threading.RLock()


def acquire_execution_lock():
    return _EXECUTION_LOCK
