请基于大纲生成短视频【分镜脚本】。

硬性要求：
- 总时长 {{total_seconds}} 秒
- 镜头数 {{scenes}} 个
- 每个镜头必须包含：
  - media_type: "image" 或 "video"
  - narration、on_screen_text、image_prompt、video_prompt、negative_prompt
- 若 media_type="image"：image_prompt 要能直接用于生成单张竖屏配图；video_prompt 也要填写（便于未来切换为视频）
- 若 media_type="video"：video_prompt 必须具体描述镜头运动与动作；image_prompt 也要填写（可用于封面/备用）
- 全部原创；严禁新闻/政治/时事；不得出现媒体来源
- 系列：{{series}}，本集 episode={{episode}}

大纲如下：
{{outline}}