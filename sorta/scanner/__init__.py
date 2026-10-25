"""scanner 包：扫描、注册与受控读取。"""

from sorta.scanner.readers import ReadResult, ReadStatus, read_file
from sorta.scanner.registry import (
    FileRegistry,
    FileVerification,
    ScanSession,
    VerificationStatus,
)
from sorta.scanner.scanner import Scanner, ScanPage

__all__ = [
    "FileRegistry",
    "FileVerification",
    "ReadResult",
    "ReadStatus",
    "ScanPage",
    "ScanSession",
    "Scanner",
    "VerificationStatus",
    "read_file",
]
