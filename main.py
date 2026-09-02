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
    image = Image.open(io.BytesIO(image_bytes)).convert('RGB')
    width, height = image.size
    
    # 🔔 1. อ่านรูปเต็มแบบ PSM 6 เพื่อดึงพิกัดและข้อความเรียงตามบรรทัด
    custom_config = r'--oem 3 --psm 6 -l eng+jpn'
    data = pytesseract.image_to_data(image, config=custom_config, output_type=pytesseract.Output.DICT)
    
    # จัดกลุ่มข้อความตามบรรทัด (line_num และ block_num)
    lines_dict = {}
    n_boxes = len(data['text'])
    for i in range(n_boxes):
        text = data['text'][i].strip()
        if not text:
            continue
        line_key = (data['block_num'][i], data['line_num'][i])
        if line_key not in lines_dict:
            lines_dict[line_key] = []
        lines_dict[line_key].append(text)

    transactions = []
    total_spent_in_image = 0
    current_date_str = None

    # 🔔 2. วนลูปอ่านทีละบรรทัดเพื่อจับคู่ วันเวลา + รายการโอน
    for line_key, words in lines_dict.items():
        line_str = " ".join(words)
        
        # ข้ามรายการรับเงิน หรือรายการที่ส่งไม่สำเร็จ
        if any(k in line_str.lower() for k in ['received', 'unable to send']):
            continue

        # ดักจับ "วันเวลา" ฝั่งซ้าย (เช่น 08/30/26 9:10 PM หรือ 08/30/26 9:10PM)
        date_match = re.search(r'(\d{2}/\d{2}/\d{2})\s*(\d{1,2}:\d{2}\s*(?:AM|PM)?)', line_str, re.IGNORECASE)
        if date_match:
            d_part = date_match.group(1)
            t_part = date_match.group(2).strip()
            current_date_str = f"{d_part} {t_part}"

        # ดักจับ "รายการโอน" (Sent Robux to ...)
        sent_match = re.search(r'Sent\s+Robux\s+to\s+(.+?)\s*-\s*.*?(\d[\d,]*)', line_str, re.IGNORECASE)
        
        if sent_match:
            recipient = sent_match.group(1).strip()
            recipient = re.sub(r'[\.\-\s]+$', '', recipient) # ตัดจุด/ขีด ท้ายชื่อ
            
            amount = int(sent_match.group(2).replace(',', ''))
            
            # 🔔 3. แปลงวันเวลาจากสลิปภาพ แล้วบวกเพิ่ม 30 วันตรงๆ
            reset_date_str = "ไม่สามารถระบุวันเวลาจากรูปได้"
            if current_date_str:
                try:
                    # แปลงปี 26 เป็น 2026
                    if "AM" in current_date_str.upper() or "PM" in current_date_str.upper():
                        txn_date = datetime.datetime.strptime(current_date_str, "%m/%d/%y %I:%M %p")
                    else:
                        txn_date = datetime.datetime.strptime(current_date_str, "%m/%d/%y %H:%M")
                    
                    # บวกเพิ่ม 30 วันนับจาก Timestamp ในรูป
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
