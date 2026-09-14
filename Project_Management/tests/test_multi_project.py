#!/usr/bin/env python3
"""公开版基础安全测试。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
EXCLUDED_DIRS = {".git", "__pycache__", "output"}
SECRET_MARKERS = (
    "REPLACE_WITH_REAL_PASSWORD",
    "REPLACE_WITH_REAL_TOKEN",
    "REPLACE_WITH_REAL_SESSION_COOKIE",
)


def test_no_secret_markers_in_source() -> None:
    findings: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        if path.resolve() == Path(__file__).resolve():
            continue
        if path.suffix.lower() not in {".py", ".md", ".yaml", ".yml", ".json"}:
            continue
        content = path.read_text(encoding="utf-8", errors="replace")
        for marker in SECRET_MARKERS:
            if marker in content:
                findings.append(f"{path.relative_to(ROOT)}: {marker}")
    assert not findings, "发现待替换的敏感占位符:\n" + "\n".join(findings)


if __name__ == "__main__":
    test_no_secret_markers_in_source()
    print("公开版基础安全检查通过")
