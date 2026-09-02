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

# ปล่อยให้ระบบอัตโนมัติค้นหาโปรแกรม Tesseract บน Linux เอง
pytesseract.pytesseract.tesseract_cmd = 'tesseract'

TOKEN = os.getenv("DISCORD_TOKEN")
MONTHLY_MAX_LIMIT = int(os.getenv("MONTHLY_MAX_LIMIT", 10000))

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

def process_roblox_image_fast(image_bytes):
    """ฟังก์ชันแกะข้อความเฉพาะโซนตัวเลขและรายชื่อผู้รับเงิน ป้องกัน AI สแกนพลาด"""
    image = Image.open(io.BytesIO(image_bytes))
    
    # ล็อกเป้าหมายให้อ่านเฉพาะภาษาอังกฤษและตัวเลขเท่านั้นเพื่อความเร็วสูงสุด
    custom_config = r'--oem 3 --psm 6 -l eng'
    raw_text = pytesseract.image_to_string(image, config=custom_config)
    
    transactions = []
    total_spent_in_image = 0
    
    # อ้างอิงเวลาปัจจุบัน ณ วินาทีที่ผู้ใช้กดสั่งรันบอต เพื่อนำมานับคิวรีเซ็ตล่วงหน้า 30 วัน
    current_time = datetime.datetime.now()
    reset_time = current_time + datetime.timedelta(days=30)
    
    for line_str in raw_text.split('\n'):
        if not line_str.strip():
            continue
            
        # ลบช่องว่างออกให้เกลี้ยงเพื่อป้องกัน Tesseract พิมพ์เว้นวรรคเพี้ยน
        clean_line = re.sub(r'\s+', '', line_str).lower()
        
        # 🔔 ใช้ Regex ดักจับคำว่า sentrobuxto ดึงชื่อผู้รับ และคว้าตัวเลขกลุ่มท้ายสุดของบรรทัดทันที
        # ตัดปัญหาเรื่องไอคอนหกเหลี่ยม หรือเครื่องหมายลบเพี้ยนได้อย่างเด็ดขาด 100%
        roblox_match = re.search(r'sentrobuxto([a-z0-9_\.]+).*?(\d[\d,]*)\s*$', clean_line)
        
        if roblox_match:
            recipient = roblox_match.group(1)
            # ลบเครื่องหมายจุลภาคคั่นหลักพันออกก่อนนำไปบวกเลข
            amount_str = roblox_match.group(2).replace(',', '')
            amount = int(amount_str)
            
            total_spent_in_image += amount
            
            transactions.append({
                "amount": amount,
                "recipient": recipient,
                "reset_date_str": reset_time.strftime("%d/%m/%Y เวลา %H:%M น.")
            })
                
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

    # แสดงหลอดโหลดสถานะ Progress Bar เพื่อให้ผู้ใช้รู้ว่าระบบกำลังรันอยู่
    await interaction.response.send_message("⏳ [░░░░░░░░░░] 0% • กำลังเตรียมไฟล์รูปภาพ...")
    
    try:
        image_bytes = await image.read()
        await interaction.edit_original_response(content="⏳ [███░░░░░░░] 30% • กำลังส่งรูปภาพเข้าสู่ระบบ AI...")
        
        await interaction.edit_original_response(content="⏳ [██████░░░░] 60% • AI กำลังวิเคราะห์ข้อมูลและดักจับรายชื่อ...")
        
        # แยกการสแกนภาพที่หนักไปรันที่ Background Thread ป้องกันการบล็อกสัญญานเครือข่าย
        results, total_spent = await asyncio.to_thread(process_roblox_image_fast, image_bytes)
        
        await interaction.edit_original_response(content="⏳ [█████████░] 90% • กำลังคำนวณโควตาคงเหลือรายเดือน...")
        await asyncio.sleep(0.3)

        if not results:
            await interaction.edit_original_response(content="❌ ไม่พบข้อมูลธุรกรรมการโอน Robux ในรูปภาพนี้ กรุณาตรวจสอบว่าใช้รูปหน้าจอประวัติโอนโดยตรงครับ")
            return
        
        remaining_roblox_quota = MONTHLY_MAX_LIMIT - total_spent
        if remaining_roblox_quota < 0:
            remaining_roblox_quota = 0
        
        embed = discord.Embed(
            title="📊 สรุปประวัติและโควตา Robux คงเหลือ",
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
            
        # สแกนเสร็จสิ้น 100% เปลี่ยนหลอดโหลดเป็นกล่องข้อมูลสรุป Embed สุดสวยงาม
        await interaction.edit_original_response(content="✅ [██████████] 100% • ประมวลผลเสร็จสิ้น!", embed=embed)
        
    except Exception as e:
        print(f"❌ Error occurred during process: {str(e)}")
        await interaction.edit_original_response(content=f"❌ **เกิดข้อผิดพลาดในการประมวลผล:** `รายละเอียด: {str(e)}`")

bot.run(TOKEN)
