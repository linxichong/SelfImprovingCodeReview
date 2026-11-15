"""ローカルレビュー画面の HTML を構成する."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html import escape
from urllib.parse import urlencode

from self_improving_code_review.domain.common.enums import BundleStatus
from self_improving_code_review.infrastructure.persistence.web_file_completion import (
    FileCompletion,
    FileCompletionPage,
    FileCompletionStatus,
)
from self_improving_code_review.infrastructure.persistence.web_read_repository import (
    DETAIL_LIMIT,
    PAGE_SIZE,
    Row,
)

STATUS = {
    "success": ("成功", "green"),
    "failed": ("失败", "red"),
    "blocked": ("已拦截", "red"),
    "partial-success": ("部分成功", "amber"),
    "running": ("运行中", "blue"),
    "pending": ("等待中", "muted"),
    "published": ("已发布", "green"),
    "skipped": ("已跳过", "muted"),
    "complete": ("完成", "green"),
}
SEVERITIES = {
    "critical": ("严重", "red"),
    "high": ("高危", "amber"),
    "medium": ("中危", "blue"),
    "low": ("低危", "green"),
    "info": ("提示", "muted"),
}
FILE_STATUSES = {
    FileCompletionStatus.BLOCKED: ("已拦截", "red"),
    FileCompletionStatus.FAILED: ("失败", "red"),
    FileCompletionStatus.PARTIAL: ("部分完成", "amber"),
    FileCompletionStatus.PENDING: ("待处理", "blue"),
    FileCompletionStatus.SKIPPED: ("已跳过", "muted"),
    FileCompletionStatus.COMPLETED: ("已完成", "green"),
    FileCompletionStatus.FILTERED: ("已过滤", "muted"),
}
FILE_DECISIONS = {"review": "纳入审查", "lightweight": "轻量审查", "filter": "排除"}
FILE_REASONS = {
    "binary": "二进制",
    "generated": "生成文件",
    "lock-file": "依赖锁文件",
    "vendor": "第三方依赖",
    "build-output": "构建产物",
    "minified": "压缩文件",
    "excluded": "规则排除",
    "document": "文档",
    "deleted": "删除文件",
    "no-commentable-line": "没有可评论行",
    "budget-exceeded": "超出审查预算",
}
REPORT_TYPES = {"review": "审查报告", "evaluation": "评估报告", "benchmark": "基准评测"}
NAV = (("/", "总览", "◫"), ("/runs", "审查运行", "≡"), ("/reports", "已生成报告", "▤"))
DASH = "—"


def html(value: object) -> str:
    """データ値を安全なテキストまたは属性値に変換する."""
    return escape(DASH if value is None else str(value), quote=True)


def fmt_number(value: object) -> str:
    """計数・使用量を桁区切り付きで表示する."""
    return f"{value:,}" if isinstance(value, int | float) else html(value)


def _format_cost(value: object) -> str:
    """USD の保存精度を維持しつつ、表示を小数点以下 6 桁にそろえる."""
    try:
        cost = Decimal(str(value))
        if not cost.is_finite():
            return DASH
        unit = Decimal("0.000001")
        if 0 < cost < unit:
            return "<0.000001"
        return f"{cost.quantize(unit):,.6f}"
    except InvalidOperation:
        return DASH


def fmt_time(value: object) -> str:
    """タイムゾーン付き日時を UTC 表示にそろえる."""
    if isinstance(value, str):
        try:
            stamp = datetime.fromisoformat(value)
            if stamp.tzinfo is not None:
                return stamp.astimezone(UTC).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            return html(value)
    return html(value)


def page(title: str, body: str, active: str | None = None) -> str:
    """共通のナビゲーションと文書の骨組みを返す."""
    nav = "".join(
        f'<a href="{path}" {"aria-current=page" if path == active else ""}>'
        f'<span aria-hidden="true">{icon}</span>{label}</a>'
        for path, label, icon in NAV
    )
    brand = '<span class="brand-icon" aria-hidden="true">✓</span><span>Review Console</span>'
    sidebar = (
        f'<aside class="sidebar"><a class="brand" href="/">{brand}</a>'
        f'<nav aria-label="主导航">{nav}</nav><div class="side-bottom">'
        '<p><i></i>本地 · 只读</p><form method="post" action="/logout">'
        '<button class="quiet" type="submit">退出登录</button></form></div></aside>'
        if active is not None
        else ""
    )
    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{html(title)} · Review Console</title>"
        '<link rel="stylesheet" href="/style.css"></head>'
        f'<body class="{"app" if active is not None else "auth"}">{sidebar}'
        f'<main id="main">{body}</main></body></html>'
    )


def login(error: str = "") -> str:
    """ログインフォームと認証エラーを表示する."""
    notice = f'<p class="notice" role="alert">{html(error)}</p>' if error else ""
    form = (
        '<section class="login-card"><div class="brand"><span class="brand-icon" '
        'aria-hidden="true">✓</span>Review Console</div><h1>登录</h1>'
        '<p class="description">查看本地审查运行、发现和报告。</p>'
        f'{notice}<form method="post" action="/login">'
        '<label for="user">管理员账户</label>'
        '<input id="user" name="username" autocomplete="username" '
        'required maxlength="128" autofocus>'
        '<label for="password">密码</label>'
        '<input id="password" name="password" type="password" autocomplete="current-password" '
        'required maxlength="1024"><button type="submit">登录</button></form></section>'
    )
    return page("登录", form)


def overview(data: Row) -> str:
    """件数と重大度分布、最近の実行を表示する."""
    severities = data["severities"]
    known = {**SEVERITIES, **{key: (key, "muted") for key in severities if key not in SEVERITIES}}
    findings = sum(severities.values())
    bars = "".join(
        f'<div class="bar"><span>{html(label)}</span>'
        f'<progress class="{tone}" aria-label="{html(label)}" value="{severities.get(key, 0)}"'
        f' max="{max(severities.values(), default=1)}"></progress>'
        f"<strong>{fmt_number(severities.get(key, 0))}</strong></div>"
        for key, (label, tone) in known.items()
    )
    token_total = data["input_tokens"] + data["output_tokens"]
    cards = _cards(
        (
            ("审查运行", data["runs"], f"{fmt_number(data['successes'])} 次成功"),
            ("主要发现", findings, f"{fmt_number(severities.get('critical', 0))} 条严重"),
            ("待关注运行", data["issues"], "失败、已拦截、部分成功"),
            ("Token 用量", token_total, "输入与输出合计"),
        )
    )
    return page(
        "总览",
        (
            _heading("审查总览", "查看运行健康度、问题分布和用量。")
            + cards
            + '<div class="overview-grid"><section class="panel"><header><h2>发现严重程度</h2>'
            '<p>所有运行的主要发现</p></header><div class="bars">'
            + bars
            + "</div></section>"
            + '<section class="panel"><header><h2>Token 构成</h2><p>所有运行累计</p></header>'
            '<dl class="usage"><div><dt>输入 Token</dt>'
            f"<dd>{fmt_number(data['input_tokens'])}</dd></div>"
            "<div><dt>输出 Token</dt>"
            f"<dd>{fmt_number(data['output_tokens'])}</dd></div></dl></section></div>"
            + '<section class="panel"><header class="spread"><div><h2>最近运行</h2>'
            "<p>时间统一显示为 UTC</p></div>"
            '<a class="text-link" href="/runs">全部运行 →</a></header>'
            + _run_table(data["recent"])
            + "</section>"
        ),
        "/",
    )


def runs(items: list[Row], total: int, query: str, status: str, current: int) -> str:
    """実行検索とページ送りを表示する."""
    status_options = '<option value="">所有状态</option>' + "".join(
        f'<option value="{key}" {"selected" if key == status else ""}>{label}</option>'
        for key, (label, _) in STATUS.items()
        if key not in {"published", "complete"}
    )
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    links = "".join(
        '<a class="outline" href="/runs?'
        f'{html(urlencode({"q": query, "status": status, "page": n}))}">'
        f"{label}</a>"
        for n, label in ((current - 1, "上一页"), (current + 1, "下一页"))
        if 1 <= n <= pages
    )
    content = (
        _heading("审查运行", "按项目、模型、提交或运行 ID 找到审查记录。")
        + '<section class="panel"><form class="filters" method="get" action="/runs">'
        '<label for="q">搜索</label>'
        '<input id="q" name="q" maxlength="120" placeholder="输入项目、模型或提交 SHA" '
        f'value="{html(query)}">'
        '<label for="status">状态</label>'
        f'<select id="status" name="status">{status_options}</select>'
        '<button type="submit">筛选</button><a class="text-link" href="/runs">清除</a></form>'
        + _run_table(items)
        + '<footer class="pagination">'
        f"<span>共 {fmt_number(total)} 条 · 第 {current} / {pages} 页</span>"
        f"<div>{links}</div></footer></section>"
    )
    return page("审查运行", content, "/runs")


def run_detail(data: Row, active: str = "/runs", *, source: str | None = None) -> str:
    """実行または JSON レポートの詳細を表示する."""
    stats = data.get("metrics", {})
    coverage = data.get("file_completion")
    coverage_section = (
        _file_completion(coverage, data["run_id"], data["status"])
        if isinstance(coverage, FileCompletionPage)
        else ""
    )
    facts = (
        ("状态", _badge(data.get("status"), STATUS)),
        ("模型", html(data.get("model"))),
        ("审查模式", html(data.get("mode"))),
        ("提示词版本", html(data.get("prompt_version"))),
        ("提交 SHA", html(data.get("source_revision"))),
        ("运行 ID", html(data.get("run_id"))),
        ("开始时间 (UTC)", fmt_time(data.get("started_at"))),
        ("完成时间 (UTC)", fmt_time(data.get("finished_at"))),
    )
    details = "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in facts)
    cards = _cards(
        (
            ("主要发现", stats.get("main_finding_count"), None),
            ("输入 Token", stats.get("input_tokens"), None),
            ("输出 Token", stats.get("output_tokens"), None),
            ("估算成本 (USD)", _format_cost(stats.get("cost_usd")), None),
        )
    )
    return page(
        "审查详情",
        (
            _back(active, "已生成报告" if source else "审查运行")
            + _heading(data.get("project_id") or "审查详情", source)
            + cards
            + '<section class="panel"><header><h2>运行信息</h2></header>'
            f'<dl class="facts">{details}</dl></section>'
            + coverage_section
            + _findings(data.get("findings", []))
            + _section(
                "审查单元",
                _data_rows(data.get("bundles", [])),
                (
                    ("单元", "bundle_id"),
                    ("状态", "status"),
                    ("输入 Token", "input_tokens"),
                    ("输出 Token", "output_tokens"),
                    ("请求耗时 (ms)", "latency_ms"),
                    ("错误代码", "error_code"),
                ),
            )
            + _section(
                "失败记录",
                _data_rows(data.get("errors", [])),
                (
                    ("单元", "bundle_id"),
                    ("错误代码", "code"),
                ),
            )
            + _section(
                "过滤的文件",
                _data_rows(data.get("filtered", [])),
                (
                    ("文件", "path"),
                    ("处理方式", "decision"),
                    ("原因", "reason"),
                ),
            )
            + _section(
                "SCM 发布记录",
                _data_rows(data.get("publications", [])),
                (
                    ("仓库", "repository_external_id"),
                    ("变更编号", "change_external_id"),
                    ("类型", "kind"),
                    ("状态", "status"),
                    ("更新时间", "updated_at"),
                ),
            )
        ),
        active,
    )


def _file_completion(coverage: FileCompletionPage, run_id: str, run_status: str) -> str:
    """ファイル単位の完了状況と、該当する根拠を表示する."""
    total = sum(coverage.counts.values())
    insight = (
        "运行中或被拦截时可能有尚未记录的文件。"
        if run_status in {"pending", "running", "blocked"}
        else ""
    )
    counts = "".join(
        f'<li><span class="badge {tone}">{label}</span>'
        f"<strong>{coverage.counts[state]}</strong></li>"
        for state, (label, tone) in FILE_STATUSES.items()
        if coverage.counts.get(state)
    )
    rows = [_file_completion_row(entry) for entry in coverage.files]
    table = _table(
        ("文件", "选入方式 / 原因", "完成情况", "审查单元", "主要发现"),
        rows,
        "此运行没有保存文件级记录。",
    )
    pagination = _file_completion_pagination(coverage, run_id) if coverage.last > 1 else ""
    return (
        '<section class="panel" id="file-completion"><header><h2>文件级审查完成情况</h2>'
        f"<p>已记录 {total:,} 个文件。完成状态按各文件所属的审查单元计算。{insight}</p></header>"
        f'<ul class="file-completion-counts">{counts}</ul>{table}{pagination}</section>'
    )


def _file_completion_row(entry: FileCompletion) -> list[str]:
    """一つのファイルと、そのレビュー単位ごとの保存結果を整形する."""
    label, tone = FILE_STATUSES[entry.status]
    decision = html(FILE_DECISIONS.get(entry.decision, entry.decision))
    if entry.reason:
        decision += f"<small>{html(FILE_REASONS.get(entry.reason, entry.reason))}</small>"
    successful = sum(unit.status is BundleStatus.SUCCESS for unit in entry.units)
    details = "".join(
        f"<li><code>{html(unit.bundle_id)}</code> {_badge(unit.status.value, STATUS)}"
        + (f"<small>{html(unit.error_code)}</small>" if unit.error_code else "")
        + "</li>"
        for unit in entry.units
    )
    units = (
        f"<details><summary>{successful}/{len(entry.units)} 个单元完成</summary>"
        f'<ul class="file-completion-units">{details}</ul></details>'
        if entry.units
        else DASH
    )
    findings = (
        fmt_number(entry.finding_count)
        if entry.status in {FileCompletionStatus.COMPLETED, FileCompletionStatus.PARTIAL}
        else DASH
    )
    return [
        f"<code>{html(entry.path)}</code>",
        decision,
        f'<span class="badge {tone}">{label}</span>',
        units,
        findings,
    ]


def _file_completion_pagination(coverage: FileCompletionPage, run_id: str) -> str:
    """現在の実行を維持してファイル一覧だけをページ送りする."""
    links = "".join(
        '<a class="outline" href="/run?'
        f'{html(urlencode({"id": run_id, "file_page": number}))}#file-completion">{text}</a>'
        for number, text in ((coverage.current - 1, "上一页"), (coverage.current + 1, "下一页"))
        if 1 <= number <= coverage.last
    )
    return (
        '<footer class="pagination">'
        f"<span>第 {coverage.current} / {coverage.last} 页</span><div>{links}</div></footer>"
    )


def reports(entries: list[Row]) -> str:
    """認識できた既存 JSON を種別・生成時刻とともに表示する."""
    rows = [
        [
            f'<a class="table-link" href="/report?{html(urlencode({"name": item["name"]}))}">'
            f"{html(item['name'])}</a>",
            html(REPORT_TYPES.get(item["kind"])),
            html(item["label"]),
            fmt_time(item["modified"]),
        ]
        for item in entries
    ]
    return page(
        "已生成报告",
        (
            _heading("已生成报告", "查看已经保存的审查 JSON、评估结果和 AACR-Bench 对照。")
            + '<section class="panel">'
            + _table(
                ("报告文件", "类型", "项目 / 评测", "文件更新时间 (UTC)"),
                rows,
                "报告目录中没有识别到 JSON 报告。",
            )
            + "</section>"
        ),
        "/reports",
    )


def report_detail(name: str, report: Row) -> str:
    """保存済みレポートを対応する表として表示する."""
    data = report["data"]
    kind = report["kind"]
    if kind == "review":
        run = data["run"]
        versions = run.get("versions")
        return run_detail(
            {
                **run,
                "prompt_version": versions.get("prompt_version")
                if isinstance(versions, dict)
                else None,
                "metrics": data["metrics"] if isinstance(data.get("metrics"), dict) else {},
                "findings": _data_rows(data["findings"])
                + [
                    {**item, "is_main": False}
                    for item in _data_rows(data.get("reference_findings", []))
                ],
                "errors": _data_rows(data.get("errors", [])),
                "filtered": [
                    {**item, "path": item.get("file", item.get("path"))}
                    for item in _data_rows(data.get("filtered_files", []))
                ],
            },
            "/reports",
            source=name,
        )
    summary = ""
    if kind == "benchmark":
        rows = [
            x["summary"] for x in _data_rows(data["rows"]) if isinstance(x.get("summary"), dict)
        ]
        summary = _benchmark_summary(rows)
        labels = (
            ("工具", "tool"),
            ("样本", "instance"),
            ("重复", "repeat"),
            ("状态", "status"),
            ("命中", "hit_n"),
            ("参考数量", "reference_n"),
            ("输入 Token", "input_tokens"),
            ("输出 Token", "output_tokens"),
        )
    else:
        rows = _data_rows(data["reports"])
        labels = (
            ("评估组", "arm"),
            ("重复", "repeat"),
            ("精确率", "precision"),
            ("召回率", "recall"),
            ("真阳性", "true_positive"),
            ("漏报", "false_negative"),
            ("输入 Token", "input_tokens"),
            ("输出 Token", "output_tokens"),
        )
    return page(
        "报告详情",
        _back("/reports", "已生成报告")
        + _heading(name, REPORT_TYPES[kind])
        + summary
        + _section("逐次结果", rows, labels),
        "/reports",
    )


def _benchmark_summary(rows: list[Row]) -> str:
    """ベンチマークの完了行からツールごとの検出率と平均使用量を集計する."""
    tools: dict[str, Counter[str]] = {}
    for row in rows:
        tool = row.get("tool")
        if not isinstance(tool, str):
            continue
        totals = tools.setdefault(tool, Counter())
        totals["runs"] += 1
        if row.get("status") != "complete":
            continue
        totals["complete"] += 1
        for key in ("hit_n", "reference_n"):
            value = row.get(key)
            if type(value) is int and value >= 0:
                totals[key] += value
        input_tokens, output_tokens = row.get("input_tokens"), row.get("output_tokens")
        if (
            type(input_tokens) is int
            and input_tokens >= 0
            and type(output_tokens) is int
            and output_tokens >= 0
        ):
            totals["tokens"] += input_tokens + output_tokens
            totals["usage_count"] += 1
    summary = []
    for tool, stats in tools.items():
        refs = stats["reference_n"]
        hits = stats["hit_n"]
        recall = f"{hits}/{refs} ({hits / refs:.1%})" if refs else DASH
        average = round(stats["tokens"] / stats["usage_count"]) if stats["usage_count"] else None
        summary.append(
            [html(tool), f"{stats['complete']}/{stats['runs']}", recall, fmt_number(average)]
        )
    heads = ("工具", "运行完成", "参考问题检出率", "每个 PR 平均 Token")
    return (
        '<section class="panel"><header><h2>工具对比</h2></header>'
        + _table(heads, summary, "暂无试次。")
        + "</section>"
    )


def problem(title: str, message: str, active: str | None = None) -> str:
    """入力エラーと読み取り障害を安全な文言で表示する."""
    return page(title, _heading(title, message), active)


def _heading(title: object, description: str | None) -> str:
    """各ページの題名と必要な案内を表示する."""
    subtitle = f"<p>{html(description)}</p>" if description else ""
    return f'<header class="page-head"><h1>{html(title)}</h1>{subtitle}</header>'


def _back(path: str, label: str) -> str:
    """一覧に戻る内部リンクを表示する."""
    return f'<a class="back" href="{path}">← {label}</a>'


def _badge(value: object, labels: dict[str, tuple[str, str]]) -> str:
    """状態や重大度に対応する表示ラベルを生成する."""
    key = str(value) if value is not None else ""
    label, tone = labels.get(key, (key or DASH, "muted"))
    return f'<span class="badge {tone}">{html(label)}</span>'


def _cards(items: tuple[tuple[str, object, str | None], ...]) -> str:
    """概要と詳細の指標をカードとして並べる."""
    return (
        '<div class="cards">'
        + "".join(
            f'<article class="metric"><p>{label}</p><strong>{fmt_number(value)}</strong>'
            + (f"<small>{subtitle}</small>" if subtitle else "")
            + "</article>"
            for label, value, subtitle in items
        )
        + "</div>"
    )


def _run_table(items: list[Row]) -> str:
    """一覧用の実行項目と詳細リンクを生成する."""
    rows = []
    for item in items:
        run_url = html(urlencode({"id": item["run_id"]}))
        sha = html(str(item.get("source_revision") or DASH)[:12])
        rows.append(
            [
                f'<a class="table-link" href="/run?{run_url}">{html(item["project_id"])}</a>'
                f"<small>{sha}</small>",
                _badge(item["status"], STATUS),
                html(item["model"]),
                html(item["mode"]),
                fmt_number(item["metrics"].get("main_finding_count")),
                fmt_time(item["started_at"]),
            ]
        )
    return _table(
        ("项目 / 提交", "状态", "模型", "模式", "主要发现", "开始时间 (UTC)"),
        rows,
        "没有符合条件的审查运行。",
    )


def _section(title: str, rows: list[Row], columns: tuple[tuple[str, str], ...]) -> str:
    """項目がある場合に上限付きの詳細表を表示する."""
    if not rows:
        return ""
    cells = [[fmt_number(row.get(key)) for _, key in columns] for row in rows[:DETAIL_LIMIT]]
    note = f"<p>仅显示前 {DETAIL_LIMIT} 条</p>" if len(rows) > DETAIL_LIMIT else ""
    return (
        f'<section class="panel"><header><h2>{title}</h2>{note}</header>'
        + _table(tuple(label for label, _ in columns), cells, "")
        + "</section>"
    )


def _table(heads: tuple[str, ...], rows: list[list[str]], empty: str) -> str:
    """表または空状態を共通のマークアップで出力する."""
    if not rows:
        return f'<div class="empty">{empty}</div>'
    columns = "".join(f'<th scope="col">{column}</th>' for column in heads)
    items = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return (
        f'<div class="table-wrap"><table><thead><tr>{columns}</tr></thead>'
        f"<tbody>{items}</tbody></table></div>"
    )


def _findings(findings: object) -> str:
    """発見のカードと表示件数の制限を描画する."""
    items = _data_rows(findings)
    cards = "".join(_finding(item) for item in items[:DETAIL_LIMIT])
    note = f"<p>仅显示前 {DETAIL_LIMIT} 条</p>" if len(items) > DETAIL_LIMIT else ""
    return (
        f'<section class="panel"><header><h2>审查发现</h2>{note}</header>'
        + (
            f'<div class="finding-list">{cards}</div>'
            if cards
            else '<div class="empty">这次运行没有审查发现。</div>'
        )
        + "</section>"
    )


def _finding(item: Row) -> str:
    """一件の位置、説明と対策を表示する."""
    start, end = item.get("start_line"), item.get("end_line")
    lines = html(f"{start}-{end}" if start != end and end is not None else start)
    file = html(item.get("file"))
    state = "主要发现" if item.get("is_main", True) else "参考发现"
    return (
        '<article class="finding"><div class="finding-meta">'
        + _badge(item.get("severity"), SEVERITIES)
        + f"<code>{file}:{lines}</code><span>{state}</span></div>"
        f"<h3>{html(item.get('title'))}</h3><p>{html(item.get('description'))}</p>"
        "<details><summary>查看修复建议</summary>"
        f"<p>{html(item.get('suggestion'))}</p><small>{html(item.get('rule_id'))}</small></details></article>"
    )


def _data_rows(value: object) -> list[Row]:
    """外部レポートの配列から行オブジェクトを取り出す."""
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []
