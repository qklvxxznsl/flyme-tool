# FlymeFirmwareTool（iOS Swift 上传版）

1. 官网新链接抓取失败到哪里看：先看 GitHub Actions 运行日志。
2. 仓库留档在 data/unresolved-links.txt。
3. 同一次运行的 Job Summary 会列出未解决项。
4. 本地终端运行数据更新脚本时也会打印未解决项。
5. 客户端只读取 firmware.json。
6. 数据更新脚本负责抓取官网和手动链接。
7. 有未解决项时以仓库文件记录为准。
