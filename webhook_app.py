# -*- coding: utf-8 -*-
from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import os
import json
import time
import traceback
import openai

app = Flask(__name__)

# === ✅ 環境變數 ===
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
openai.api_key = OPENAI_API_KEY

# === ✅ Google Sheets 授權 ===
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]
if "GOOGLE_CREDS_JSON" in os.environ:
    creds_escaped = os.environ["GOOGLE_CREDS_JSON"]
    creds_json_str = json.loads(creds_escaped)
    creds_dict = json.loads(creds_json_str)
    with open("google-credentials.json", "w", encoding="utf-8") as f:
        json.dump(creds_dict, f)
    credentials = Credentials.from_service_account_file("google-credentials.json", scopes=scopes)
else:
    credentials = Credentials.from_service_account_file("google-credentials.json", scopes=scopes)

client = gspread.authorize(credentials)
sheet = client.open_by_key(SPREADSHEET_ID).sheet1

# === ✅ 資料寫入 ===
def write_record_to_sheet(record):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    total = record["單價"] * record["數量"]
    row = [
        now,
        record.get("分類", ""),
        record.get("品項", ""),
        record.get("單價", ""),
        record.get("數量", ""),
        total,
        record.get("備註", ""),
        record.get("攝取熱量(kcal)", ""),
        record.get("攝取糖份(g)", ""),
        record.get("剩餘量", ""),
        record.get("每日消耗(kcal)", "")
    ]
    sheet.append_row(row)
    print("✅ 寫入成功：", row)

# === ✅ 檢查資料格式 ===
def is_valid_record(record):
    try:
        return (
            isinstance(record["分類"], str) and
            isinstance(record["品項"], str) and
            isinstance(record["單價"], int) and
            isinstance(record["數量"], int)
        )
    except:
        return False

# === ✅ GPT 分析訊息（強制回傳 JSON 格式）===
def analyze_message_with_gpt(text, retry=1):
    prompt = f"""
你是一個記帳助理，請將下列用戶輸入文字轉換成 JSON 格式，格式如下：

{{
  "分類": "食",             
  "品項": "蘋果",
  "單價": 12,
  "數量": 1,
  "備註": "LINE輸入",
  "攝取熱量(kcal)": 100,
  "攝取糖份(g)": 15,
  "剩餘量": "",
  "每日消耗(kcal)": ""
}}

如果資訊不足（例如沒有提到數量或分類），請填上 null 或 ""，不要猜測。只回傳 JSON。
使用者輸入：
{text}
    """.strip()

    try:
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )
        content = response.choices[0].message.content.strip()
        print("📤 GPT 回傳內容：", content)

        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("找不到有效 JSON")

        json_str = content[start:end+1]
        return json.loads(json_str)

    except Exception as e:
        print("❌ GPT 分析錯誤：", e)
        if retry > 0:
            print("🔁 嘗試重新呼叫 GPT")
            time.sleep(1)
            return analyze_message_with_gpt(text, retry=retry-1)
        return None

# === ✅ webhook 接收入口 ===
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# === ✅ 處理使用者訊息 ===
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()
    print("📩 收到訊息：", text)

    CANCEL_KEYWORDS = ["不用處理", "繞過", "結束", "跳過", "沒關係"]
    if any(kw in text for kw in CANCEL_KEYWORDS):
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="✅ 已中斷處理")
        )
        return

    try:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="⏳ 處理中，查詢資料中...")
        )
    except:
        pass

    try:
        # Step 1: 查找歷史類似資料（可選）
        all_data = sheet.get_all_values()
        item = text.split()[0]
        matched_rows = [row for row in all_data[1:] if item in row]
        if matched_rows:
            preview = "\n".join(["｜".join(r[:5]) for r in matched_rows[:3]])
            line_bot_api.push_message(
                event.source.user_id,
                TextSendMessage(text=f"🔍 找到類似資料：\n{preview}")
            )

        # Step 2: GPT 分析
        record = analyze_message_with_gpt(text)
        if not record:
            line_bot_api.push_message(
                event.source.user_id,
                TextSendMessage(text="❌ 分析失敗，請再試一次或換句話說")
            )
            return

        # Step 3: 缺資料補問
        MISSING_FIELDS = []
        if not record.get("分類"): MISSING_FIELDS.append("分類（如食/衣/住/行）")
        if not record.get("品項"): MISSING_FIELDS.append("品項（如蘋果/衣服）")
        if not isinstance(record.get("單價"), int): MISSING_FIELDS.append("單價（例如：50元）")
        if not isinstance(record.get("數量"), int): MISSING_FIELDS.append("數量（例如：1個）")

        if MISSING_FIELDS:
            question = "❓ 我需要更多資訊：\n" + "\n".join(f"- {f}" for f in MISSING_FIELDS)
            line_bot_api.push_message(
                event.source.user_id,
                TextSendMessage(text=question)
            )
            return

        # Step 4: 寫入 Google Sheets
        write_record_to_sheet(record)
        reply_text = f"✅ 已記錄：{record['品項']} × {record['數量']} = {record['單價'] * record['數量']} 元"
        line_bot_api.push_message(
            event.source.user_id,
            TextSendMessage(text=reply_text)
        )

    except Exception as e:
        print("🔴 錯誤：", e)
        traceback.print_exc()
        line_bot_api.push_message(
            event.source.user_id,
            TextSendMessage(text="❌ 錯誤，請稍後再試或手動輸入")
        )

# === ✅ Render 啟動點 ===
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
