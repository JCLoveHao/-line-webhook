import re
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

app = Flask(__name__)

# === ✅ 環境設定 ===
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "")
SPREADSHEET_ID = os.environ.get("SPREADSHEET_ID", "")

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

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

# === ✅ 寫入 Google Sheets ===
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

# === ✅ webhook ===
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
    print("📬 接收訊息：", text)

    # 使用者中斷關鍵字
    if any(kw in text for kw in ["不用處理", "繞過", "結束", "跳過", "沒關係"]):
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 已中斷處理"))
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

        # === ✅ 自動解析 item 與 price，支援多種格式 ===
        match = re.match(r"(.+?)[：:：]?\s*(\d+)", text)  # 中/英文冒號 或 沒空格
        if not match:
            raise ValueError("格式錯誤，無法解析品項與金額")
        item = match.group(1).strip()
        price = int(match.group(2))

        # === 🔍 查詢歷史品項是否出現過 ===
        all_data = sheet.get_all_values()
        matched_rows = []
        for row in all_data[1:]:
            if time.time() - start_time > 5:
                line_bot_api.push_message(
                    event.source.user_id,
                    TextSendMessage(text="⚠️ 查詢超過 5 秒自動停止，請輸入『沒有』或補充資料")
                )
                return
            if item in row:
                matched_rows.append(row)

        if matched_rows:
            preview = "\n".join(["｜".join(r[:5]) for r in matched_rows[:3]])
            line_bot_api.push_message(
                event.source.user_id,
                TextSendMessage(text=f"🔍 找到類似資料：\n{preview}")
            )

        # === ✅ 寫入記錄 ===
        record = {
            "分類": "食",
            "品項": item,
            "單價": price,
            "數量": 1,
            "備註": "LINE輸入",
            "攝取熱量(kcal)": "",
            "攝取糖份(g)": "",
            "剩餘量": "",
            "每日消耗(kcal)": ""
        }
        write_record_to_sheet(record)

        reply_text = f"✅ 已記錄 {item}，{price} 元"
        line_bot_api.push_message(event.source.user_id, TextSendMessage(text=reply_text))

    except Exception as e:
        print("🔴 錯誤：", e)
        traceback.print_exc()
        line_bot_api.push_message(
            event.source.user_id,
            TextSendMessage(text="❌ 發生錯誤，請稍後再試或手動輸入資料。")
        )

# === ✅ 啟動 ===
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
