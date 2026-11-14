# -*- coding: utf-8 -*-
"""App 仓库薄文档检查（规则只在 tools/docs-manifest.json，不写死）

用法：python tools/check_docs.py
"""
import io, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = os.path.join(ROOT, "tools", "docs-manifest.json")


def main():
    if not os.path.exists(MANIFEST):
        print("缺少 tools/docs-manifest.json，先跑主仓库 6-sync_ios.py")
        return 1
    try:
        with io.open(MANIFEST, encoding="utf-8") as f:
            m = json.load(f)
    except Exception as e:
        print("docs-manifest.json 解析失败：", e)
        return 1

    errors = []
    for rel in m.get("required_files") or []:
        if not os.path.exists(os.path.join(ROOT, rel.replace("/", os.sep))):
            errors.append("缺文件：" + rel)

    snippets = m.get("required_snippets") or {}
    for rel, keys in snippets.items():
        p = os.path.join(ROOT, rel.replace("/", os.sep))
        if not os.path.exists(p):
            continue
        text = io.open(p, encoding="utf-8", errors="replace").read()
        for k in keys:
            if k not in text:
                errors.append("%s 缺少关键字：%s" % (rel, k))

    if errors:
        print("文档检查未通过（%d 项）：" % len(errors))
        for e in errors:
            print("  -", e)
        return 1
    print("App 文档检查通过：", os.path.basename(ROOT), len(m.get("required_files") or []), "个文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
