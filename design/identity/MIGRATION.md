# Orchpact 官网、工作台与文档的配色接入

适用品牌套件 1.0.0。以下路径分别相对于引擎仓库 `orchpact` 和官网仓库 `orchpact.github.io`。两个仓库各自保留一份随版本提交的 `design/identity/`，重构后的 CI 和本地构建均应读取本仓库的文件。后续品牌改动在同一版本的套件中维护，然后分别更新两个仓库。

## 1. 安装文件并连接入口

在已放置品牌套件的仓库根目录运行对应的一段。以下命令会更新列出的静态资源，建议在站点重构的工作分支中执行并检查差异。

**官网和文档**

```sh
python3 design/identity/validate.py
identity=design/identity
assets=src/assets
cp "$identity/tokens.css" "$assets/identity-tokens.css"
cp "$identity/brand/icon.svg" "$assets/mark.svg"
cp "$identity/brand/logo-light.svg" "$assets/orchpact-logo.svg"
cp "$identity/brand/logo-dark.svg" "$assets/orchpact-logo-dark.svg"
cp "$identity/brand/LICENSE-wordmark.txt" "$assets/LICENSE-wordmark.txt"
cp "$identity/brand/favicon.ico" "$assets/favicon.ico"
cp "$identity/brand/apple-touch-icon.png" "$assets/apple-touch-icon.png"
cp "$identity/brand/avatar-1024.png" "$assets/orchpact-avatar.png"
cp "$identity/brand/social-card.png" "$assets/orchpact-social.png"
```

在 `src/index.html` 中先加载 `/assets/identity-tokens.css`，再加载 `/assets/site.css`。在 `scripts/build.py` 的文档页和 404 页模板中，先加载同一份变量表，再加载 `/assets/docs.css`。首页页眉和页脚按所在表面明度选择横向标志。文档页眉空间足够时用横标，收紧时切换为图标。现有 36 px 图标旁若保留同名文字，设置图标 alt 为空。把分享图接入页面的 `og:image` 等元数据时使用本站可访问的绝对 HTTPS 地址。后续改动依照官网 `AGENTS.md` 指向的本地规范审查。

**引擎与管理后台**

```sh
python3 design/identity/validate.py
identity=design/identity
static=src/orchpact/static
cp "$identity/tokens.css" "$static/brand/identity-tokens.css"
cp "$identity/brand/icon.svg" "$static/brand/orchpact-icon.svg"
cp "$identity/brand/icon.svg" "$static/favicon.svg"
cp "$identity/brand/icon.svg" "$static/account/favicon.svg"
cp "$identity/brand/favicon.ico" "$static/brand/favicon.ico"
cp "$identity/brand/apple-touch-icon.png" "$static/brand/apple-touch-icon.png"
cp "$identity/brand/logo-light.svg" "$static/brand/orchpact-logo.svg"
cp "$identity/brand/logo-light.svg" "$static/account/orchpact-logo.svg"
cp "$identity/brand/logo-dark.svg" "$static/brand/orchpact-logo-dark.svg"
cp "$identity/brand/logo-light.png" "$static/brand/orchpact-logo.png"
cp "$identity/brand/LICENSE-wordmark.txt" "$static/brand/LICENSE-wordmark.txt"
cp "$identity/brand/mark-mono.svg" "$static/brand/orchpact-mark.svg"
cp "$identity/brand/avatar.svg" "$static/brand/orchpact-avatar.svg"
cp "$identity/brand/avatar-1024.png" "$static/brand/orchpact-avatar.png"
```

在 `src/orchpact/static/kernel/index.html`、`static/account/index.html`（相对 `src/orchpact/`）先加载 `/static/brand/identity-tokens.css`，再加载工作台与账号样式。图标入口分别已有 36 px 和 38 px 的空间。需要兼容 ICO 的页面先声明 ICO，再声明 SVG，并加上 `rel="apple-touch-icon"`。引擎内置的 API 参考页面另由 `src/orchpact/interfaces/api-docs.html` 加载 Swagger UI；如要统一其颜色，可在 Swagger 样式后加本站范围内的覆盖样式，保留方法名称、响应码和文本标签。

同次更新引擎的 `static/brand/README.md` 和官网的品牌说明。上面的兼容路径使用新的透明 PNG 横标。对外声明过固定尺寸的入口，需同步核对消费者的缩放行为。检查页眉硬编码的宽高与新素材的 `viewBox` 比例，使用自动高度。运行时只发布实际引用的文件，原始规范和验证器保存在 `design/identity/`。

## 2. 按组件用途迁移变量

可先用旧变量名指向新变量，让一批页面一起切换，再逐组件消除多义别名。两处 `--paper` 在旧仓库中用途不同，以下按现有实际用法映射。

| 现有名称或场景 | 目标变量与使用位置 |
| --- | --- |
| 官网/文档 `--paper`，后台 `--page` | `--op-page` 用于页面底色 |
| 官网/文档 `--white` 作卡片底，后台 `--paper`、`--input` | `--op-surface` 用于内容面板和输入框底；按钮文字单独使用 `--op-on-action` |
| `--ink`、`--muted`；后台 `--surface-subtle`、文档 `--control` 作弱背景 | 分别使用 `--op-text`、`--op-muted` 和 `--op-subtle` |
| `--line` | 装饰分隔线用 `--op-line`，输入框等必要轮廓用 `--op-control`，画布依赖边用 `--op-edge` |
| `--forest` 作按钮底、后台 `--action`、`--action-text` | 主按钮常态用 `--op-action` 搭配 `--op-on-action`，悬停和按下态分别使用 `--op-action-hover`、`--op-action-active` |
| `--accent`、旧 `--orange` | 分开处理正文链接、焦点环、流程进度与警告。对应 `--op-link`、`--op-focus`、信息语义与警告语义 |
| `--accent-fill`、`--selected` | 当前导航项和列表选中项使用 `--op-selected-bg` 配正文或链接色；沿用 `aria-current` 或选中标记 |
| `--code`、`--code-text` | 代码块统一成 `--op-code-bg` 与 `--op-code-text`，复制按钮同样检查对比度 |
| `--danger` 与场景内写死的状态色 | 使用成对的状态变量，如 `color: var(--op-danger-fg)` 与 `background: var(--op-danger-bg)` |
| 深色品牌区、原 `--lime` | 固定品牌色取 `--op-brand-indigo`、`--op-brand-paper`、`--op-brand-lime`、`--op-brand-ink`。已验收交付强调使用 `--op-delivery-bg` 和 `--op-on-delivery` |
| `--glass`、`--glow`、半透明边和旧绿色阴影 | 可直接用实色 `--op-surface`、`--op-subtle` 和 `--op-line`。保留叠色的组件在最终合成背景上重新测量 |

## 3. 主题、焦点与深色品牌区

默认让变量表响应系统主题。若产品提供持久的明暗选择，可在所有样式表之前用同源的短初始化脚本读取已选值，写入 `<html data-op-theme="light">` 或 `"dark"`。选择“跟随系统”时删除这个属性和相应的本地偏好。读取存储失败时跳过设置，页面继续按系统主题显示。使用严格 CSP 时，将初始化脚本作为同源静态文件加载。官网与文档可共享同源偏好，自托管后台只读取自身源的偏好。

移除旧 `:root` 和旧暗色媒体查询中会盖过新主题的颜色或 `color-scheme` 声明。固定宣传区使用 `--op-hero-bg` 与 `--op-on-hero`。它的内部文字用纸白，键盘焦点用纸白外环，按钮或链接与底色逐项配对。在普通页面、导航和画布中，给交互目标使用 2 px 以上的 `--op-focus` 外环，保留 2–3 px 间距。焦点环落在控件外侧时以外侧可见表面为对比背景。对直接接触两种背景的控件可加一圈分隔边。用键盘检查视口裁剪、固定页眉、抽屉、对话框及跳转链接。强制颜色模式沿用浏览器系统色和可见轮廓。

## 4. 三处界面的检查点

**官网。** 在 `src/assets/site.css` 中逐段检查首屏演示、试验台、事件标签、页脚、复制反馈和移动菜单。旧品牌色也出现在背景图层、固定悬停色、阴影和行内 SVG。演示的“当前步骤”表示播放位置；`src/assets/home.js` 中已播放步骤与正式交付需要不同视觉。使用 `--op-info-fg` 配 `--op-info-bg` 表达进行中，只有经确认的最终交付显示标志中的亮勾。

**管理后台。** 检查 `kernel/style.css`、`kernel/studio.css`、`kernel/flow.css` 和 `account/` 下的样式，覆盖登录、导航、数据表、输入框、积分、工作流画布和检查面板。画布普通依赖用 `--op-edge`，高亮依赖用 `--op-edge-selected`。保留分组虚线、连接源虚线及键盘焦点。现有 `.flow-wire` 和 `.flow-wire-group` 另有透明度，接入后可使用实色描边，或按实际画布底重新验算。节点左边框同步显示状态文字或图标。

| 引擎状态 | 业务含义与样式 |
| --- | --- |
| `queued`、`waiting`、`cancelled` | 用 neutral 标注排队、等待依赖、取消。保留各自措辞 |
| `running`、`completed` | info 用于执行中。completed 用 neutral 配“运行完成”，单个检查明确通过时再使用 success；最终交付仅在验收数据确认后点亮 |
| `awaiting_approval`、`review`、`partial`、`waived` | warning 分别配“外部写操作待审批”“待验收”或“待答复”“部分完成”“缺口已记录”，按运行数据选择文字 |
| `unknown`、验证器 `INCONCLUSIVE` | 显示“结果未知”或“证据不足”，注明可采取的处理，使用 warning |
| `failed`、`rejected`、`interrupted` | danger 配“失败”“已拒绝”“执行中断”；在状态边框之外保留可读文本 |
| 验收项 `accepted`，根交付确认 | 对验收项使用 success。根交付确认使用交付强调色和勾。外部操作 `approved` 仅标记该操作获批 |

**文档。** 在 `src/assets/docs.css` 中检查正文链接、目录当前项、搜索键盘选中项、代码与复制按钮、表格、提示块、图表、404 页。正文链接保留下划线。图表由 `src/assets/diagrams.js` 配置 Mermaid，目前含固定旧色；`.diagram-scroll` 同时固定了浅色表面。重构时从图表容器的计算样式读取 `--op-surface`、`--op-text`、`--op-control`、`--op-edge`，分别设置结点表面、文字、轮廓和连线，同时调整图表背景及边标签底色。若支持切换主题，用保留的 Mermaid 源码重绘，并在切换期间避免旧渲染覆盖新图。维持严格渲染模式、原始代码回退及窄屏横向滚动。打印使用纸白与深字，保留图例、下划线和状态文字。

## 5. 版本与验收

1. 先修改随仓库维护的套件版本，再把白名单中的资源同步到源目录。CI 运行 `python3 design/identity/validate.py`，并对已发布的本地副本做字节比对，例如官网运行 `cmp design/identity/tokens.css src/assets/identity-tokens.css`。任何机器无需检出另一个仓库。
2. 迁移中可运行 `rg -n '#[[:xdigit:]]{3,8}\b|rgba?\(|hsla?\(' src/assets` 盘点官网固定色；引擎相应范围为 `src/orchpact/static/kernel` 和 `src/orchpact/static/account`。检查结果按用途处理，第三方库的内置主题用外层配置或样式覆盖。
3. 官网按仓库约定执行 `uv run python scripts/build.py`、`uv run python scripts/check.py`、`uv run python -m unittest discover -s tests` 和 `node --test tests/*.test.cjs`。引擎按仓库说明在隔离测试环境运行 Python 与 Node 测试，另检查发行包中的公开素材可加载。最后运行两仓各自的 `git diff --check`。
4. 在浅色、深色、窄屏及键盘导航下抽查首页首屏、深色区、登录、运行详情、画布普通/选中/失败节点、文档正文及搜索。验证 Logo、favicon、暗面字标、代码复制和图表回退。功能性文字对比至少 4.5∶1，大号文字及必要控件轮廓至少 3∶1。透明层、悬停、按下与外环使用合成后的颜色测量。
