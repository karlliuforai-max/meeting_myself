"""版本号管理工具。

用法（从项目根运行）：
    python scripts/bump_version.py patch          # 0.0.1 → 0.0.2
    python scripts/bump_version.py minor          # 0.0.1 → 0.1.0
    python scripts/bump_version.py major          # 0.0.1 → 1.0.0
    python scripts/bump_version.py set 0.1.0      # 直接指定版本

执行后自动：
1. 更新 VERSION 文件
2. 在 CHANGELOG.md 顶部插入新版本占位条目（供你填写变更内容）
3. 打印提醒（不 commit、不 push，由用户手动操作）
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read_version() -> tuple[int, int, int]:
    raw = (ROOT / "VERSION").read_text().strip()
    parts = raw.split(".")
    if len(parts) != 3:
        raise SystemExit(f"VERSION 格式错误（期望 x.y.z）：{raw}")
    return tuple(int(p) for p in parts)


def write_version(major: int, minor: int, patch: int) -> str:
    ver = f"{major}.{minor}.{patch}"
    (ROOT / "VERSION").write_text(ver + "\n")
    return ver


def bump_changelog(ver: str) -> None:
    path = ROOT / "docs" / "CHANGELOG.md"
    today = date.today().isoformat()
    entry = f"## v{ver} — {today}\n\n<!-- 在这里填写本版本的变更内容 -->\n\n"
    if path.exists():
        old = path.read_text(encoding="utf-8")
        # 插在第一个 ## 之前（或文件顶部）
        if "## " in old:
            idx = old.index("## ")
            path.write_text(old[:idx] + entry + old[idx:], encoding="utf-8")
        else:
            path.write_text(entry + old, encoding="utf-8")
    else:
        path.write_text(f"# Changelog\n\n{entry}", encoding="utf-8")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        major, minor, patch = read_version()
        print(f"当前版本：v{major}.{minor}.{patch}")
        print("用法：bump_version.py [patch|minor|major|set <x.y.z>]")
        return

    major, minor, patch = read_version()

    if args[0] == "patch":
        patch += 1
    elif args[0] == "minor":
        minor += 1
        patch = 0
    elif args[0] == "major":
        major += 1
        minor = 0
        patch = 0
    elif args[0] == "set":
        if len(args) < 2:
            raise SystemExit("用法：bump_version.py set <x.y.z>")
        major, minor, patch = (int(x) for x in args[1].split("."))
    else:
        raise SystemExit(f"未知子命令：{args[0]}")

    ver = write_version(major, minor, patch)
    bump_changelog(ver)
    print(f"✅ 版本已更新 → v{ver}")
    print(f"📝 CHANGELOG.md 已插入 v{ver} 占位条目，请填写变更内容")
    print("📌 由你决定何时 git add / commit / push")


if __name__ == "__main__":
    main()
