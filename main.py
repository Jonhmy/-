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

def process_roblox_image_fast(image_bytes):
    image = Image.open(io.BytesIO(image_bytes))
    width, height = image.size
    
    # 1. ครอบตัดเฉพาะพื้นที่ฝั่งขวา (ประมาณ 20% ถึง 100% ของความกว้าง)
    # เพื่อตัดคอลัมน์ชื่อผู้โอน (KORN2_FF) ด้านซ้ายออก ป้องกัน OCR อ่านบรรทัดสลับกัน
    crop_box = (int(width * 0.20), 0, width, height)
    cropped_image = image.crop(crop_box)
    
    # 2. อ่านข้อความโดยใช้ภาษาอังกฤษและภาษาญี่ปุ่นร่วมกัน
    custom_config = r'--oem 3 --psm 6 -l eng+jpn'
    raw_text = pytesseract.image_to_string(cropped_image, config=custom_config)
    
    transactions = []
    total_spent_in_image = 0
    
    for line_str in raw_text.split('\n'):
        line = line_str.strip()
        if not line:
            continue
            
        # กรองรายการที่ไม่ใช่การโอนออกทิ้ง
        if any(k in line.lower() for k in ['received', 'unable to send']):
            continue

        # 3. Regex ดักจับ: 
        # Group 1: วันที่และเวลาจริงจากสลิป (เช่น 08/30/26 9:10 PM)
        # Group 2: ชื่อผู้รับ (ข้อความหลัง Sent Robux to)
        # Group 3: ยอด Robux ท้ายบรรทัด
        pattern = r'(\d{2}/\d{2}/\d{2}\s+\d{1,2}:\d{2}\s*(?:AM|PM)?).*?Sent\s+Robux\s+to\s+(.+?)\s*-\s*.*?(\d[\d,]*)'
        match = re.search(pattern, line, re.IGNORECASE)
        
        if match:
            date_str = match.group(1).strip()
            recipient = match.group(2).strip()
            # ตัดอักขระขยะท้ายชื่อออก เช่น จุด หรือ ขีด
            recipient = re.sub(r'[\.\-\s]+$', '', recipient)
            
            amount_str = match.group(3).replace(',', '')
            amount = int(amount_str)
            
            # คำนวณวันรีเซ็ตโควตาอิงตามวันเวลาจริงในสลิป + 30 วัน
            try:
                # แปลง Text วันเวลาจากสลิปเป็น Obj (รองรับทั้งแบบมี AM/PM และไม่มี)
                if "AM" in date_str.upper() or "PM" in date_str.upper():
                    txn_date = datetime.datetime.strptime(date_str, "%m/%d/%y %I:%M %p")
                else:
                    txn_date = datetime.datetime.strptime(date_str, "%m/%d/%y %H:%M")
                reset_date = txn_date + datetime.timedelta(days=30)
                reset_date_str = reset_date.strftime("%d/%m/%Y เวลา %H:%M น.")
            except Exception:
                # กรณีอ่านวันเวลาเพี้ยน ให้ใช้วันปัจจุบัน + 30 วันสำรองไว้
                reset_date_str = (datetime.datetime.now() + datetime.timedelta(days=30)).strftime("%d/%m/%Y เวลา %H:%M น.")
            
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
                        f"**📅 ลำดับคิวรวมรอรีเซ็ตคืนโควตา (+30 วันจากสลิป)**\n\n"
                        f"{full_queue_text}"
        )
            
        embed.set_footer(text="คำนวณและมัดรวมข้อมูลผ่านระบบอัปเดตความเสถียร 100%")
        await ctx.send(embed=embed)
        await processing_msg.delete()
        
    except Exception as e:
        print(f"❌ Error occurred during process: {str(e)}")
        await processing_msg.edit(content=f"❌ **เกิดข้อผิดพลาดในการประมวลผล:** `{str(e)}`")

bot.run(TOKEN)
