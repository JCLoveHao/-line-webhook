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

# ✅ 環境變數
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)
openai.api_key = OPENAI_API_KEY

# ✅ Google Sheets 授權
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

# ✅ 寫入 Google Sheets 函式
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

# ✅ 呼叫 GPT 判斷內容
def analyze_message_with_gpt(message):
    prompt = f"""
你是一個記帳小助手，請協助分析以下文字，並用 JSON 回傳這些欄位：
分類（食、衣、住、行、育、樂、醫、其他）
品項
單價（整數）
數量（整數，若無則預設1）
備註（若有附加說明可寫入）

請根據下列訊息拆解：
「{message}」
只回傳 JSON，不需要解釋。
    """
    try:
        response = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )
        content = response.choices[0].message.content.strip()
        print("🧠 GPT 回傳：", content)
        return json.loads(content)
    except Exception as e:
        print("🔴 GPT 分析錯誤：", e)
        return None

# ✅ webhook 接收
@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# ✅ LINE 訊息處理
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()
    print("📩 接收訊息：", text)

    CANCEL_KEYWORDS = ["不用處理", "跳過", "結束", "略過"]
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
        start_time = time.time()
        record = analyze_message_with_gpt(text)

        if not record or "單價" not in record:
            raise ValueError("GPT 無法解析內容")

        # 查詢舊資料（限5秒）
        all_data = sheet.get_all_values()
        matched_rows = []
        for row in all_data[1:]:
            if time.time() - start_time > 5:
                line_bot_api.push_message(
                    event.source.user_id,
                    TextSendMessage(text="⚠️ 查詢超過5秒自動停止，如要補充請輸入「沒有」或提供更多資訊")
                )
                return
            if record["品項"] in row:
                matched_rows.append(row)

        if matched_rows:
            preview = "\n".join(["｜".join(r[:5]) for r in matched_rows[:3]])
            line_bot_api.push_message(
                event.source.user_id,
                TextSendMessage(text=f"🔍 找到類似資料：\n{preview}")
            )

        record.setdefault("數量", 1)
        record.setdefault("攝取熱量(kcal)", "")
        record.setdefault("攝取糖份(g)", "")
        record.setdefault("剩餘量", "")
        record.setdefault("每日消耗(kcal)", "")
        write_record_to_sheet(record)

        reply_text = f"✅ 已記錄：{record['品項']}，{record['單價']}元 x {record['數量']}"
        line_bot_api.push_message(
            event.source.user_id,
            TextSendMessage(text=reply_text)
        )

    except Exception as e:
        print("🔴 錯誤：", e)
        traceback.print_exc()
        line_bot_api.push_message(
            event.source.user_id,
            TextSendMessage(text="❌ 抱歉，這筆資料我無法理解，請手動輸入或重新描述")
        )

# ✅ Render 啟動
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
