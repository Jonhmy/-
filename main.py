import os
import re
import datetime
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image
import io
import pytesseract
import asyncio
from dotenv import load_dotenv

load_dotenv()

# 🔔 [เพิ่มบรรทัดนี้] สั่งชี้ทางให้โค้ดวิ่งไปเรียกใช้ Tesseract ของระบบ Linux บน Railway ได้ทันที
pytesseract.pytesseract.tesseract_cmd = 'tesseract'

TOKEN = os.getenv("DISCORD_TOKEN")
MONTHLY_MAX_LIMIT = int(os.getenv("MONTHLY_MAX_LIMIT", 10000))
# ... (หลังจากบรรทัดนี้ด้านล่างให้ใช้โค้ดเดิมทั้งหมดได้เลยครับ) ...

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

def process_roblox_image_fast(image_bytes):
    """ฟังก์ชันสแกนภาพเวอร์ชันยืดหยุ่นสูงสุด ดักจับแค่วันเวลาและตัวเลขติดลบท้ายบรรทัด"""
    image = Image.open(io.BytesIO(image_bytes))
    
    # ล็อกเป้าหมายการอ่านเฉพาะตัวอักษรภาษาอังกฤษและตัวเลข
    custom_config = r'--oem 3 --psm 6 -l eng'
    raw_text = pytesseract.image_to_string(image, config=custom_config)
    
    transactions = []
    total_spent_in_image = 0
    
    for line_str in raw_text.split('\n'):
        if not line_str.strip():
            continue
            
        # 1. ตรวจจับส่วนของวันเวลา (เช่น 08/15/26 6:03 PM) [cite: 2EADts.png]
        date_match = re.search(r'(\d{2}/\d{2}/\d{2})\s+(\d{1,2}:\d{2}\s*[APap][Mm])', line_str)
        
        if date_match:
            date_time_str = f"{date_match.group(1)} {date_match.group(2)}"
            date_time_str = re.sub(r'\s+', ' ', date_time_str)
            
            # ลบช่องว่างออกทั้งหมดเพื่อความสะอาดในการดึงตัวเลข
            clean_line = re.sub(r'\s+', '', line_str).lower()
            
            # แปลงตัวอักษรที่มักสับสนเป็นตัวเลขมาตรฐาน
            clean_line = clean_line.replace('o', '0').replace('q', '0').replace('@', '0').replace('⬡', '0')
            
            # 🔔 [จุดแก้ไขสําคัญ] ดักจับตัวเลขชุดสุดท้ายที่อยู่หลังเครื่องหมายลบ (-) [cite: 2EADts.png]
            # วิธีนี้จะไม่สนชื่อผู้รับเงินหรือสัญลักษณ์ไอคอนใดๆ ทั้งสิ้น ขอแค่มีเครื่องหมายลบกับตัวเลขด้านหลัง [cite: 2EADts.png]
            amount_match = re.search(r'-(\d+)\s*$', clean_line)
            
            if amount_match:
                amount = int(amount_match.group(1))
                
                # พยายามแกะชื่อผู้รับเงินแบบกว้างๆ (ถ้าเจอคำว่า to) ถ้าไม่เจอให้ใส่ชื่อพิกัดมาตรฐาน [cite: 2EADts.png]
                name_match = re.search(r'to([a-z0-9_\.]+)', clean_line)
                recipient = name_match.group(1) if name_match else "User"
                
                try:
                    tx_time = datetime.datetime.strptime(date_time_str, "%m/%d/%y %I:%M %p")
                    reset_time = tx_time + datetime.timedelta(days=30)
                    
                    # ตรวจสอบวันเวลากับปัจจุบันเพื่อใช้หักโควตาสรุปรายเดือน
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
    print(f'🤖 บอตเปิดออนไลน์สำเร็จในชื่อ: {bot.user.name}')
    try:
        synced = await bot.tree.sync()
        print(f"🔄 ซิงค์ Slash Commands สำเร็จแล้ว จำนวน {len(synced)} คำสั่ง")
    except Exception as e:
        print(f"❌ เกิดข้อผิดพลาดในการซิงค์คำสั่ง: {e}")

@bot.tree.command(name="check", description="ตรวจสอบประวัติการโอนและคำนวณโควตา Robux คงเหลือจากรูปภาพ")
@app_commands.describe(image="แนบรูปภาพหน้าจอประวัติการซื้อขาย (My Transactions)")
async def check_quota(interaction: discord.Interaction, image: discord.Attachment):
    if not any(image.filename.lower().endswith(ext) for ext in ['png', 'jpg', 'jpeg', 'webp']):
        await interaction.response.send_message("❌ ไฟล์ที่แนบมาไม่ใช่รูปภาพที่รองรับ (กรุณาใช้ png, jpg, jpeg, webp)", ephemeral=True)
        return

    # 1. เริ่มต้นระบบ (0%)
    await interaction.response.send_message("⏳ [░░░░░░░░░░] 0% • กำลังเตรียมไฟล์รูปภาพ...")
    
    try:
        # 2. อ่านข้อมูลภาพ (30%)
        image_bytes = await image.read()
        await interaction.edit_original_response(content="⏳ [███░░░░░░░] 30% • กำลังอัปโหลดรูปภาพเข้าสู่ระบบ...")
        
        # 3. สั่งสแกนตัวหนังสือด้วย Tesseract (60%)
        await interaction.edit_original_response(content="⏳ [██████░░░░] 60% • ระบบกำลังอ่านตัวอักษรและวิเคราะห์ข้อมูลธุรกรรม...")
        
        # รันบน Thread เบื้องหลังเพื่อความลื่นไหล
        results, total_spent = await asyncio.to_thread(process_roblox_image_fast, image_bytes)
        
        # 4. จัดเรียงข้อมูล (90%)
        await interaction.edit_original_response(content="⏳ [█████████░] 90% • กำลังคำนวณประวัติและจัดเรียงคิว Rolling Window...")
        await asyncio.sleep(0.3)

        if not results:
            await interaction.edit_original_response(content="❌ ไม่พบข้อมูลธุรกรรมการโอน Robux ในรูปภาพนี้ กรุณาตรวจสอบว่าใช้รูปหน้าจอจากหน้า My Transactions โดยตรงครับ")
            return
        
        remaining_roblox_quota = MONTHLY_MAX_LIMIT - total_spent
        if remaining_roblox_quota < 0:
            remaining_roblox_quota = 0
        
        embed = discord.Embed(
            title="📊 Сรุปประวัติและโควตา Robux คงเหลือ",
            color=discord.Color.teal()
        )
        
        embed.add_field(
            name="💡 Status โควตาในปัจจุบันของคุณ",
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
            
        # 5. สแกนเสร็จสมบูรณ์ (100%)
        await interaction.edit_original_response(content="✅ [██████████] 100% • ประมวลผลเสร็จสิ้น!", embed=embed)
        
    except Exception as e:
        print(f"❌ Error occurred during process: {str(e)}")
        await interaction.edit_original_response(content=f"❌ **เกิดข้อผิดพลาดในการประมวลผล:** `รายละเอียด: {str(e)}`")

bot.run(TOKEN)
