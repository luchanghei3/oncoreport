# Oncoseeing HTML/CSS Template Report Generator v5.0.1

v5.0.1 将报告从 ReportLab 固定坐标绘图方式重构为 **HTML/CSS 模板 + Chromium 打印 PDF**。这更接近网页开发方式，后续新增内容、调整模块顺序、修改间距和卡片样式会更方便。

## 为什么 v5.0.1 更适合继续扩展

ReportLab 的优势是精确画固定位置的图形，但长文本、复杂中英文混排、卡片自适应、表格换行和模块间距都需要手动计算。医学商业报告后期一定会不断改文案和加模块，所以固定坐标很容易出现文字出框和重叠。

HTML/CSS 版的优势：

- 使用 `grid` / `flex` 控制模块排版
- 使用 `line-height` / `padding` / `gap` 控制留白体系
- 表格和段落可以自然换行
- 卡片高度可以跟随内容变化
- 页眉、页脚、封面、图表、免责声明可以拆成可维护模板
- 修改样式时主要改 `static/css/report.css`，不用反复改 Python 坐标

## 文件结构

```text
oncoseeing_report_v5_0_1/
├── render_oncoseeing_v5_0_1.py
├── sample_input_oncoseeing_v5_0_1.json
├── sample_probabilities_v5_0_1.csv
├── README_v5_0.md
├── templates/
│   └── report.html
├── static/
│   ├── css/
│   │   └── report.css
│   └── assets/
│       ├── cover_background_right_bottom_v5_0.png
│       ├── header_background_band_v5_0.png
│       └── ibiri_full_logo_theme_v5_0.png
└── output/
    ├── example_oncoseeing_report_v5_0_1_1.html
    └── example_oncoseeing_report_v5_0_1_1.pdf
```

## 安装依赖

建议在 conda 或 venv 环境中运行：

```bash
pip install jinja2 playwright
python -m playwright install chromium
```

如果你的服务器已经有系统 Chromium，也可以直接运行。本包脚本默认使用 `/usr/bin/chromium`。

## 快速测试

```bash
cd oncoseeing_report_v5_0_1

python render_oncoseeing_v5_0_1.py \
  --input sample_input_oncoseeing_v5_0_1.json \
  --output output/report.pdf \
  --html output/report.html \
  --sqlite-db /pool/sun/liuluchang/cosmic_project/database/sql/mydb.db \
  --sqlite-query "SELECT cancer_type, cosmic_sample_id, hgvsg FROM mutation WHERE cancer_type='lung_carcinoma'" \
  --sqlite-cancer-col cancer_type \
  --sqlite-sample-col cosmic_sample_id \
  --sqlite-hgvsg-col hgvsg

python render_oncoseeing_v5_0_1.py \
  --input sample_input_oncoseeing_v5_0_1.json \
  --output output/report.pdf \
  --html output/report.html 

python render_oncoseeing_v5_0_1.py \
  --input sample_input_oncoseeing_v5_0_1.json \
  --output output/report_with_cancer_cards.pdf \
  --html output/report_with_cancer_cards.html \
  --sqlite-db /pool/sun/liuluchang/cosmic_project/database/sql/mydb.db \
  --sqlite-query "SELECT cancer_type, cosmic_sample_id, hgvsg FROM mutation" \
  --sqlite-cancer-col cancer_type \
  --sqlite-sample-col cosmic_sample_id \
  --sqlite-hgvsg-col hgvsg

## 实际运行
python render_oncoseeing_v5_0_1.py \
  --input sample_input_oncoseeing_v5_0_1.json \
  --output output/report_with_cancer_cards.pdf \
  --html output/report_with_cancer_cards.html \
  --sqlite-db /pool/sun/liuluchang/cosmic_project/database/sql/mydb.db

运行后会生成：

```text
output/example_oncoseeing_report_v5_0_1_1.html
output/example_oncoseeing_report_v5_0_1_1.pdf
```

## 只生成 HTML 预览

如果只是想先看网页版布局：

```bash
python render_oncoseeing_v5_0_1.py \
  --input sample_input_oncoseeing_v5_0_1.json \
  --output output/example_oncoseeing_report_v5_0_1_1.pdf \
  --html output/example_oncoseeing_report_v5_0_1_1.html \
  --html-only
```

然后用浏览器打开：

```bash
open output/example_oncoseeing_report_v5_0_1_1.html
```

Linux 服务器上可以用：

```bash
xdg-open output/example_oncoseeing_report_v5_0_1_1.html
```

## 用 CSV 覆盖癌种概率

```bash
python render_oncoseeing_v5_0_1.py \
  --input sample_input_oncoseeing_v5_0_1.json \
  --prob-csv sample_probabilities_v5_0_1.csv \
  --output output/example_oncoseeing_report_v5_0_1_1.pdf \
  --html output/example_oncoseeing_report_v5_0_1_1.html
```

CSV 格式：

```csv
cancer,probability
结直肠癌,0.873
胃癌,0.065
胰腺癌,0.031
其他,0.031
```

`probability` 可以写 `0.873`，也可以写 `87.3%`。

## 如何改版式

主要改这两个文件：

```text
templates/report.html
static/css/report.css
```

常见修改：

- 调整卡片圆角、阴影、边框：改 `.card`
- 调整页面留白：改 `.content-page`、`.page-content`
- 调整 1.1 / 1.2 间距：改 `.gap-section`、`.mt-section`
- 调整癌种进度条：改 `.progress-track`、`.progress-fill`
- 调整页眉：改 `.page-header`、`.header-logo`、`.header-brand-stack`
- 调整封面：改 `.cover-page`、`.cover-main`、`.cover-background`

## 中英文字体策略

HTML 版仍按你的要求处理：

- 中文主体：`Noto Serif CJK SC` / `SimSun` / `Songti SC`
- 英文和数字：`Times New Roman` / `Tinos` / `Times`

脚本里的 `mixed_span()` 会自动把同一行里的英文、数字和百分号包成：

```html
<span class="latin">...</span>
```

这样可以避免中文字体渲染英文时出现字母挤压或粘连。

## 当前报告结构与自动分页

报告正文采用流式 A4 排版：一级章节会从新页开始；章节内的表格、癌种卡片和长文本会按内容自动延续到下一页，避免原先固定高度页面在内容变长时发生截断或重叠。

1. 基本信息
   - 1.1 受检者与样本信息
   - 1.2 检测说明
2. 检测结果综合评估
   - 2.1 综合结论摘要（测得位点数与匹配癌种）
   - 2.2 风险评估总览
3. 阳性发现深度解读
   - 总体阳性发现分布图（数据库提供分布数据时显示）
   - 按 HGVS 查询结果逐癌种生成的筛查卡片：特异性突变点详情、意义、健康管理和干预建议
   - 小结
4. 医学建议
5. 免责声明

要生成第 3 节的癌种卡片，运行时需传入 `--sqlite-db`；脚本会用检测到的 HGVSG 在 `mutation` 表中查询 `cancer_type`，一个位点如匹配多个癌种，会在每个相关癌种卡片中保留，便于临床综合解释。

## 后续扩展建议

新增页面时，建议在 `templates/report.html` 中复制一个：

```html
<section class="page content-page">
  {{ page_header('页面名称', 页码) }}
  <main class="page-content">
    {{ section_title('编号', '中文标题', 'English subtitle') }}
    ...
  </main>
</section>
```

新增样式统一写在 `static/css/report.css`，不要把大量内联样式写进 HTML。


## v5.0.1 viewer compatibility note
This patch disables soft CSS `box-shadow` in the generated PDF because some local PDF readers render Chromium soft shadows as large gray rectangles. The visual hierarchy is now based on clean borders and white cards for more stable cross-viewer rendering.
