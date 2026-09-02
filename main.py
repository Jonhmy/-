import os
import re
import cv2
import datetime
import numpy as np
import discord
from discord.ext import commands
from PIL import Image
import io
import easyocr
import asyncio
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
MONTHLY_MAX_LIMIT = int(os.getenv("MONTHLY_MAX_LIMIT", 10000))

# โหลด EasyOCR รองรับทั้งภาษาอังกฤษและภาษาญี่ปุ่น
reader = easyocr.Reader(['en', 'ja'], gpu=False)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

def process_roblox_image_fast(image_bytes):
    # 1. แปลงไฟล์ภาพเป็น OpenCV Format
    file_bytes = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(file_bytes, cv2.IMREAD_COLOR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # 2. อ่านข้อความและตำแหน่งพิกัดทั้งหมดในภาพด้วย EasyOCR
    # detail=1 จะคืนค่า [bounding_box, text, confidence]
    ocr_results = reader.readtext(gray, detail=1)
    
    # Sort รายการตามแนวตั้ง (Y-axis) จากบนลงล่าง
    ocr_results.sort(key=lambda x: x[0][0][1])

    # 3. จัดกลุ่มข้อความที่อยู่ในบรรทัด (แถว) เดียวกันตามพิกัด Y
    rows = []
    threshold_y = 18  # ระยะพิกเซลที่ถือว่าเป็นบรรทัดเดียวกัน

    for box, text, prob in ocr_results:
        y_center = (box[0][1] + box[2][1]) / 2
        matched = False
        
        for row in rows:
            if abs(row['y_center'] - y_center) < threshold_y:
                row['items'].append((box[0][0], text)) # เก็บพิกัด X และข้อความ
                matched = True
                break
                
        if not matched:
            rows.append({
                'y_center': y_center,
                'items': [(box[0][0], text)]
            })

    transactions = []
    total_spent_in_image = 0

    # 4. ประมวลผลแต่ละแถวที่จัดกลุ่มเรียบร้อยแล้ว
    for row in rows:
        # เรียงข้อความในแถวเดียวกันจากซ้ายไปขวาตามพิกัด X
        row['items'].sort(key=lambda x: x[0])
        full_line_str = " ".join([item[1] for item in row['items']])

        # กรองรายการที่ไม่ใช่การโอนออกทิ้ง
        if any(k in full_line_str.lower() for k in ['received', 'unable to send']):
            continue

        # ตรวจจับโครงสร้างการโอนออก
        # 4.1 หายอด Robux ท้ายบรรทัด (เช่น - 50 หรือ - 1,000)
        amount_match = re.search(r'-\s*.*?(\d[\d,]*)', full_line_str)
        # 4.2 หาชื่อผู้รับเงิน (ข้อความหลัง Sent Robux to)
        recipient_match = re.search(r'Sent\s+Robux\s+to\s+(.+?)(?=\s*-\s*|\s*$)', full_line_str, re.IGNORECASE)
        # 4.3 หาวันเวลาซีกซ้ายสุด (เช่น 08/30/26 9:10 PM)
        date_match = re.search(r'(\d{2}/\d{2}/\d{2})\s*(\d{1,2}:\d{2}\s*(?:AM|PM)?)', full_line_str, re.IGNORECASE)

        if amount_match and recipient_match:
            amount = int(amount_match.group(1).replace(',', ''))
            
            recipient = recipient_match.group(1).strip()
            recipient = re.sub(r'[\.\-\s]+$', '', recipient) # ตัดสัญลักษณ์ตกค้าง

            reset_date_str = "ไม่สามารถระบุวันเวลาจากรูปได้"
            if date_match:
                d_part = date_match.group(1)
                t_part = date_match.group(2).strip()
                datetime_str = f"{d_part} {t_part}"
                
                try:
                    if "AM" in t_part.upper() or "PM" in t_part.upper():
                        txn_date = datetime.datetime.strptime(datetime_str, "%m/%d/%y %I:%M %p")
                    else:
                        txn_date = datetime.datetime.strptime(datetime_str, "%m/%d/%y %H:%M")
                    
                    # คำนวณวันคืนโควตา +30 วันจาก Timestamp ในรูป
                    reset_date = txn_date + datetime.timedelta(days=30)
                    reset_date_str = reset_date.strftime("%d/%m/%Y เวลา %H:%M น.")
                except Exception:
                    pass

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
    try:
        synced = await bot.tree.sync()
        print(f"🔄 ซิงค์ Slash Commands สำเร็จแล้ว จำนวน {len(synced)} คำสั่ง")
    except Exception as e:
        print(f"❌ เกิดข้อผิดพลาดในการซิงค์คำสั่ง: {e}")

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
        await asyncio.sleep(0.3)

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
