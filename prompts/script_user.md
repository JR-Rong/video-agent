请基于大纲生成短视频【分镜脚本】（适配长视频，最多5分钟）。

总目标：
- 视频总时长 {{total_seconds}} 秒（<=300）
- 少切换画面：只在关键情节/节点切换图片或视频
- 自动识别关键节点 importance="key"，非关键为 "normal"

【时代与合理性约束】
{{era_constraints}}

【历史史实约束（仅在 history 类启用）】
{{history_constraints}}

硬性要求：
1) 镜头数 {{scenes}} 个（不宜过多，镜头更长）
2) 每个镜头必须包含：
   - seconds：建议 10~40 秒（可少量 5~10 秒用于转场）
   - importance："normal" 或 "key"
   - orientation："portrait" 或 "landscape"（默认 portrait）
   - media_type："image" 或 "video"
   - narration、on_screen_text、image_prompt、video_prompt、negative_prompt
3) 【强开头 Hook】：
   - 第1个镜头必须 importance="key"
   - 前 5~10 秒旁白必须强吸引：悬念/反差/承诺收益/情绪冲击
   - 第1个镜头 on_screen_text 必须是标题级大字
4) 【关键节点切换规则】：
   - importance="key" 建议 media_type="video" 或更强画面提示
   - importance="normal" 建议 media_type="image"
   - key 镜头占比 10%~30%
5) 原创与安全：
   - 全部原创表达，严禁抄袭
   - 严禁新闻/政治动员/时事/热点；不得出现媒体来源或“据报道”等表达
   - history 类：不得编造关键史实（可合理补全细节但弱化断言）

大纲如下：
{{outline}}