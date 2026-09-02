import os
import re
import datetime
import discord
from discord.ext import commands
from PIL import Image
import io
import pytesseract
import asyncio
from dotenv import load_dotenv

load_dotenv()

pytesseract.pytesseract.tesseract_cmd = 'tesseract'

TOKEN = os.getenv("DISCORD_TOKEN")
MONTHLY_MAX_LIMIT = int(os.getenv("MONTHLY_MAX_LIMIT", 10000))

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

def parse_datetime(date_str_raw):
    """ฟังก์ชันช่วยแปลงข้อความวันเวลาเป็น Datetime และบวก 30 วัน"""
    match = re.search(r'(\d{2}/\d{2}/\d{2})\s*(\d{1,2}:\d{2}\s*(?:AM|PM)?)', date_str_raw, re.IGNORECASE)
    if not match:
        return None
    
    d_part = match.group(1)
    t_part = match.group(2).strip()
    full_str = f"{d_part} {t_part}"
    
    try:
        if "AM" in t_part.upper() or "PM" in t_part.upper():
            txn_date = datetime.datetime.strptime(full_str, "%m/%d/%y %I:%M %p")
        else:
            txn_date = datetime.datetime.strptime(full_str, "%m/%d/%y %H:%M")
        
        reset_date = txn_date + datetime.timedelta(days=30)
        return reset_date.strftime("%d/%m/%Y เวลา %H:%M น.")
    except Exception:
        return None

def process_roblox_image_fast(image_bytes):
    image = Image.open(io.BytesIO(image_bytes)).convert('RGB')
    width, height = image.size

    # ครอบตัดภาพตามเปอร์เซ็นต์ความกว้าง (ตัดคอลัมน์ผู้ส่งตรงกลางออก)
    # ฝั่งซ้าย: โซน Timestamp (0% - 30%)
    left_box = (0, 0, int(width * 0.30), height)
    # ฝั่งขวา: โซน รายการโอน + ยอดเงิน (35% - 100%)
    right_box = (int(width * 0.35), 0, width, height)

    crop_left = image.crop(left_box)
    crop_right = image.crop(right_box)

    # อ่าน OCR แบบแยกฝั่งอย่างชัดเจน
    config_eng = r'--oem 3 --psm 6 -l eng'
    config_multi = r'--oem 3 --psm 6 -l eng+jpn'

    left_raw = pytesseract.image_to_string(crop_left, config=config_eng)
    right_raw = pytesseract.image_to_string(crop_right, config=config_multi)

    left_lines = [l.strip() for l in left_raw.split('\n') if l.strip()]
    right_lines = [l.strip() for l in right_raw.split('\n') if l.strip()]

    transactions = []
    total_spent_in_image = 0
    left_ptr = 0

    for right_line in right_lines:
        # กรองรายการที่ไม่ใช่โอนออก
        if any(k in right_line.lower() for k in ['received', 'unable to send']):
            if left_ptr < len(left_lines):
                left_ptr += 1
            continue

        # ค้นหารายการโอนออก
        match = re.search(r'Sent\s+Robux\s+to\s+(.+?)\s*-\s*.*?(\d[\d,]*)', right_line, re.IGNORECASE)
        if match:
            recipient = match.group(1).strip()
            recipient = re.sub(r'[\.\-\s]+$', '', recipient) # ตัดจุดขีดตกค้าง
            
            amount = int(match.group(2).replace(',', ''))
            
            # จับคู่วันเวลาจากบรรทัดฝั่งซ้ายที่ตรงกัน
            reset_date_str = "ไม่สามารถระบุวันเวลาจากรูปได้"
            if left_ptr < len(left_lines):
                parsed = parse_datetime(left_lines[left_ptr])
                if parsed:
                    reset_date_str = parsed
                left_ptr += 1

            total_spent_in_image += amount
            transactions.append({
                "amount": amount,
                "recipient": recipient if recipient else "ไม่ระบุชื่อ",
                "reset_date_str": reset_date_str
            })

    return transactions, total_spent_in_image

@bot.event
async def on_ready():
    print(f'🤖 บอตเปิดออนไลน์สำเร็จในชื่อ: {bot.user.name}')

@bot.command(name="check")
async def check_multiple_images(ctx):
    if not ctx.message.attachments:
        await ctx.send("⚠️ กรุณาพิมพ์ `!check` พร้อมแนบรูปภาพประวัติการโอน")
        return

    valid_images = [att for att in ctx.message.attachments if any(att.filename.lower().endswith(ext) for ext in ['png', 'jpg', 'jpeg', 'webp'])]
    
    if not valid_images:
        await ctx.send("❌ ไฟล์ที่แนบมาทั้งหมดไม่ใช่รูปภาพที่รองรับ")
        return

    processing_msg = await ctx.send(f"⏳ [░░░░░░░░░░] 0% • ตรวจพบรูปภาพทั้งหมด {len(valid_images)} รูป...")
    
    try:
        all_results = []
        grand_total_spent = 0
        
        for idx, img_att in enumerate(valid_images, start=1):
            progress_percent = int((idx / len(valid_images)) * 80)
            await processing_msg.edit(content=f"⏳ [██████░░░░] {progress_percent}% • AI กำลังอ่านข้อความรูปที่ {idx}/{len(valid_images)}...")
            
            image_bytes = await img_att.read()
            results, total_spent = await asyncio.to_thread(process_roblox_image_fast, image_bytes)
            
            all_results.extend(results)
            grand_total_spent += total_spent
            
        await processing_msg.edit(content="⏳ [█████████░] 90% • กำลังสรุปยอดรวม...")
        await asyncio.sleep(0.2)

        if not all_results:
            await processing_msg.edit(content="❌ สแกนเสร็จสิ้น แต่ไม่พบข้อมูลรายการโอน Robux")
            return
        
        remaining_roblox_quota = max(0, MONTHLY_MAX_LIMIT - grand_total_spent)
        
        queue_text_list = []
        for queue_idx, tx in enumerate(all_results, start=1):
            queue_text_list.append(
                f"**{queue_idx}.** 💰 โอนออก `-{tx['amount']:,}` Robux\n"
                f"└ ผู้รับ: `{tx['recipient']}` | คืนโควตา: `{tx['reset_date_str']}`"
            )
        
        full_queue_text = "\n\n".join(queue_text_list)
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
                        f"**📅 ลำดับคิวรวมรอรีเซ็ตคืนโควตา (+30 วันอิงตามเวลาในสลิปจริง)**\n\n"
                        f"{full_queue_text}"
        )
            
        embed.set_footer(text="คำนวณและมัดรวมข้อมูลผ่านระบบอัปเดตความเสถียร 100%")
        await ctx.send(embed=embed)
        await processing_msg.delete()
        
    except Exception as e:
        print(f"❌ Error occurred during process: {str(e)}")
        await processing_msg.edit(content=f"❌ **เกิดข้อผิดพลาดในการประมวลผล:** `{str(e)}`")

bot.run(TOKEN)
