#!/usr/bin/env python3
"""
预定义特例索引管理工具（rules 仓库侧）

索引源文件：presets/index.json  —— App 通过 jsdelivr / jsdmirror 拉取此文件判断是否有更新
规则文件：  presets/<id>.srs（同目录的 .json 是 srs 的源）

子命令：
  list                       列出全部条目
  validate                   结构校验 + 每条 ruleset 对应的 presets/<id>.srs 必须存在
  diff                       对比 presets/*.srs 与索引，列出仓库有但索引没有的文件
  add ...                    新增一条（自动 bump version / updatedAt）
  remove --id ID             删除一条（自动 bump）
  bump                       version+1，updatedAt=今天
  sync-app --app PATH        把索引写到 <app>/EasyWay/Resources/presets-index.json，
                             并把需要内置的 .srs 复制到 <app>/EasyWay/Resources/rulesets
                             （需要内置 = isDefault、tags 含 media、或 App 里已经有该文件）

发布流程：改索引 → validate → 提交并推到 release 分支 → App 下次连接 VPN 时拉取。
App 侧内置副本用 sync-app 同步，随 App 版本发布。

仅依赖 Python 3 标准库。
"""

import argparse
import datetime as _dt
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRESETS_DIR = os.path.join(ROOT, "presets")
INDEX_PATH = os.path.join(PRESETS_DIR, "index.json")
RESTRICT_TYPES = ("default", "singleRegion", "onlyRegion", "multipleRegions", "exclusiveRegion")
REGION_REQUIRED = ("onlyRegion", "exclusiveRegion")

APP_INDEX_REL = os.path.join("EasyWay", "Resources", "presets-index.json")
APP_RULESETS_REL = os.path.join("EasyWay", "Resources", "rulesets")


# ---------------------------------------------------------------------------
# IO helpers

def load_index(path=INDEX_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save_index(index, path=INDEX_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
        f.write("\n")


def bump(index):
    index["version"] = int(index.get("version", 0)) + 1
    index["updatedAt"] = _dt.date.today().isoformat()


def srs_path(entry):
    return os.path.join(ROOT, entry["ruleset"] + ".srs")


def local_srs_ids():
    return {f[:-4] for f in os.listdir(PRESETS_DIR) if f.endswith(".srs")}


# ---------------------------------------------------------------------------
# validate

def validate_index(index):
    """返回 (errors, warnings)"""
    errors, warnings = [], []
    if not isinstance(index.get("version"), int) or index["version"] < 1:
        errors.append("version 必须是 >= 1 的整数")
    if not index.get("updatedAt"):
        errors.append("updatedAt 缺失")
    if not index.get("defaultDescription"):
        errors.append("defaultDescription 缺失")

    names, ids = set(), set()
    for i, e in enumerate(index.get("presets", [])):
        tag = f"#{i} {e.get('name') or e.get('id') or '?'}"
        for key in ("id", "name", "ruleset", "restrictType", "isDefault"):
            if key not in e:
                errors.append(f"{tag}: 缺少字段 {key}")
        if not e.get("id") or not e.get("name") or not e.get("ruleset"):
            errors.append(f"{tag}: id/name/ruleset 不能为空")
            continue
        if e["name"] in names:
            errors.append(f"{tag}: name 重复")
        names.add(e["name"])
        if e["id"] in ids:
            errors.append(f"{tag}: id 重复")
        ids.add(e["id"])
        if e["ruleset"] != f"presets/{e['id']}":
            errors.append(f"{tag}: ruleset 应为 presets/{e['id']}（App 用 id 命名本地文件）")
        if e.get("restrictType") not in RESTRICT_TYPES:
            errors.append(f"{tag}: restrictType 非法 {e.get('restrictType')!r}")
        if e.get("restrictType") in REGION_REQUIRED and not e.get("region"):
            errors.append(f"{tag}: {e['restrictType']} 需要 region")
        if e.get("region") and e.get("restrictType") not in REGION_REQUIRED:
            warnings.append(f"{tag}: region 对 {e['restrictType']} 无意义")
        if not isinstance(e.get("isDefault"), bool):
            errors.append(f"{tag}: isDefault 必须是 bool")
        for list_key in ("tags", "keywords"):
            if list_key in e and (not isinstance(e[list_key], list) or not all(isinstance(x, str) for x in e[list_key])):
                errors.append(f"{tag}: {list_key} 必须是字符串数组")
        if not os.path.exists(srs_path(e)):
            errors.append(f"{tag}: 规则文件缺失 {os.path.relpath(srs_path(e), ROOT)}")
    return errors, warnings


def cmd_validate(args):
    index = load_index(args.index)
    errors, warnings = validate_index(index)
    for w in warnings:
        print("WARN ", w)
    for e in errors:
        print("ERROR", e)
    if not errors:
        print(f"OK: v{index['version']} ({index['updatedAt']}), {len(index['presets'])} 条")
    return 1 if errors else 0


# ---------------------------------------------------------------------------
# list / diff

def cmd_list(args):
    index = load_index(args.index)
    print(f"v{index['version']}  {index['updatedAt']}  {len(index['presets'])} presets\n")
    for e in index["presets"]:
        flags = []
        if e.get("isDefault"):
            flags.append("default")
        flags += e.get("tags") or []
        region = f"({e['region']})" if e.get("region") else ""
        missing = "" if os.path.exists(srs_path(e)) else "  [srs 缺失]"
        print(f"{e['id']:<20} {e['name']:<16} {e['restrictType'] + region:<22} {' '.join(flags)}{missing}")
    return 0


def cmd_diff(args):
    index = load_index(args.index)
    indexed = {e["id"] for e in index["presets"]}
    local = local_srs_ids()
    only_local = sorted(local - indexed)
    only_index = sorted(indexed - local)
    print(f"仓库 {len(local)} 个 .srs，索引 {len(indexed)} 条")
    if only_local:
        print("\n仓库有、索引没有（可用 add 加入，App 里才搜得到）:")
        for i in only_local:
            print("  +", i)
    if only_index:
        print("\n索引有、仓库没有（App 下载会失败）:")
        for i in only_index:
            print("  -", i)
    if not only_local and not only_index:
        print("两边一致")
    return 1 if only_index else 0


# ---------------------------------------------------------------------------
# add / remove / bump

def cmd_add(args):
    index = load_index(args.index)
    if any(e["id"] == args.id for e in index["presets"]):
        print(f"id 已存在: {args.id}", file=sys.stderr)
        return 1
    if any(e["name"] == args.name for e in index["presets"]):
        print(f"name 已存在: {args.name}", file=sys.stderr)
        return 1
    entry = {
        "id": args.id,
        "name": args.name,
        "ruleset": f"presets/{args.id}",
        "restrictType": args.restrict,
    }
    if args.region:
        entry["region"] = args.region
    if args.description:
        entry["description"] = args.description
    entry["isDefault"] = bool(args.default)
    if args.tags:
        entry["tags"] = args.tags
    if args.keywords:
        entry["keywords"] = args.keywords
    index["presets"].append(entry)
    errors, _ = validate_index(index)
    if errors:
        for e in errors:
            print("ERROR", e, file=sys.stderr)
        return 1
    bump(index)
    save_index(index, args.index)
    print(f"已添加 {args.id}，索引 v{index['version']}")
    return 0


def cmd_remove(args):
    index = load_index(args.index)
    before = len(index["presets"])
    index["presets"] = [e for e in index["presets"] if e["id"] != args.id]
    if len(index["presets"]) == before:
        print(f"未找到 id: {args.id}", file=sys.stderr)
        return 1
    bump(index)
    save_index(index, args.index)
    print(f"已删除 {args.id}，索引 v{index['version']}")
    return 0


def cmd_bump(args):
    index = load_index(args.index)
    bump(index)
    save_index(index, args.index)
    print(f"索引 v{index['version']} ({index['updatedAt']})")
    return 0


# ---------------------------------------------------------------------------
# sync-app

def cmd_sync_app(args):
    index = load_index(args.index)
    errors, _ = validate_index(index)
    if errors:
        for e in errors:
            print("ERROR", e, file=sys.stderr)
        return 1

    app = os.path.abspath(args.app)
    app_index = os.path.join(app, APP_INDEX_REL)
    app_rulesets = os.path.join(app, APP_RULESETS_REL)
    if not os.path.isdir(os.path.join(app, "EasyWay")):
        print(f"看起来不是 App 仓库: {app}", file=sys.stderr)
        return 1
    os.makedirs(app_rulesets, exist_ok=True)

    save_index(index, app_index)
    print(f"索引 → {os.path.relpath(app_index, app)}  (v{index['version']})")

    already_bundled = {f[:-4] for f in os.listdir(app_rulesets) if f.endswith(".srs")}
    copied = updated = unchanged = 0
    new_files = []
    for e in index["presets"]:
        need = e.get("isDefault") or "media" in (e.get("tags") or []) or e["id"] in already_bundled
        if not need:
            continue
        src = srs_path(e)
        dst = os.path.join(app_rulesets, e["id"] + ".srs")
        if os.path.exists(dst):
            with open(src, "rb") as a, open(dst, "rb") as b:
                if a.read() == b.read():
                    unchanged += 1
                    continue
            shutil.copyfile(src, dst)
            updated += 1
        else:
            shutil.copyfile(src, dst)
            copied += 1
            new_files.append(e["id"] + ".srs")
    print(f".srs: 新增 {copied}，更新 {updated}，未变化 {unchanged}")
    if new_files:
        print("新文件需在 Xcode 里加入 EasyWay（media 条目还有 EasyWayTV）target 的资源列表:")
        for f in new_files:
            print("  ", f)
    stale = sorted(already_bundled - {e["id"] for e in index["presets"]})
    if stale:
        print("App 里有但索引已不含（可考虑删除）:", ", ".join(stale))
    return 0


# ---------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--index", default=INDEX_PATH, help="索引文件路径（默认 presets/index.json）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list").set_defaults(fn=cmd_list)
    sub.add_parser("validate").set_defaults(fn=cmd_validate)
    sub.add_parser("diff").set_defaults(fn=cmd_diff)
    sub.add_parser("bump").set_defaults(fn=cmd_bump)

    a = sub.add_parser("add")
    a.add_argument("--id", required=True, help="presets/<id>.srs 的 id，如 apple-tvplus")
    a.add_argument("--name", required=True, help="展示名（发布后不要改，是 App 数据库主键）")
    a.add_argument("--restrict", default="default", choices=RESTRICT_TYPES)
    a.add_argument("--region", help="onlyRegion / exclusiveRegion 的地区码，如 HK")
    a.add_argument("--description")
    a.add_argument("--default", action="store_true", help="isDefault=true（App 全新安装即创建并内置 .srs）")
    a.add_argument("--tags", nargs="*", help="如 media（tvOS 内置并展示）")
    a.add_argument("--keywords", nargs="*", help="搜索别名")
    a.set_defaults(fn=cmd_add)

    r = sub.add_parser("remove")
    r.add_argument("--id", required=True)
    r.set_defaults(fn=cmd_remove)

    s = sub.add_parser("sync-app")
    s.add_argument("--app", required=True, help="App 仓库路径（含 EasyWay/ 目录）")
    s.set_defaults(fn=cmd_sync_app)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
