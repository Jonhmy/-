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
    """ฟังก์ชันสกัดเฉพาะวันเวลาและคำนวณ +30 วัน"""
    # ค้นหารูปแบบ MM/DD/YY HH:MM AM/PM
    match = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})\s*(\d{1,2}:\d{2}\s*(?:AM|PM)?)', date_str_raw, re.IGNORECASE)
    if not match:
        # ดักจับกรณี OCR อ่านเวลาไม่ติด (ใช้แค่วันที่)
        match_date_only = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', date_str_raw)
        if match_date_only:
            d_part = match_date_only.group(1)
            t_part = "00:00 AM"
        else:
            return "ไม่สามารถระบุวันเวลาจากรูปได้"
    else:
        d_part = match.group(1)
        t_part = match.group(2).strip()

    try:
        full_str = f"{d_part} {t_part}"
        year_len = len(d_part.split('/')[-1])
        
        if "AM" in t_part.upper() or "PM" in t_part.upper():
            fmt = "%m/%d/%y %I:%M %p" if year_len == 2 else "%m/%d/%Y %I:%M %p"
        else:
            fmt = "%m/%d/%y %H:%M" if year_len == 2 else "%m/%d/%Y %H:%M"

        txn_date = datetime.datetime.strptime(full_str, fmt)
        # บวก 30 วันคืนโควตา
        reset_date = txn_date + datetime.timedelta(days=30)
        return reset_date.strftime("%d/%m/%Y เวลา %H:%M น.")
    except Exception:
        return "ไม่สามารถระบุวันเวลาจากรูปได้"

def process_roblox_image_fast(image_bytes):
    image = Image.open(io.BytesIO(image_bytes)).convert('RGB')
    width, height = image.size

    # 🔔 ตัดรูปภาพแบ่งตามช่องที่ขีดเส้นแดงไว้
    # ช่องที่ 1: วันเวลา (ความกว้าง 0% ถึง 18%)
    crop_col1 = image.crop((0, 0, int(width * 0.18), height))
    
    # ช่องที่ 3 และ 4: รายการโอน + ยอดเงิน (ความกว้าง 32% ถึง 100%)
    crop_col3_4 = image.crop((int(width * 0.32), 0, width, height))

    # อ่าน OCR แยกก้อนกันชัดเจน
    config_col1 = r'--oem 3 --psm 6 -l eng'
    config_col3_4 = r'--oem 3 --psm 6 -l eng+jpn'

    text_col1 = pytesseract.image_to_string(crop_col1, config=config_col1)
    text_col3_4 = pytesseract.image_to_string(crop_col3_4, config=config_col3_4)

    # แยกข้อความออกเป็นรายบรรทัด
    lines_col1 = [l.strip() for l in text_col1.split('\n') if l.strip()]
    lines_col3_4 = [l.strip() for l in text_col3_4.split('\n') if l.strip()]

    transactions = []
    total_spent_in_image = 0
    idx_col1 = 0

    # วนลูปประมวลผลช่อง 3-4 เป็นหลัก
    for line in lines_col3_4:
        # ข้ามรายการรับเงิน หรือ โอนไม่สำเร็จ
        if any(k in line.lower() for k in ['received', 'unable to send']):
            if idx_col1 < len(lines_col1):
                idx_col1 += 1
            continue

        # ดักจับรายการโอนออก "Sent Robux to..."
        match = re.search(r'Sent\s+Robux\s+to\s+(.+?)\s*-\s*.*?(\d[\d,]*)', line, re.IGNORECASE)
        if match:
            recipient = match.group(1).strip()
            recipient = re.sub(r'[\.\-\s]+$', '', recipient)  # ตัดจุดขีดตกค้างท้ายชื่อ
            amount = int(match.group(2).replace(',', ''))

            # ดึงวันเวลาจาก ช่อง 1 ตามบรรทัดที่ตรงกัน
            reset_date_str = "ไม่สามารถระบุวันเวลาจากรูปได้"
            if idx_col1 < len(lines_col1):
                reset_date_str = parse_datetime(lines_col1[idx_col1])
                idx_col1 += 1

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
