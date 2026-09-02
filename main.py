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
    image = Image.open(io.BytesIO(image_bytes))
    
    # 🔔 เพิ่ม +jpn เพื่อให้อ่านอักษรภาษาญี่ปุ่นและสัญลักษณ์พิเศษได้
    custom_config = r'--oem 3 --psm 6 -l eng+jpn'
    raw_text = pytesseract.image_to_string(image, config=custom_config)
    
    transactions = []
    total_spent_in_image = 0
    
    current_time = datetime.datetime.now()
    reset_time = current_time + datetime.timedelta(days=30)
    
    for line_str in raw_text.split('\n'):
        line = line_str.strip()
        if not line:
            continue
        
        # กรองรายการรับเงิน หรือรายการที่ส่งไม่สำเร็จออก
        if any(k in line.lower() for k in ['received', 'unable to send']):
            continue

        # 🔔 Regex สแกนหาคำว่า "Sent Robux to" แล้วเก็บชื่อผู้รับ (ก้อนกลาง) และยอดเงิน (ก้อนท้าย)
        match = re.search(r'Sent\s+Robux\s+to\s+(.+?)\s*[\.-]*\s*@?\s*(\d[\d,]*)', line, re.IGNORECASE)
        
        if match:
            recipient = match.group(1).strip()
            # ทำความสะอาดสัญลักษณ์ตกค้างท้ายชื่อ เช่น จุด หรือ ขีด
            recipient = re.sub(r'[\.\-\s]+$', '', recipient)
            
            amount_str = match.group(2).replace(',', '')
            amount = int(amount_str)
            
            total_spent_in_image += amount
            
            transactions.append({
                "amount": amount,
                "recipient": recipient if recipient else "ไม่ระบุชื่อ",
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

# ⭐ คำสั่งแบบพิมพ์ธรรมดา !check (รองรับการโยนส่งหลายรูปพร้อมกันสูงสุด 10 รูป)
@bot.command(name="check")
async def check_multiple_images(ctx):
    # 1. ตรวจสอบว่าผู้ใช้ส่งไฟล์แนบมาด้วยหรือไม่
    if not ctx.message.attachments:
        await ctx.send("⚠️ กรุณาพิมพ์ `!check` พร้อมแนบรูปภาพประวัติการโอน (สามารถเลือกส่งหลายรูปพร้อมกันได้เลยครับ)")
        return

    # กรองนับเฉพาะไฟล์ที่เป็นรูปภาพที่รองรับเท่านั้น
    valid_images = [att for att in ctx.message.attachments if any(att.filename.lower().endswith(ext) for ext in ['png', 'jpg', 'jpeg', 'webp'])]
    
    if not valid_images:
        await ctx.send("❌ ไฟล์ที่แนบมาทั้งหมดไม่ใช่รูปภาพที่รองรับ (กรุณาใช้ png, jpg, jpeg, webp)")
        return

    # ส่งข้อความตั้งต้นเพื่ออัปเดตเปอร์เซ็นต์หลอดโหลด
    processing_msg = await ctx.send(f"⏳ [░░░░░░░░░░] 0% • ตรวจพบรูปภาพทั้งหมด {len(valid_images)} รูป กำลังจัดเตรียมไฟล์...")
    
    try:
        all_results = []
        grand_total_spent = 0
        
        # วนลูปประมวลผลสแกนรูปภาพทีละรูป (รันสะสมคิวรวมกันในทีเดียว)
        for idx, img_att in enumerate(valid_images, start=1):
            progress_percent = int((idx / len(valid_images)) * 80) # คำนวณหลอดโหลดขยับตามจำนวนรูป
            await processing_msg.edit(content=f"⏳ [██████░░░░] {progress_percent}% • AI กำลังอ่านข้อความรูปที่ {idx}/{len(valid_images)}...")
            
            # อ่านข้อมูลภาพย่อย
            image_bytes = await img_att.read()
            # ส่งไปสแกนที่เธรดเบื้องหลัง ป้องกันบอตค้าง
            results, total_spent = await asyncio.to_thread(process_roblox_image_fast, image_bytes)
            
            # รวมยอดสะสมและข้อมูลคิวจากรูปนี้เข้าสู่กองกลาง
            all_results.extend(results)
            grand_total_spent += total_spent
            
        await processing_msg.edit(content="⏳ [█████████░] 90% • กำลังสรุปยอดรวมและจัดเรียงคิว Rolling Window...")
        await asyncio.sleep(0.3)

        if not all_results:
            await processing_msg.edit(content="❌ สแกนเสร็จสิ้น แต่ไม่พบข้อมูลธุรกรรมการโอน Robux ในรูปภาพทั้งหมดที่ส่งมาครับ")
            return
        
        # คำนวณยอดโควตาคงเหลือจากลิมิตรายเดือน
                # --- (ท่อนวนลูปสแกนรูปภาพด้านบนให้คงไว้เหมือนเดิม) ---
        
        # คำนวณยอดโควตาคงเหลือจากลิมิตรายเดือน
        remaining_roblox_quota = MONTHLY_MAX_LIMIT - grand_total_spent
        if remaining_roblox_quota < 0:
            remaining_roblox_quota = 0
        
        # 🔔 [จุดแก้ไขเด็ดขาด] เปลี่ยนจาก Field แยก มารวบรวมข้อความยาวเป็นก้อนเดียว
        queue_text_list = []
        for queue_idx, tx in enumerate(all_results, start=1):
            queue_text_list.append(
                f"**{queue_idx}.** 💰 โอนออก `-{tx['amount']:,}` Robux\n"
                f"└ ผู้รับ: `{tx['recipient']}` | คืนโควตา: `{tx['reset_date_str']}`"
            )
        
        # รวมรายการทั้งหมดคั่นด้วยการเว้นบรรทัดใหม่
        full_queue_text = "\n\n".join(queue_text_list)
        
        # ตรวจสอบความยาวตัวอักษรไม่ให้เกินขีดจำกัด Discord (สูงสุด 4,000 ตัวอักษรใน Description)
        if len(full_queue_text) > 3800:
            full_queue_text = full_queue_text[:3800] + "\n\n... (ข้อมูลรายการหนาแน่นเกินไป ระบบตัดการแสดงผลส่วนท้าย) ..."

        embed = discord.Embed(
            title="📊 สรุปประวัติและโควตาคงเหลือ (ระบบรันหลายรูป)",
            color=discord.Color.purple(),
            description=f"💡 *รวมยอดประมวลผลจากรูปภาพหลักฐานทั้งหมด `{len(valid_images)}` รูปเรียบร้อยแล้ว*\n\n"
                        f"**💡 Status โควตาในปัจจุบันของคุณ**\n"
                        f"• ลิมิตบัญชีของคุณรายเดือน: `{MONTHLY_MAX_LIMIT:,}` Robux\n"
                        f"• ยอดรวมที่ใช้ไปจากทุกรูป: `{grand_total_spent:,}` Robux\n"
                        f"• ➡️ **ตอนนี้คุณยังส่งได้อีก:** **`{remaining_roblox_quota:,}` Robux**\n\n"
                        f"───────────────────\n"
                        f"**📅 ลำดับคิวรวมรอรีเซ็ตคืนโควตา (+30 วัน)**\n\n"
                        f"{full_queue_text}"
        )
            
        embed.set_footer(text="คำนวณและมัดรวมข้อมูลผ่านระบบอัปเดตความเสถียร 100%")
        
        # สแกนเสร็จสมบูรณ์ลบหลอดโหลด และส่งตารางสรุปรวมยอดแสดงผลทันที
        await ctx.send(embed=embed)
        await processing_msg.delete()
        
    except Exception as e:
        print(f"❌ Error occurred during process: {str(e)}")
        await processing_msg.edit(content=f"❌ **เกิดข้อผิดพลาดในการประมวลผลกลุ่มรูปภาพ:** `รายละเอียด: {str(e)}`")
bot.run(TOKEN)
