#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
央行行政处罚监测日报生成器 - API增强版
修复：GitHub Actions国外服务器调用Kimi API超时问题
"""

import os
import sys
import time
from datetime import datetime

from jinja2 import Template
from openai import OpenAI

# ========== 配置 ==========
KIMI_API_KEY = os.getenv("KIMI_API_KEY", "")
KIMI_BASE_URL = "https://api.moonshot.ai/v1"
KIMI_MODEL = "kimi-k2-turbo"
API_TIMEOUT = 120  # 120秒超时
API_RETRIES = 5    # 重试5次
API_RETRY_DELAY = 10  # 每次重试间隔10秒


def fetch_latest_penalty():
    from playwright.sync_api import sync_playwright

    url = "https://www.pbc.gov.cn/zhengwugongkai/4081330/4081344/4081407/4081705/index.html"
    max_retries = 3
    timeout_ms = 60000

    for attempt in range(1, max_retries + 1):
        print("[尝试 " + str(attempt) + "/" + str(max_retries) + "] 访问列表页：" + url)

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                    viewport={'width': 1920, 'height': 1080},
                    locale='zh-CN',
                    timezone_id='Asia/Shanghai'
                )

                page = context.new_page()
                page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                    window.chrome = { runtime: {} };
                    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                    Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh'] });
                """)

                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(8000)

                link = None
                for sel in ["a[href*='银罚决字']", "a[title*='银罚决字']", "ul li a", ".TRS_Editor a", "table a"]:
                    try:
                        link = page.query_selector(sel)
                        if link and '银罚决字' in (link.inner_text() or ''):
                            break
                    except:
                        continue

                if not link:
                    print("   未找到处罚链接")
                    browser.close()
                    if attempt < max_retries:
                        print("   等待5秒后重试...")
                        time.sleep(5)
                        continue
                    return None

                title = link.inner_text().strip()
                href = link.get_attribute("href")

                if href.startswith('/'):
                    href = "https://www.pbc.gov.cn" + href
                elif not href.startswith('http'):
                    base = "https://www.pbc.gov.cn/zhengwugongkai/4081330/4081344/4081407/4081705/"
                    href = base + href

                print("[2/3] 找到：" + title)
                print("       详情页：" + href)

                detail_page = context.new_page()
                detail_page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                    window.chrome = { runtime: {} };
                    Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                    Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN', 'zh'] });
                """)
                detail_page.goto(href, wait_until="domcontentloaded", timeout=timeout_ms)
                detail_page.wait_for_timeout(5000)

                print("[3/3] 提取表格数据...")
                data = extract_table_data(detail_page)
                data['source_url'] = href
                data['list_title'] = title

                browser.close()
                return data

        except Exception as e:
            print("   错误：" + str(e))
            if attempt < max_retries:
                print("   等待5秒后重试...")
                time.sleep(5)
            else:
                print("   已用尽重试次数")
                return None

    return None


def extract_table_data(page):
    data = {
        'party_name': '',
        'doc_number': '',
        'violation_type': '',
        'penalty_content': '',
        'authority': '',
        'decision_date': '',
        'publicity_period': '',
        'remarks': ''
    }

    header_map = {
        '当事人名称': 'party_name',
        '行政处罚决定书文号': 'doc_number',
        '违法行为类型': 'violation_type',
        '行政处罚内容': 'penalty_content',
        '作出行政处罚决定机关名称': 'authority',
        '作出行政处罚决定日期': 'decision_date',
        '公示期限': 'publicity_period',
        '备注': 'remarks',
    }

    try:
        tables = page.query_selector_all("table")

        for table in tables:
            rows = table.query_selector_all("tr")
            if len(rows) < 2:
                continue

            headers = []
            header_cells = rows[0].query_selector_all("th, td")
            for cell in header_cells:
                text = cell.inner_text().strip().replace('\n', '').replace(' ', '')
                headers.append(text)

            if not any('当事人名称' in h for h in headers):
                continue

            print("   发现目标表格，表头：" + " | ".join(headers))

            if len(rows) >= 2:
                data_cells = rows[1].query_selector_all("td")
                start_idx = 1 if len(data_cells) > len(headers) else 0

                for i, header in enumerate(headers):
                    cell_idx = start_idx + i
                    if cell_idx < len(data_cells):
                        for key_word, field_name in header_map.items():
                            if key_word in header:
                                value = data_cells[cell_idx].inner_text().strip()
                                data[field_name] = value
                                break
            break

    except Exception as e:
        print("表格提取异常：" + str(e))

    print("   提取结果：")
    for k, v in data.items():
        print("      " + k + " = " + (v if v else "(空)"))

    return data


def analyze_with_kimi(data):
    # 调试：检查API Key是否读取成功（只打印前8位）
    if KIMI_API_KEY:
        print("   API Key已读取：" + KIMI_API_KEY[:8] + "...")
    else:
        print("   [错误] 未读取到 KIMI_API_KEY 环境变量")
        print("   请检查 GitHub Secrets 是否设置：Settings -> Secrets -> KIMI_API_KEY")
        return get_default_data(), False

    client = OpenAI(api_key=KIMI_API_KEY, base_url=KIMI_BASE_URL, timeout=API_TIMEOUT)

    prompt = "你是一名专业的金融法律合规专家，精通汽车金融公司监管要求。\n\n"
    prompt += "请基于以下中国人民银行行政处罚信息，输出两部分内容：\n\n"
    prompt += "【处罚信息】\n"
    prompt += "当事人：" + data.get('party_name', '未知') + "\n"
    prompt += "文号：" + data.get('doc_number', '未知') + "\n"
    prompt += "违法行为：" + data.get('violation_type', '未知') + "\n"
    prompt += "处罚内容：" + data.get('penalty_content', '未知') + "\n"
    prompt += "决定机关：" + data.get('authority', '中国人民银行') + "\n"
    prompt += "决定日期：" + data.get('decision_date', '未知') + "\n"
    prompt += "公示期限：" + data.get('publicity_period', '未知') + "\n\n"
    prompt += "【要求】\n"
    prompt += "1. 专业洞见（3-4条）：分析该处罚反映的监管趋势、法律风险点、对行业的警示意义。每条100字以内。\n"
    prompt += "2. 汽车金融专项建议（4-5条）：从汽车金融公司展业特点（零售信贷、经销商网络、客户身份识别、资金清算、征信查询等）出发，给出具体可落地的合规建议。每条150字以内。\n\n"
    prompt += "请用中文输出，格式如下：\n\n"
    prompt += "洞见1|这里是洞见内容...\n"
    prompt += "洞见2|...\n"
    prompt += "洞见3|...\n\n"
    prompt += "建议1|标题|内容...\n"
    prompt += "建议2|标题|内容...\n"

    print("\n[AI分析] 调用 Kimi API (模型: " + KIMI_MODEL + ")...")
    print("   超时设置：" + str(API_TIMEOUT) + "秒，重试次数：" + str(API_RETRIES))

    for attempt in range(1, API_RETRIES + 1):
        try:
            print("   尝试 " + str(attempt) + "/" + str(API_RETRIES) + "...")
            resp = client.chat.completions.create(
                model=KIMI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=2000,
            )
            print("   成功")
            return parse_ai_response(resp.choices[0].message.content), True
        except Exception as e:
            print("   失败：" + str(e))
            if attempt < API_RETRIES:
                print("   等待" + str(API_RETRY_DELAY) + "秒后重试...")
                time.sleep(API_RETRY_DELAY)
            else:
                print("   已用尽重试次数，使用默认数据")
                break

    return get_default_data(), False


def get_default_data():
    return {
        'insights': [
            '反洗钱合规仍是监管高频处罚领域，客户身份识别与可疑交易报告义务履行不到位是主要违规点。',
            '"双罚制"持续深化，机构与个人同步追责已成常态，汽车金融公司需建立全员合规责任制。',
            '数据安全与征信合规要求升级，违反数据安全管理规定已成为新的处罚增长点。'
        ],
        'recommendations': [
            ('经销商KYC连带责任机制', '将经销商客户身份识别质量纳入合作准入与考核，对批量购车、大额贷款设置强化尽调流程。'),
            ('征信查询全流程管控', '建立事前授权+事中留痕+事后审计机制，确保每笔征信查询均有明确授权记录。'),
            ('反洗钱监测系统升级', '针对汽车金融场景设置可疑交易模型，如短期频繁换车、首付来源异常、异地集中购车等。'),
            ('资金清算隔离机制', '严格区分自有资金与客户资金，对经销商代收款、代还款建立资金隔离与对账机制。')
        ]
    }


def parse_ai_response(text):
    insights = []
    recommendations = []
    for line in text.strip().splitlines():
        line = line.strip()
        if line.startswith('洞见') and '|' in line:
            insights.append(line.split('|', 1)[1])
        elif line.startswith('建议') and '|' in line:
            parts = line.split('|')
            if len(parts) >= 3:
                recommendations.append((parts[1], parts[2]))
    if not insights:
        insights = [text[:200]]
    if not recommendations:
        recommendations = [('建议', text[:300])]
    return {'insights': insights, 'recommendations': recommendations}


def generate_html(data, analysis, is_ai_generated):
    output_path = "index.html"

    if is_ai_generated:
        source_tag = '<span style="color:#38a169;font-size:12px;">● 本分析由 Kimi AI 实时生成</span>'
    else:
        source_tag = '<span style="color:#dd6b20;font-size:12px;">● 本分析为默认模板（Kimi API 调用失败时降级）</span>'

    template = Template("""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <title>央行处罚监测日报</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #fafafa; color: #333; line-height: 1.6; padding: 40px 20px; }
        .container { max-width: 900px; margin: 0 auto; }
        .header { border-bottom: 2px solid #1a3a5c; padding-bottom: 20px; margin-bottom: 40px; }
        .header h1 { font-size: 24px; color: #1a3a5c; font-weight: 600; }
        .header .meta { font-size: 13px; color: #888; margin-top: 8px; }
        .section { margin-bottom: 40px; }
        .section-title { font-size: 16px; font-weight: 600; color: #1a3a5c; margin-bottom: 16px; padding-left: 12px; border-left: 3px solid #1a3a5c; }
        table { width: 100%; border-collapse: collapse; font-size: 14px; background: #fff; }
        th { background: #f5f5f5; padding: 12px; text-align: left; font-weight: 500; color: #555; border-bottom: 1px solid #e0e0e0; }
        td { padding: 12px; border-bottom: 1px solid #eee; vertical-align: top; }
        tr:hover { background: #fafafa; }
        .highlight { color: #c53030; font-weight: 500; }
        .insight-box { background: #fff; border: 1px solid #e0e0e0; border-radius: 6px; padding: 20px; margin-bottom: 16px; }
        .insight-box ul { list-style: none; padding-left: 0; }
        .insight-box li { padding: 6px 0; padding-left: 16px; position: relative; font-size: 14px; color: #555; }
        .insight-box li::before { content: "—"; position: absolute; left: 0; color: #999; }
        .rec-box { background: #f0fff4; border-left: 3px solid #38a169; padding: 16px 20px; margin-bottom: 12px; border-radius: 0 6px 6px 0; }
        .rec-box h4 { font-size: 14px; color: #276749; margin-bottom: 6px; }
        .rec-box p { font-size: 13px; color: #666; line-height: 1.5; }
        .footer { margin-top: 60px; padding-top: 20px; border-top: 1px solid #eee; font-size: 12px; color: #999; text-align: center; }
        .badge { display: inline-block; background: #1a3a5c; color: #fff; padding: 2px 10px; border-radius: 12px; font-size: 12px; margin-left: 8px; }
        .source-tag { margin-top: 8px; display: block; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>央行行政处罚监测日报 <span class="badge">汽车金融</span></h1>
            <div class="meta">{{report_date}} · 数据来源：中国人民银行</div>
        </div>
        <div class="section">
            <div class="section-title">一、处罚信息</div>
            <table>
                <tr><th style="width:140px">字段</th><th>内容</th></tr>
                <tr><td>当事人名称</td><td>{{party_name}}</td></tr>
                <tr><td>决定书文号</td><td class="highlight">{{doc_number}}</td></tr>
                <tr><td>违法行为类型</td><td>{{violation_type}}</td></tr>
                <tr><td>行政处罚内容</td><td>{{penalty_content}}</td></tr>
                <tr><td>作出机关</td><td>{{authority}}</td></tr>
                <tr><td>决定日期</td><td>{{decision_date}}</td></tr>
                <tr><td>公示期限</td><td>{{publicity_period}}</td></tr>
                <tr><td>备注</td><td>{{remarks}}</td></tr>
            </table>
            {% if source_url %}<p style="margin-top: 12px; font-size: 12px; color: #888;">原文链接：<a href="{{source_url}}" target="_blank">{{source_url}}</a></p>{% endif %}
        </div>
        <div class="section">
            <div class="section-title">二、专业洞见</div>
            {% for insight in insights %}<div class="insight-box"><ul><li>{{insight}}</li></ul></div>{% endfor %}
            <div class="source-tag">{{source_tag}}</div>
        </div>
        <div class="section">
            <div class="section-title">三、汽车金融专项建议</div>
            {% for title, content in recommendations %}<div class="rec-box"><h4>{{title}}</h4><p>{{content}}</p></div>{% endfor %}
            <div class="source-tag">{{source_tag}}</div>
        </div>
        <div class="footer">央行行政处罚监测日报 · {{report_date}} · 仅供内部合规参考</div>
    </div>
</body>
</html>""")

    html = template.render(
        report_date=datetime.now().strftime('%Y年%m月%d日'),
        party_name=data.get('party_name', '—'),
        doc_number=data.get('doc_number', '—'),
        violation_type=data.get('violation_type', '—'),
        penalty_content=data.get('penalty_content', '—'),
        authority=data.get('authority', '—'),
        decision_date=data.get('decision_date', '—'),
        publicity_period=data.get('publicity_period', '—'),
        remarks=data.get('remarks', '—'),
        source_url=data.get('source_url', ''),
        insights=analysis['insights'],
        recommendations=analysis['recommendations'],
        source_tag=source_tag
    )

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)

    print("\n已生成：" + output_path)


def main():
    print("=" * 60)
    print("央行行政处罚监测日报生成器")
    print("=" * 60)

    print("\n>>> 步骤1：爬取央行处罚公示页面")
    data = fetch_latest_penalty()
    if not data:
        print("\n爬取失败，生成默认页面")
        data = {
            'party_name': '—',
            'doc_number': '—',
            'violation_type': '—',
            'penalty_content': '—',
            'authority': '—',
            'decision_date': '—',
            'publicity_period': '—',
            'remarks': '—',
            'source_url': ''
        }

    print("\n抓取结果：")
    print("   当事人：" + data.get('party_name', '—'))
    print("   文号：" + data.get('doc_number', '—'))
    print("   违法行为：" + data.get('violation_type', '—'))
    print("   处罚内容：" + data.get('penalty_content', '—'))
    print("   公示期限：" + data.get('publicity_period', '—'))

    print("\n>>> 步骤2：Kimi AI 分析")
    analysis, is_ai = analyze_with_kimi(data)
    if is_ai:
        print("   [✓] 分析来源：Kimi AI 实时生成")
    else:
        print("   [!] 分析来源：默认模板")

    print("\n>>> 步骤3：生成HTML报告")
    generate_html(data, analysis, is_ai)

    print("\n" + "=" * 60)
    print("完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()
