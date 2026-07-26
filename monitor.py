#!/usr/bin/env python3
"""
南航公告监控系统
- 每30分钟检查南航官网公告页面
- 发现新公告通过飞书机器人推送通知
- 失败自动重试（最多3次，间隔5分钟）
- 连续3次失败发送飞书告警
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# ========== 配置 ==========
FEISHU_WEBHOOK = os.environ.get("FEISHU_WEBHOOK", "")

# 南航公告 API 配置
BASE_URL = "https://m.csair.com/prod-api/page/site/openApi/getNews"
API_PARAMS = {
    "lcode": "zh",
    "acode": "cn",
    "type": "PC",
    "clientChannel": "ECS-C",
}
# 监控的公告页面（2026年 + 2025年）
MONITOR_PAGES = [
    {"pid": "38416", "label": "2026年公告"},
    {"pid": "18160", "label": "2025年公告"},
]

STATE_FILE = "state.json"
MAX_RETRIES = 3
RETRY_INTERVAL = 300  # 5分钟

# 北京时区
BEIJING_TZ = timezone(timedelta(hours=8))


def now_str():
    """返回北京时间字符串"""
    return datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")


# ========== 飞书通知 ==========
def send_feishu(content, is_alert=False):
    """发送飞书消息"""
    if not FEISHU_WEBHOOK:
        print("[WARN] FEISHU_WEBHOOK 未配置，跳过发送")
        return False

    if is_alert:
        # 告警消息 - 红色卡片
        card = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": "⚠️ 南航公告监控异常"},
                    "template": "red",
                },
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": content}},
                    {"tag": "note", "elements": [{"tag": "plain_text", "content": f"时间：{now_str()}"}]},
                ],
            },
        }
    else:
        # 新公告通知 - 蓝色卡片
        card = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": "📢 南航新公告"},
                    "template": "blue",
                },
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": content}},
                    {"tag": "hr"},
                    {"tag": "note", "elements": [{"tag": "plain_text", "content": f"检测时间：{now_str()}"}]},
                ],
            },
        }

    data = json.dumps(card).encode("utf-8")
    req = Request(FEISHU_WEBHOOK, data=data, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("code") == 0 or result.get("StatusCode") == 0:
                print(f"[OK] 飞书通知发送成功")
                return True
            else:
                print(f"[ERROR] 飞书通知发送失败: {result}")
                return False
    except Exception as e:
        print(f"[ERROR] 飞书通知发送异常: {e}")
        return False


# ========== 南航 API ==========
def fetch_announcements(page):
    """获取指定页面的公告列表"""
    params = dict(API_PARAMS)
    params["pid"] = page["pid"]
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{BASE_URL}?{query}"

    req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"})
    with urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8")

    # 解析 JSONP: getContentList([...])
    match = re.search(r"getContentList\((.+)\)\s*;?\s*$", raw, re.DOTALL)
    if match:
        data = json.loads(match.group(1))
    else:
        # 尝试直接解析 JSON
        data = json.loads(raw)

    return data


# ========== 状态管理 ==========
def load_state():
    """加载已知公告状态"""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"seen_ids": {}, "last_check": ""}


def save_state(state):
    """保存公告状态"""
    state["last_check"] = now_str()
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ========== 主逻辑 ==========
def check_and_notify():
    """检查新公告并发送通知"""
    state = load_state()
    seen_ids = state.get("seen_ids", {})

    all_new = []
    for page in MONITOR_PAGES:
        print(f"[INFO] 正在检查 {page['label']} (pid={page['pid']})...")
        announcements = fetch_announcements(page)
        print(f"[INFO] 获取到 {len(announcements)} 条公告")

        for ann in announcements:
            ann_id = ann.get("catalogue", "")
            if ann_id and ann_id not in seen_ids:
                # 新公告
                ann["page_label"] = page["label"]
                all_new.append(ann)
                seen_ids[ann_id] = {
                    "title": ann.get("lname", ""),
                    "pushTime": ann.get("pushTime", ""),
                    "first_seen": now_str(),
                }

    if all_new:
        print(f"[INFO] 发现 {len(all_new)} 条新公告！")
        for ann in all_new:
            title = ann.get("lname", "无标题")
            push_time = ann.get("pushTime", "")
            localpath = ann.get("localpath", "")
            link = f"https://www.csair.com/mcms/mcmsNewSite/zh/cn/#{localpath}" if localpath else ""

            content = f"**{title}**\n"
            content += f"发布时间：{push_time}\n"
            content += f"来源：{ann.get('page_label', '')}\n"
            if link:
                content += f"\n[👉 点击查看公告]({link})"

            send_feishu(content, is_alert=False)
            time.sleep(1)  # 避免飞书限流
    else:
        print("[INFO] 没有新公告")

    # 更新状态
    state["seen_ids"] = seen_ids
    save_state(state)
    return len(all_new)


def main():
    """主函数，带重试逻辑"""
    print(f"[INFO] ========== 南航公告监控启动 ==========")
    print(f"[INFO] 当前时间：{now_str()}")

    last_error = ""
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            new_count = check_and_notify()
            print(f"[INFO] 第 {attempt} 次执行成功，发现 {new_count} 条新公告")
            print(f"[INFO] ========== 监控结束 ==========")
            return  # 成功则退出

        except Exception as e:
            last_error = str(e)
            print(f"[ERROR] 第 {attempt} 次执行失败: {last_error}")
            if attempt < MAX_RETRIES:
                print(f"[INFO] {RETRY_INTERVAL} 秒后重试...")
                time.sleep(RETRY_INTERVAL)

    # 3次都失败，发送告警
    alert_msg = f"连续 {MAX_RETRIES} 次执行失败\n"
    alert_msg += f"最后错误：{last_error}\n"
    alert_msg += f"请检查网络连接或南航API是否正常"

    print(f"[ALERT] {alert_msg}")
    send_feishu(alert_msg, is_alert=True)
    print(f"[INFO] ========== 监控结束（失败） ==========")


if __name__ == "__main__":
    main()
