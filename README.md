# shortvideo-agent
一个短视频生成 Agent：
- 多类别内容生成：情感/历史/生活等（可扩展）
- 内容 -> 分镜脚本 -> 图像 -> 合成视频（低成本：默认图像序列）
- 记忆与续写：每次生成会存档，下次可基于同一 series 续写
- 严禁：实事/新闻/政治（会直接拒绝）
- 批量上传：提供 uploader 接口与 stub（需自行对接各平台 OpenAPI）
- 禁止抄袭：提示词要求原创，并做基础自检（可扩展为更强的相似度检测）
- 可控时长：指定 total_seconds，自动分配到镜头

## 快速开始

1. 复制环境变量
```bash
cp .env.example .env
```

2. 安装（见 scripts/）
Linux: bash scripts/install_linux.sh
Windows PowerShell(管理员或允许脚本执行): powershell -ExecutionPolicy Bypass -File scripts/install_windows.ps1

3. 运行
Linux: bash scripts/run_linux.sh
Windows: powershell -ExecutionPolicy Bypass -File scripts/run_windows.ps1

## 用法示例
生成一个“情感”类，30秒视频，series 为 novel-001，会自动续写：
```bash
python -m shortvideo_agent.main \
  --category emotion \
  --series novel-001 \
  --prompt "都市里两个人的误会与和解" \
  --total-seconds 30 \
  --scenes 6 \
  --mode image_video \
  --upload-platforms "douyin,kuaishou,bilibili"
```

只生成内容与分镜，不出视频:
```bash
python -m shortvideo_agent.main --dry-run ...
```