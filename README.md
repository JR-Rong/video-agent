# shortvideo-agent

一个用于**短视频自动生成**的 Agent，支持：
- 多类别内容生成（类别可在 `configs/categories.yaml` 扩展）
- 分镜脚本生成（每集自动续写：基于同一 `series` 的历史记录）
- 媒体生成与复用素材库：
  - 图片：生成后入库，后续优先检索复用
  - 视频片段：优先“图生视频（image2video）”，生成后入库，后续优先检索复用
- 成片剪辑：图片动效（Ken Burns）/ 视频片段 / 混合剪辑
- 严禁：新闻/时政/政治/实时热点等（命中会拒绝）
- 成片导出到目录（你自行上传到不同平台/账号）
- 可选显示可灵（Kling）资源包余量（余额查询）

> 说明：目前视频生成实现对接 **Kling（可灵）**，使用 AK/SK 生成 JWT 进行鉴权；图片生成默认用 OpenAI 图像模型（也可替换为其他供应商）。

---

## 项目结构（关键目录）

- `configs/`
  - `providers.yaml`：模型与供应商配置（Kling、OpenAI、分辨率策略等）
  - `categories.yaml`：允许的内容类别
  - `render_presets.yaml`：最终成片输出尺寸/帧率预设
  - `output_routing.yaml`：导出目录路由（按类别分配到不同文件夹）
- `prompts/`：所有提示词模板（md）
- `data/`
  - `outputs/`：每次生成的中间产物与最终视频（run_dir）
  - `export/`：导出目录（最终给你手动上传使用）
  - `media_library/`：素材库（图片/视频片段 + sqlite 索引）
  - `memory.sqlite3`：每次生成记录（用于续写）

---

## 快速开始（Linux）

### 1) 准备
- 安装 `ffmpeg`（脚本会尝试通过 conda 安装）
- 准备 OpenAI Key（用于文本 + 图片生成）
- 准备 Kling 的 `access_key/secret_key`（用于视频生成与余额查询）

### 2) 安装
```bash
bash scripts/install_linux.sh
conda activate shortvideo-agent
```

### 3) 配置
复制环境变量示例并填写：
```bash
cp .env.example .env
```
编辑 configs/providers.yaml：
- OpenAI：openai.text_model、openai.image_model
- Kling：kling.base_url、kling.access_key、kling.secret_key

编辑 configs/output_routing.yaml（可选）：
- 按类别把成片导出到不同目录（对应不同账号手动上传）

### 4) 运行（使用可执行文件）
查看帮助：
```bash
shortvideo-agent --help
shortvideo-agent run --help
```
生成一条 30s，8 镜头，默认图片动效（最低成本）：
```bash
shortvideo-agent run \
  --category life \
  --series life-001 \
  --prompt "一个发生在城市角落的温暖小故事" \
  --total-seconds 30 \
  --scenes 8
```
混合模式（脚本中的 media_type 决定镜头用 image/video；视频镜头优先 image2video）：
```bash
shortvideo-agent run \
  --category emotion \
  --series emo-001 \
  --prompt "关于误会与和解的原创短故事" \
  --total-seconds 35 \
  --scenes 9 \
  --media-mode mixed
```
强制全部用视频片段（成本高；依赖 Kling 配置）：
```bash
shortvideo-agent run \
  --category fantasy \
  --series fan-001 \
  --prompt "原创奇幻冒险，强调镜头运动与氛围" \
  --total-seconds 30 \
  --scenes 6 \
  --media-mode videos
```
仅生成大纲/分镜（不生成媒体、不剪辑）：
```bash
shortvideo-agent run \
  --category life \
  --series life-002 \
  --prompt "通勤路上发生的趣事" \
  --dry-run
```

## 输出位置说明
### 1) 运行输出（中间产物）
data/outputs/{series}/ep_{episode}/
- scene_XXX.png：生成图片（若镜头走图片）
- ref_XXX.png：视频镜头用的参考图（用于 image2video）
- rawclip_XXX.mp4：生成视频原片段（若镜头走视频）
- clip_XXX.mp4：统一规格后的镜头片段
- concat.mp4：镜头拼接
- narration.mp3：配音
- final.mp4：最终成片

### 2) 导出目录（用于你手动上传）
data/export/{route}/{series}/ep_{episode}/
- final.mp4
- meta.yaml（标题、描述、tags 等，便于复制发布）
导出路由由 configs/output_routing.yaml 控制。

### 3) 素材库（自动复用）
data/media_library/
- images/：图片素材
- videos/：视频片段素材
- library.sqlite3：素材索引（关键词/提示词/方向/尺寸/时长/使用次数）
复用逻辑：先按方向（portrait/landscape）过滤，再按关键词+提示词相似度匹配，命中则复用；未命中才生成并入库。

## 命令与参数详解
命令：shortvideo-agent run


--category TEXT（必填）
内容类别，必须在 configs/categories.yaml 的 allowlist 中。
示例：emotion / life / history / fantasy 等
若不在 allowlist：直接拒绝


--series TEXT（必填）
系列 ID，用于“续写”与历史记录。

同一个 series 第一次运行生成 episode=1
下一次同一 series 会自动 episode+1，并把上一集摘要作为续写上下文
示例：
--series novel-001



--prompt TEXT（必填）
用户主题描述。会经过安全策略检查：

严禁：新闻/时政/政治/实时热点/突发事件等
命中即拒绝生成


--total-seconds INTEGER（默认 30）
最终成片总时长（秒）。内部会对每个镜头时长做分配，尽量保持每镜头 2~5 秒的节奏。
范围建议：5 ~ 600


--scenes INTEGER（默认 6）
镜头数量。镜头越多节奏越快、生成成本更高（尤其视频镜头）。
范围建议：1 ~ 30


--media-mode [images|videos|mixed]（默认 images）
媒体生成策略：

images：所有镜头走图片 + Ken Burns 动效（最低成本，强烈推荐默认）
videos：所有镜头走视频片段（成本高）
mixed：尊重脚本内 media_type 字段（image/video 混合）
注意：脚本的 media_type 由模型生成；mixed 才会使用它。
images 与 videos 会覆盖脚本的镜头类型。


--reuse-min-score FLOAT（默认 78.0）
素材库复用阈值（0~100），越高越严格：

高：更少复用，更接近“每次都新生成”
低：复用更多，成本更低但可能画面更重复


--dry-run / --no-dry-run（默认 false）
--dry-run：只生成大纲+分镜，不调用图片/视频生成，也不剪辑


--out-json PATH（可选）
将最终 result JSON 写入指定文件（便于外部系统读取）。

示例：--out-json ./result.json


## Kling（可灵）余额/资源包余量展示
程序启动时会尝试调用：

GET /account/costs
展示“资源包列表及余量”。默认查询最近 30 天（可配）：

在 configs/providers.yaml：

kling_costs:
```yaml
  lookback_days: 30
  resource_pack_name: ""
```
当前仅“显示余额/余量”，不会自动降级生成策略。

## 常见问题（FAQ）
1) 为什么我的视频镜头优先 image2video？
为了更稳定、可控、且更利于复用素材库：

先复用/生成一张参考图（图片更便宜、更好复用）
用这张图做 image2video，得到短视频片段
2) 横竖屏怎么控制？
模型会在每个镜头给出 orientation: portrait|landscape（默认 portrait）。
素材库也会记录 orientation，检索优先匹配同方向。

最终成片输出尺寸由 configs/render_presets.yaml 控制（默认竖屏 1080x1920 或你可以改为 720x1280 的 preset）。

3) 如何新增类别？
编辑 configs/categories.yaml 的 allowlist，添加新类别 key 即可。

开发者提示
若你要替换图片生成供应商：实现一个新的 image provider，并在 configs/providers.yaml 切换 image_generation.provider
若你要调整“关键镜头高分辨率策略”：修改 configs/providers.yaml 的 video_generation.default/key_shot