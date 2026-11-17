# Orchpact 品牌素材

标志以两条相互咬合的流程轨道组成 O 形「契环」：两侧代表独立团队，内部交接表达协作，闭合外形表达契约与交付边界。图形和字标使用可编辑的 SVG 路径，没有脚本、远程资源或运行时字体依赖。

## 文件与用途

| 文件 | 用途 |
| --- | --- |
| [orchpact-logo.svg](orchpact-logo.svg) | 横向 logo，透明背景，407×96 视框；用于浅色背景的页面页眉和项目介绍 |
| [orchpact-logo.png](orchpact-logo.png) | 同一横向 logo 的透明 PNG，1628×384 |
| [orchpact-mark.svg](orchpact-mark.svg) | 独立图形，透明背景，96×96 视框；用于浅色背景 |
| [orchpact-icon.svg](orchpact-icon.svg) | 森林绿圆角方形 icon，96×96 视框；用于 favicon 和应用图标 |
| [orchpact-avatar.svg](orchpact-avatar.svg) | 完整方形头像矢量源文件，1024×1024 |
| [orchpact-avatar.png](orchpact-avatar.png) | 直接上传 GitHub 等平台的头像 PNG，1024×1024，不透明背景 |

头像刻意保留完整方形背景，便于平台自行裁切为圆形或圆角方形。图形边缘保留安全空间；请保持原始长宽比，不单独裁掉某一侧轨道。横向 logo 以浅色背景展示，深色背景使用头像或 icon。

## 颜色

| 色彩 | 色值 | 使用位置 |
| --- | --- | --- |
| 森林绿 | `#173D32` | 字标、浅色版主轨道、头像背景 |
| 纸白 | `#F4F4EC` | 深色版主轨道 |
| 浅草绿 | `#D4E7A3` | 深色版交接轨道 |
| 橄榄绿 | `#789552` | 浅色版交接轨道 |

## 维护与同步

本目录的 SVG 是矢量源文件。字标已经转曲，不附带字体文件；PNG 由对应 SVG 栅格化导出，修改形状或配色后须重新导出。

运行时的 `../favicon.svg`、`../account/favicon.svg` 和 `../showcase/mark.svg` 使用本目录 icon 的相同内容；账号页和实例介绍页的 `orchpact-logo.svg` 使用本目录 logo 的相同内容。

独立官网仓库的 `src/assets/mark.svg`、`src/assets/orchpact-logo.svg` 和 `src/assets/orchpact-avatar.png` 分别保存 icon、logo 和头像的副本。官网构建只读取自己的文件，不依赖主仓库检出。修改后分别验证两个仓库的资源和页面。

## English

Two interlocking workflow tracks form an O-shaped pact seal. The SVGs contain editable paths with outlined lettering and no external fonts or resources. Use the horizontal logo on a light background. Use `orchpact-icon.svg` for favicons and `orchpact-avatar.png` for a profile image; the 1024×1024 avatar has an opaque square background and safe margins for circular cropping. Keep the aspect ratio and re-export PNGs from their SVG sources after changes.
