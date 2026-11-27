# Orchpact · 序契品牌素材

靛蓝底承托纸白的任务节点、依赖线和验收关口。荧光黄的勾穿过关口，落在代表交付物的实心节点上。

| 资产 | 尺寸 | 使用位置 |
| --- | --- | --- |
| [logo-light.svg](logo-light.svg) | 322 × 80，透明底 | 官网、后台和文档的浅色页眉，深色字标 |
| [logo-dark.svg](logo-dark.svg) | 322 × 80，透明底 | 三站深色页眉，纸白字标 |
| [logo-light.png](logo-light.png) · [logo-dark.png](logo-dark.png) | 1288 × 320，透明底 | 横排组合的 4× 位图，用于 README、Markdown 和静态内容系统；按页面底色选对应版本 |
| [icon.svg](icon.svg) | 128 × 128 视框 | 后台收起的侧栏、产品入口、支持 SVG 的浏览器标签 |
| [favicon.ico](favicon.ico) | 16、24、32、48、64、128、256 px | 浏览器及收藏夹；16–32 px 使用已调校的细线和验收勾 |
| [icon-512.png](icon-512.png) | 512 × 512，四角透明 | 需要栅格图片的产品入口、媒体资料 |
| [apple-touch-icon.png](apple-touch-icon.png) | 180 × 180，不透明 | iOS 主屏幕收藏，平台负责裁切圆角 |
| [avatar.svg](avatar.svg) · [avatar-1024.png](avatar-1024.png) | 1024 × 1024，不透明 | 社区、代码托管平台或组织头像，可安全裁成圆形 |
| [mark-mono.svg](mark-mono.svg) | 128 × 128 视框，透明底 | 单色印刷、单色页脚；图形保留节点、验收关口与交付点 |
| [social-card.svg](social-card.svg) · [social-card.png](social-card.png) | 1200 × 630，不透明 | 官网、文档的公开链接预览。PNG 用于社交平台，SVG 可编辑说明文字 |

## 尺寸与留白

- 横排组合中的图标和字标已按比例排好。页眉建议高 32–40 px，对应宽约 129–161 px。空间不足时使用独立图标。
- 横排组合四周建议留出至少四分之一图标高度的净空。图标 32 px 高时，对应四周至少 8 px。
- 浅底使用深色字标 `#181922`，深底使用纸白字标 `#F7F4EB`。按页眉实际底色选文件；品牌图形内部使用靛蓝 `#262896`、纸白 `#F7F4EB` 和荧光黄 `#EAFF58`。
- 官网与文档页眉使用横排组合，后台窄侧栏使用独立图标。它们都作为首页链接时，以「Orchpact 首页」给链接命名；邻近有同名文字时，将图像设为装饰，避免读屏重复播报。
- 单色源文件使用 `currentColor`。内联 SVG 会继承父元素的 `color`；作为 `<img>` 单独加载时，默认是黑色。
- 头像与 Apple 图标使用铺满画布的靛蓝底，让主机平台裁切。社交分享使用 PNG 原图，保持 1200∶630 比例。SVG 文案使用系统无衬线字体，换文案后在中文渲染环境中重新导出 PNG。

## 字标来源

横排组合和社交图沿用 Orchpact 官方仓库已有的八个拉丁字形轮廓，来源为提交 `04e9a81dbcab16b47c89554258ad024f36772fec` 的 `src/orchpact/static/brand/orchpact-logo.svg`。字形路径已嵌入 SVG，可离线显示。随附 [官方字标沿用的 MIT 版权与许可](LICENSE-wordmark.txt)。
