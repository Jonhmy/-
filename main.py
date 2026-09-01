import os
import re
import datetime
import discord
from discord.ext import commands
import easyocr
import cv2
import numpy as np

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

reader = easyocr.Reader(['en'])

# ⚙️ ตั้งค่าลิมิตสูงสุดรายเดือน (แก้ไขตัวเลขตรงนี้ได้ตามบัญชีของคุณ)
# - บัญชีปกติที่ยืนยัน 2-Step Verification ลิมิตคือ 10,000 Robux
# - บัญชีทั่วไป ลิมิตคือ 1,000 Robux
MONTHLY_MAX_LIMIT = 10000 

def process_roblox_image(image_bytes):
    nparr = np.frombuffer(image_bytes, np.uint8)
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    
    results = reader.readtext(image)
    
    lines = {}
    for (bbox, text, prob) in results:
        ymin = int(bbox)
        matched_line = None
        for y_key in lines.keys():
            if abs(ymin - y_key) < 15:
                matched_line = y_key
                break
        if matched_line is not None:
            lines[matched_line].append(text)
        else:
            lines[ymin] = [text]
            
    sorted_lines = [lines[k] for k in sorted(lines.keys())]
    transactions = []
    total_spent_in_image = 0 # ตัวแปรไว้นับยอดรวมในภาพ
    
    for line in sorted_lines:
        line_str = " ".join(line)
        
        date_match = re.search(r'(\d{2}/\d{2}/\d{2})\s+(\d{1,2}:\d{2}\s*[APap][Mm])', line_str)
        amount_match = re.search(r'-\s*([\d,]+)', line_str)
        name_match = re.search(r'Sent\s+Robux\s+to\s+([a-zA-Z0-9_\.]+)', line_str, re.IGNORECASE)
        
        if date_match and amount_match:
            date_time_str = f"{date_match.group(1)} {date_match.group(2)}"
            date_time_str = re.sub(r'\s+', ' ', date_time_str)
            
            amount = int(amount_match.group(1).replace(',', ''))
            recipient = name_match.group(1) if name_match else "ไม่ระบุชื่อ"
            
            try:
                tx_time = datetime.datetime.strptime(date_time_str, "%m/%d/%y %I:%M %p")
                reset_time = tx_time + datetime.timedelta(days=30)
                
                # ตรวจสอบว่า ยอดนี้ยังอยู่ในระยะเวลา 30 วันค้างโควตาหรือไม่ (อิงตามเวลาปัจจุบัน 1 ก.ย. 2026)
                # เพื่อความแม่นยำ จะนับเฉพาะยอดที่ยังไม่รีเซ็ตมาคำนวณหักลบ
                if reset_time > datetime.datetime.now():
                    total_spent_in_image += amount
                
                transactions.append({
                    "amount": amount,
                    "recipient": recipient,
                    "reset_datetime_obj": reset_time,
                    "reset_date_str": reset_time.strftime("%d/%m/%Y เวลา %H:%M น.")
                })
            except Exception as e:
                print(f"Error parsing date {date_time_str}: {e}")
                
    transactions.sort(key=lambda x: x["reset_datetime_obj"])
    return transactions, total_spent_in_image

@bot.event
async def on_ready():
    print(f'🤖 บอตพร้อมทำงาน: {bot.user.name}')

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    if message.attachments:
        for attachment in message.attachments:
            if any(attachment.filename.lower().endswith(ext) for ext in ['png', 'jpg', 'jpeg', 'webp']):
                
                processing_msg = await message.channel.send("🔍 กำลังประมวลผลประวัติการโอนและคำนวณโควตาคงเหลือให้ครับ...")
                
                try:
                    image_bytes = await attachment.read()
                    results, total_spent = process_roblox_image(image_bytes)
                    
                    if not results:
                        await processing_msg.edit(content="❌ ไม่พบข้อมูลธุรกรรม")
                        return
                    
                    # คำนวณยอดที่ยังสามารถส่งได้ในตอนนี้
                    remaining_roblox_quota = MONTHLY_MAX_LIMIT - total_spent
                    if remaining_roblox_quota < 0:
                        remaining_roblox_quota = 0
                    
                    embed = discord.Embed(
                        title="📊 สรุปประวัติและโควตา Robux คงเหลือ",
                        color=discord.Color.teal()
                    )
                    
                    # ⭐ [อัปเดต] กล่องข้อความไฮไลต์สรุปยอดที่ยังโอนได้ตอนนี้
                    embed.add_field(
                        name="💡 สถานะโควตาในปัจจุบันของคุณ",
                        value=f"• ลิมิตบัญชีของคุณรายเดือน: `{MONTHLY_MAX_LIMIT:,}` Robux\n"
                              f"• ยอดโอนที่ใช้ไป (ในรูป): `{total_spent:,}` Robux\n"
                              f"• ➡️ **ตอนนี้คุณยังส่งได้อีก:** **`{remaining_roblox_quota:,}` Robux**",
                        inline=False
                    )
                    
                    embed.add_field(name="───────────────", value="**📅 ลำดับคิวรอรีเซ็ตคืนโควตา (+30 วัน)**", inline=False)
                    
                    for idx, tx in enumerate(results, start=1):
                        embed.add_field(
                            name=f"{idx}. 💰 โอนออก -{tx['amount']:,} Robux",
                            value=f"**ผู้รับ:** {tx['recipient']}\n**วันคืนโควตา:** `{tx['reset_date_str']}`",
                            inline=False
                        )
                        
                    await message.channel.send(embed=embed)
                    await processing_msg.delete()
                    
                except Exception as e:
                    await processing_msg.edit(content=f"เกิดข้อผิดพลาด: {str(e)}")
                    
    await bot.process_commands(message)

bot.run('ใส่_TOKEN_ของ_BOT_ตรงนี้')
