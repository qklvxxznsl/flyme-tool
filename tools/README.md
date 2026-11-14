# Data maintenance (Swift iOS project)

This folder controls how `data/firmware.json` is updated; no app rebuild is needed for data-only
changes.

## Files

| File | Purpose |
|---|---|
| `.github/workflows/update-data.yml` | scheduled/manual data refresh |
| `tools/update_data.py` | scrape official site + merge `manual-urls.txt` + sort + write dataset |
| `tools/check_sort.py` | CI guard: fails when a group is not sorted |
| `data/manual-urls.txt` | new links, one per line |
| `data/firmware.json` | dataset consumed by the app |

## Commands

```bash
python tools/update_data.py            # process manual-urls.txt only
python tools/update_data.py --scrape   # also scrape the official site
python tools/update_data.py --check-sort
python tools/check_sort.py
```

`update_data.py` rewrites the whole dataset in sorted order. `check_sort.py` only verifies and
exits non-zero on an unsorted group.

## URL rules

- permanent: `https://download.meizu.com/Firmware/Flyme/<model>/<ver>/<region>/<ts>/<hash>/update.zip`
- signed temporary `firmware-res.flyme.com` URLs: `auth_key` is removed
- `cn_daily` range timestamps `start-end` are stored with `ts` empty and the range kept in the URL
- duplicate links are skipped
- unknown model codes create a new device entry named `<code>（新机型·待补名称）`

## Sorting

- builds inside a group: `ts` descending (empty `ts` sorts last)
- groups: normal groups by version ascending, then beta/variant groups by version ascending
- `update_data.py` sorts the whole dataset on every run
- the repository-level `6-工具脚本/8-sort_builds.py` implements the same rule and writes all three
  `firmware.json` copies; it supports `--check` and `--dry`

## kind rules

`official` = MD5 matches the official page; `multi` = same version as an official build;
`hidden` = not on the official page; `factory` = official factory image;
`beta` = `cn_daily` / `cn_beta` / `beta` / `intl_beta`; `variant` = co-branded platform.

A newly imported link stays `hidden` until the official-page MD5 matches.

Run `python 6-工具脚本/11-check_docs.py` from the project root after editing docs.

## 数据更新脚本与共享文件

- update_data.py 是数据更新脚本，不是 App 客户端；客户端只读 firmware.json。
- model-aliases.json 是唯一别名表，由主仓库复制；共享代号 meizu21Pro 按具体构建判断。
- data/manual-urls.txt 每行格式：完整链接 | 机型；共享代号必须写机型。
- data/unresolved-links.txt 记录签名还原失败；没有未解决项时只留表头。
- 只自动抓当前最新版；历史版本不自动补。
- 签名链接先跟到 firmware-res.flyme.com，去掉 auth_key 后还原成 download.meizu.com 永久直链；还原失败不入库。
- 机型条目带 official_page 和 hardware_id，抓取时使用。
- check_sort.py 校验排序；check_docs.py 校验本仓库薄文档。

## 文档自检

- 运行 `python tools/check_docs.py`。
- `python 6-工具脚本/6-sync_ios.py --check` 会比对四个共享文件：`tools/check_docs.py`、`tools/docs-manifest.json`、`tools/model-aliases.json`、`data/unresolved-links.txt`。

