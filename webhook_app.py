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
import openai  # ✅ 加入 GPT

app = Flask(__name__)

# === ✅ 環境設定 ===
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

# === ✅ OpenAI 金鑰設定 ===
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

# === ✅ GPT 處理函式 ===
def ask_gpt_for_record(text):
    prompt = f"""
你是一位記帳助手，請從使用者輸入的句子中判斷以下欄位（如無法判斷則留空）：
1. 分類（食、衣、住、行、育、樂、醫、其他）
2. 品項
3. 單價
4. 數量
5. 備註（可選）
請輸出 JSON 格式。

輸入：{text}
    """
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "你是專業的記帳助理，擅長資訊結構化。"},
            {"role": "user", "content": prompt}
        ],
        temperature=0.3
    )
    reply = response["choices"][0]["message"]["content"]
    return json.loads(reply)

# === ✅ 寫入資料 ===
def write_record_to_sheet(record):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    total = record.get("單價", 0) * record.get("數量", 1)
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

# === ✅ webhook 接收 ===
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# === ✅ 處理訊息 ===
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()
    print("📩 接收到訊息：", text)

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
            TextSendMessage(text="⏳ 處理中，分析內容...")
        )
    except:
        pass

    try:
        record = ask_gpt_for_record(text)
        record.setdefault("分類", "其他")
        record.setdefault("單價", 0)
        record.setdefault("數量", 1)
        record.setdefault("備註", "GPT自動分類")
        record.setdefault("攝取熱量(kcal)", "")
        record.setdefault("攝取糖份(g)", "")
        record.setdefault("剩餘量", "")
        record.setdefault("每日消耗(kcal)", "")

        write_record_to_sheet(record)

        reply_text = f"✅ 已記錄：{record.get('品項', '')}，{record.get('單價', 0)} 元"
        line_bot_api.push_message(
            event.source.user_id,
            TextSendMessage(text=reply_text)
        )

    except Exception as e:
        print("🔴 錯誤：", e)
        traceback.print_exc()
        line_bot_api.push_message(
            event.source.user_id,
            TextSendMessage(text="❌ 發生錯誤，請稍後再試或手動輸入。")
        )

# === ✅ Flask 啟動點 ===
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
