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

def process_roblox_image_debug(image_bytes):
    """ฟังก์ชันสแกนภาพเพื่อดึงข้อความดิบทั้งหมดออกมาตรวจสอบหาจุดเพี้ยน"""
    image = Image.open(io.BytesIO(image_bytes))
    
    # สั่งเปิดสิทธิ์อ่านภาษาอังกฤษและตัวเลขในระดับบรรทัด
    custom_config = r'--oem 3 --psm 6 -l eng'
    raw_text = pytesseract.image_to_string(image, config=custom_config)
    
    transactions = []
    total_spent_in_image = 0
    lines_scanned = []  # เก็บข้อความดิบแต่ละบรรทัดเอาไว้ส่งดู
    
    for line_str in raw_text.split('\n'):
        if not line_str.strip():
            continue
            
        # บันทึกบรรทัดที่สแกนได้เพื่อนำไปแสดงผลตรวจสอบ
        lines_scanned.append(line_str.strip())
        
        # ค้นหาวันเวลา (เช่น 08/15/26 6:03 PM) [cite: 2EADts.png]
        date_match = re.search(r'(\d{2}/\d{2}/\d{2})\s+(\d{1,2}:\d{2}\s*[APap][Mm])', line_str)
        
        if date_match:
            date_time_str = f"{date_match.group(1)} {date_match.group(2)}"
            date_time_str = re.sub(r'\s+', ' ', date_time_str)
            
            # ลบช่องว่างออกทั้งหมดเพื่อวิเคราะห์คำหลัก
            clean_line = re.sub(r'\s+', '', line_str).lower()
            
            # ดักจับตัวเลขชุดสุดท้ายที่อยู่หลังเครื่องหมายลบ (-) [cite: 2EADts.png]
            amount_match = re.search(r'-(\d+)\s*$', clean_line)
            
            if amount_match:
                amount = int(amount_match.group(1))
                
                name_match = re.search(r'to([a-z0-9_\.]+)', clean_line)
                recipient = name_match.group(1) if name_match else "User"
                
                try:
                    tx_time = datetime.datetime.strptime(date_time_str, "%m/%d/%y %I:%M %p")
                    reset_time = tx_time + datetime.timedelta(days=30)
                    
                    if reset_time > datetime.datetime.now():
                        total_spent_in_image += amount
                    
                    transactions.append({
                        "amount": amount,
                        "recipient": recipient,
                        "reset_date_str": reset_time.strftime("%d/%m/%Y เวลา %H:%M น.")
                    })
                except Exception as e:
                    print(f"Error: {e}")
                    
    return transactions, total_spent_in_image, lines_scanned

@bot.tree.command(name="check", description="ทดสอบสแกนภาพและดูข้อความดิบที่ AI มองเห็น")
@app_commands.describe(image="แนบรูปภาพหน้าจอประวัติการซื้อขาย (My Transactions)")
async def check_quota(interaction: discord.Interaction, image: discord.Attachment):
    if not any(image.filename.lower().endswith(ext) for ext in ['png', 'jpg', 'jpeg', 'webp']):
        await interaction.response.send_message("❌ ไฟล์ที่แนบมาไม่ใช่รูปภาพ", ephemeral=True)
        return

    await interaction.response.send_message("⏳ กำลังให้ AI แกะข้อความและดึง Log ข้อมูลดิบออกมาสักครู่...")
    
    try:
        image_bytes = await image.read()
        
        # เรียกใช้งานฟังก์ชันเวอร์ชันส่งค่า Debug Log ออกมา
        results, total_spent, lines_scanned = await asyncio.to_thread(process_roblox_image_debug, image_bytes)
        
        # จัดรูปแบบพิมพ์รายการข้อความดิบที่ AI แกะได้ออกมาก่อน
        debug_output = "\n".join([f"`{line}`" for line in lines_scanned[:10]]) # ดึงมาโชว์ 10 บรรทัดแรก
        
        embed = discord.Embed(
            title="🔍 ผลการตรวจสอบข้อความดิบ (AI Debug Log)",
            color=discord.Color.orange(),
            description=f"**สิ่งที่ Tesseract OCR มองเห็นจากรูปภาพของคุณ:**\n{debug_output}"
        )
        
        if results:
            embed.add_field(
                name="📊 ยอดรวมที่ดักจับสำเร็จในระบบเก่า",
                value=f"คำนวณยอดโอนที่ใช้ไปได้: `{total_spent:,}` Robux",
                inline=False
            )
        else:
            embed.add_field(
                name="⚠️ ผลการดักจับ",
                value="ระบบ Regex ไม่สามารถจับคู่คำได้เนื่องจากตัวหนังสือเพี้ยน",
                inline=False
            )
            
        await interaction.edit_original_response(content=None, embed=embed)
        
    except Exception as e:
        await interaction.edit_original_response(content=f"❌ เกิดข้อผิดพลาดในระบบ: `{str(e)}`")

bot.run(TOKEN)
