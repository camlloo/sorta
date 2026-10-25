"""跨模块共享的领域模型，字段与开发文档 §5.1 保持一致。

Pydantic 模型只承担领域与接口层数据结构；持久化由 storage 层的
SQLAlchemy ORM 模型负责（M3 引入），两者显式转换。
"""

from pydantic import BaseModel


class FileRecord(BaseModel):
    """已发现文件或可清理目录；目录以 is_regular_file=False 登记。"""

    id: str
    scan_id: str
    relative_path: str  # 相对 root 的 posix 风格路径，不允许绝对路径
    extension: str  # 不含点的小写扩展名，无扩展名为空串
    size: int  # 字节
    modified_ns: int  # st_mtime_ns
    is_regular_file: bool = True
    fingerprint: str | None = None  # 可选：执行前验证所需的 hash（M3 启用）
