import os
import re
import datetime
import discord
from discord import app_commands
from discord.ext import commands
import easyocr
import cv2
import numpy as np
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
MONTHLY_MAX_LIMIT = int(os.getenv("MONTHLY_MAX_LIMIT", 10000))

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# 🔔 [ย้ายมาไว้ตรงนี้] บังคับให้บอตโหลดไฟล์โมเดล AI เตรียมพร้อมทันทีตั้งแต่เปิดเครื่อง
print("📥 กำลังดาวน์โหลดและเตรียมโมเดล EasyOCR...")
reader = easyocr.Reader(['en'])
print("✅ โมเดล EasyOCR พร้อมใช้งานแล้ว!")

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
    total_spent_in_image = 0
    
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
    print(f'🤖 บอตออนไลน์แล้วในชื่อ: {bot.user.name}')
    try:
        # ⭐ ซิงค์คำสั่ง Slash Command ทั้งหมดไปยังระบบของ Discord ทั่วโลก
        synced = await bot.tree.sync()
        print(f"🔄 ซิงค์ Slash Commands สำเร็จแล้ว จำนวน {len(synced)} คำสั่ง")
    except Exception as e:
        print(f"❌ เกิดข้อผิดพลาดในการซิงค์คำสั่ง: {e}")

# ⭐ สร้างคำสั่ง Slash Command ชื่อ /check
@bot.tree.command(name="check", description="ตรวจสอบประวัติการโอนและคำนวณโควตา Robux คงเหลือจากรูปภาพ")
@app_commands.describe(image="แนบรูปภาพหน้าจอประวัติการซื้อขาย (My Transactions)")
async def check_quota(interaction: discord.Interaction, image: discord.Attachment):
    # ตรวจสอบนามสกุลไฟล์รูปภาพที่ผู้ใช้แนบเข้ามาในช่อง Slash Command
    if not any(image.filename.lower().endswith(ext) for ext in ['png', 'jpg', 'jpeg', 'webp']):
        await interaction.response.send_message("❌ ไฟล์ที่แนบมาไม่ใช่รูปภาพที่รองรับ (กรุณาใช้ png, jpg, jpeg, webp)", ephemeral=True)
        return

    # ตอบกลับข้อความแรกเพื่อเริ่มการทำงาน (เพราะระบบ OCR ใช้เวลาประมวลผล)
    await interaction.response.send_message("🔍 กำลังประมวลผลประวัติการโอนและคำนวณโควตาคงเหลือให้สักครู่นะครับ...")
    
    try:
        image_bytes = await image.read()
        results, total_spent = process_roblox_image(image_bytes)
        
        if not results:
            await interaction.edit_original_response(content="❌ ไม่พบข้อมูลธุรกรรมในรูปภาพนี้ กรุณาใช้รูปถ่ายที่ชัดเจนและสว่างขึ้นครับ")
            return
        
        remaining_roblox_quota = MONTHLY_MAX_LIMIT - total_spent
        if remaining_roblox_quota < 0:
            remaining_roblox_quota = 0
        
        embed = discord.Embed(
            title="📊 สรุปประวัติและโควตา Robux คงเหลือ",
            color=discord.Color.teal()
        )
        
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
            
        # อัปเดตข้อความเดิมด้วยตาราง Embed สรุปผล
        await interaction.edit_original_response(content=None, embed=embed)
        
    except Exception as e:
        await interaction.edit_original_response(content=f"เกิดข้อผิดพลาดในการอ่านรูปภาพ: {str(e)}")

bot.run(TOKEN)
