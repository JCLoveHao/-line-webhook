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

# === ✅ 寫入表單 ===
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
        record.get("備註", "")
    ]
    sheet.append_row(row)
    print("✅ 寫入成功：", row)

# === ✅ GPT 分析訊息（強制 JSON）===
def analyze_message_with_gpt(text, retry=2):
    prompt = f"""
你是一個 LINE 記帳小幫手，請將以下訊息轉為【純 JSON】格式，格式如下（不要多加說明文字）：

{{
  "分類": "食",
  "品項": "蘋果",
  "單價": 12,
  "數量": 1,
  "備註": "LINE輸入"
}}

⚠️ 請注意：
- 僅回傳一個 JSON 區塊，不需要說明文字
- 所有欄位都要有，若沒有請填 ""
- 數字欄位必須是數字（如：單價、數量）
- 不要換行、不要加上「以下是」或「這是你要的」之類的語句

使用者輸入：
{text}
    """
    try:
        response = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )
        content = response.choices[0].message.content.strip()
        print("📤 GPT 原始回應：", content)

        start = content.find("{")
        end = content.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("找不到 JSON 區塊")

        json_str = content[start:end+1]
        json_str = json_str.replace("“", "\"").replace("”", "\"").replace("‘", "\"").replace("’", "\"")
        json_str = json_str.replace("\n", "").replace("\\", "")
        return json.loads(json_str)

    except Exception as e:
        print("❌ GPT 分析錯誤：", e)
        print("⚠️ GPT 回傳內容：", locals().get("content", "（無內容）"))
        if retry > 0:
            print("🔁 正在重試...")
            time.sleep(1.5)
            return analyze_message_with_gpt(text, retry=retry-1)
        return None

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

# === ✅ 智慧傳訊工具 ===
def smart_push_message(event, text):
    try:
        if hasattr(event.source, 'user_id') and event.source.user_id:
            line_bot_api.push_message(event.source.user_id, TextSendMessage(text=text))
        elif hasattr(event.source, 'group_id') and event.source.group_id:
            line_bot_api.push_message(event.source.group_id, TextSendMessage(text=text))
        elif hasattr(event.source, 'room_id') and event.source.room_id:
            line_bot_api.push_message(event.source.room_id, TextSendMessage(text=text))
        else:
            print("⚠️ 無法識別訊息來源")
    except Exception as e:
        print("⚠️ 傳送訊息錯誤：", e)

# === ✅ 處理訊息 ===
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()
    print("📩 收到訊息：", text)

    CANCEL_KEYWORDS = ["不用處理", "繞過", "結束", "跳過", "沒關係"]
    if any(kw in text for kw in CANCEL_KEYWORDS):
        smart_push_message(event, "✅ 已中斷處理")
        return

    try:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⏳ 處理中..."))
    except:
        pass

    try:
        record = analyze_message_with_gpt(text)
        if not record:
            smart_push_message(event, "❌ 分析失敗，請再試一次")
            return

        # 檢查缺欄位
        MISSING = []
        if not record.get("分類"): MISSING.append("分類（如食/衣/住/行）")
        if not record.get("品項"): MISSING.append("品項（如蘋果）")
        if not isinstance(record.get("單價"), int): MISSING.append("單價（如50元）")
        if not isinstance(record.get("數量"), int): MISSING.append("數量（如1份）")

        if MISSING:
            msg = "❓ 請補充以下資料：\n" + "\n".join(f"- {m}" for m in MISSING)
            smart_push_message(event, msg)
            return

        write_record_to_sheet(record)
        reply = f"✅ 已記錄：{record['品項']} × {record['數量']} = {record['單價'] * record['數量']} 元"
        smart_push_message(event, reply)

    except Exception as e:
        print("❌ 發生錯誤：", e)
        traceback.print_exc()
        smart_push_message(event, "❌ 系統錯誤，請稍後再試")

# === ✅ Flask 執行點（Render專用） ===
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
