#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
央行行政处罚监测日报生成器 - GitHub Actions 版本
"""

import os
import re
import sys
import time
from datetime import datetime

from jinja2 import Template
from openai import OpenAI

# ========== 配置 ==========
KIMI_API_KEY = os.getenv("KIMI_API_KEY", "")
KIMI_BASE_URL = "https://api.moonshot.ai/v1"
KIMI_MODEL = "kimi-k2-turbo"


def fetch_latest_penalty():
    from playwright.sync_api import sync_playwright

    url = "https://www.pbc.gov.cn/zhengwugongkai/4081330/4081344/4081407/4081705/index.html"

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

        print("[1/3] 访问：" + url)
        page.goto(url, wait_until="networkidle", timeout=30000)
        page.wait_for_timeout(5000)

        selectors = [
            "a[href*='银罚决字']",
            "a[title*='银罚决字']",
            "ul li a",
            ".TRS_Editor a",
            "table a",
        ]

        link = None
        for sel in selectors:
            try:
                link = page.query_selector(sel)
                if link and '银罚决字' in (link.inner_text() or ''):
                    break
            except:
                continue

        if not link:
            print("未找到处罚链接")
            browser.close()
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
        detail_page.goto(href, wait_until="networkidle", timeout=30000)
        detail_page.wait_for_timeout(3000)

        raw_text = detail_page.inner_text("body")
        data = extract_structured_data(detail_page, raw_text)
        data['raw_text'] = raw_text[:3000]
        data['source_url'] = href
        data['list_title'] = title

        print("[3/3] 详情页完成")
        browser.close()
        return data


def extract_structured_data(page, raw_text):
    data = {
        'party_name': '',
        'doc_number': '',
        'violation_type': '',
        'penalty_content': '',
        'authority': '',
        'decision_date': '',
        'publicity_period': '五年',
        'remarks': ''
    }

    try:
        rows = page.query_selector_all("table tr")
        for row in rows:
            cells = row.query_selector_all("td, th")
            if len(cells) >= 2:
                label = cells[0].inner_text().strip()
                value = cells[1].inner_text().strip()
                if '当事人' in label or '被处罚' in label:
                    data['party_name'] = value
                elif '文号' in label:
                    data['doc_number'] = value
                elif '违法' in label or '违规' in label:
                    data['violation_type'] = value
                elif '处罚内容' in label:
                    data['penalty_content'] = value
                elif '机关' in label:
                    data['authority'] = value
                elif '日期' in label and '决定' in label:
                    data['decision_date'] = value
    except:
        pass

    if not data['party_name']:
        lines = raw_text.splitlines()
        for line in lines:
            line = line.strip()
            if len(line) >= 2 and len(line) <= 30:
                if any(k in line for k in ['公司', '银行', '集团', '中心', '协会']):
                    data['party_name'] = line
                    break

    if not data['doc_number']:
        # 不用原始字符串，避免反斜杠问题
        m = re.search('银罚决字[〔]?\d{4}[〕]?\d+号', raw_text)
        if m:
            data['doc_number'] = m.group(0)

    if not data['decision_date']:
        m = re.search('\d{4}年\d{1,2}月\d{1,2}日', raw_text)
        if m:
            data['decision_date'] = m.group(0)

    return data


def analyze_with_kimi(data):
    if not KIMI_API_KEY:
        print("未设置 KIMI_API_KEY，使用默认数据")
        return get_default_data()

    client = OpenAI(api_key=KIMI_API_KEY, base_url=KIMI_BASE_URL, timeout=60)

    prompt = "你是一名专业的金融法律合规专家，精通汽车金融公司监管要求。\n\n"
    prompt += "请基于以下中国人民银行行政处罚信息，输出两部分内容：\n\n"
    prompt += "【处罚信息】\n"
    prompt += "当事人：" + data.get('party_name', '未知') + "\n"
    prompt += "文号：" + data.get('doc_number', '未知') + "\n"
    prompt += "违法行为：" + data.get('violation_type', '未知') + "\n"
    prompt += "处罚内容：" + data.get('penalty_content', '未知') + "\n"
    prompt += "决定机关：" + data.get('authority', '中国人民银行') + "\n"
    prompt += "决定日期：" + data.get('decision_date', '未知') + "\n\n"
    prompt += "【要求】\n"
    prompt += "1. 专业洞见（3-4条）：分析该处罚反映的监管趋势、法律风险点、对行业的警示意义。每条100字以内。\n"
    prompt += "2. 汽车金融专项建议（4-5条）：从汽车金融公司展业特点（零售信贷、经销商网络、客户身份识别、资金清算、征信查询等）出发，给出具体可落地的合规建议。每条150字以内。\n\n"
    prompt += "请用中文输出，格式如下：\n\n"
    prompt += "洞见1|这里是洞见内容...\n"
    prompt += "洞见2|...\n"
    prompt += "洞见3|...\n\n"
    prompt += "建议1|标题|内容...\n"
    prompt += "建议2|标题|内容...\n"

    print("\n[AI分析] 调用 Kimi API...")
    max_retries = 3

    for attempt in range(1, max_retries + 1):
        try:
            print("   尝试 " + str(attempt) + "/" + str(max_retries) + "...")
            resp = client.chat.completions.create(
                model=KIMI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=2000,
            )
            print("   成功")
            return parse_ai_response(resp.choices[0].message.content)
        except Exception as e:
            print("   失败：" + str(e))
            if attempt < max_retries:
                time.sleep(3)
            else:
                print("   已用尽重试次数，使用默认数据")
                break

    return get_default_data()


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


def generate_html(data, analysis):
    output_path = "index.html"

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
        </div>
        <div class="section">
            <div class="section-title">三、汽车金融专项建议</div>
            {% for title, content in recommendations %}<div class="rec-box"><h4>{{title}}</h4><p>{{content}}</p></div>{% endfor %}
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
        publicity_period=data.get('publicity_period', '五年'),
        remarks=data.get('remarks', '—'),
        source_url=data.get('source_url', ''),
        insights=analysis['insights'],
        recommendations=analysis['recommendations']
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
            'publicity_period': '五年',
            'remarks': '爬取失败',
            'source_url': ''
        }

    print("\n抓取结果：")
    print("   当事人：" + data.get('party_name', '—'))
    print("   文号：" + data.get('doc_number', '—'))

    print("\n>>> 步骤2：Kimi AI 分析")
    analysis = analyze_with_kimi(data)

    print("\n>>> 步骤3：生成HTML报告")
    generate_html(data, analysis)

    print("\n" + "=" * 60)
    print("完成！")
    print("=" * 60)


if __name__ == "__main__":
    main()
