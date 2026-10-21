# PaperRadar 1.3.0 检索控件与日期验收

2026-10-21。新功能用于通用科研检索，不预选学科、不自动设置用户的研究画像。

- 8 类学科，82 个中英主题，60 个期刊/会议。可输入片段或别名补全，仍可自定义。方向和期刊各最多 5 项，组内为任一命中；有检索词时组间共同约束。只选方向逐路检索，只选期刊由 Crossref 检索。
- 默认模糊匹配：词项不要求连续，接受部分前缀/片段和轻微拼写差异；精确匹配要求连续词组。DOI/arXiv ID 不做模糊放宽。相似度只用于检索，身份确认与去重规则保持严格。
- 日历日期以 `YYYY-MM-DD` 传给 Crossref、OpenAlex、Semantic Scholar；arXiv 用 GMT `submittedDate` 区间，按首投稿日期筛选。每个来源仍有自己的日期定义、缺漏和索引覆盖限制。
- 年/月/日精度按来源保存。只给年份或月份的记录，若整个可能区间都在所选范围内可纳入；部分重叠或未知时默认排除，用户可选保留待核对。不能据此补出真实发表日。
- schema v4 新增 `publication_date` 与 `date_kind`。非空旧库升级前备份，失败则停止，不改结构；旧 PDF、元数据和标签保留。

## 已完成验证

首次 18 个新用例全部失败，覆盖缺失控件、按日参数、日期精度、错误期刊匹配、方向检索等问题。修复后扩展到 28 个新用例；全部 290 项源码测试通过，包含 Qt 鼠标点击下拉选择、模糊补全、快捷日期、主窗口传参、同年按日排序、数据库日期往返、旧库升级及备份失败保护。

真实接口七项小样检查：片段标题定位并限定单日；Nature 日期范围；Nature/Science 多选；Nature Communications 单独期刊检索；OpenAlex 日期范围；arXiv 首投稿日期；Semantic Scholar 日期范围。首次部分请求收到 HTTP 429，保存原始记录；降低 Crossref 请求频率并使用正常重试后补测，七项均获得日期合格结果。小样不代表总体召回率或跨学科相关性达标，返回的新闻、书评等也可能在期刊元数据中出现；本版本不把所有期刊条目标为研究论文。

真实 Qt 窗口、已选筛选条件及日历弹窗均已渲染检查。最终打包与安装验证记录由发布阶段追加，见本次任务输出目录中的 `search-controls-installation.json`。

## 官方参考

- [Qt 日期控件](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QDateTimeEdit.html)
- [Qt 可编辑下拉框](https://doc.qt.io/qtforpython-6/PySide6/QtWidgets/QComboBox.html)
- [Crossref 日期与期刊过滤](https://www.crossref.org/documentation/retrieve-metadata/rest-api/rest-api-filters/)
- [Crossref 2026-07-21 限流调整](https://community.crossref.org/t/refining-rest-api-limits-for-improved-stability-and-reliability/16137)
- [OpenAlex 过滤](https://docs.openalex.org/api-entities/works/filter-works)
- [arXiv 查询语法与投稿日期](https://info.arxiv.org/help/api/user-manual.html)
- [Semantic Scholar API](https://api.semanticscholar.org/api-docs/graph)
- [Nature 学科目录](https://www.nature.com/subjects)

建议项由人工选择，官方学科目录用于核对术语，不是实时热度、收录完整度或期刊质量证明。
