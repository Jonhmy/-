import os
import re
import datetime
import discord
from discord.ext import commands
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
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

def parse_date_and_add_30_days(text):
    """สกัดวันที่ MM/DD/YY หรือ MM/DD/YYYY แล้ว +30 วัน"""
    date_match = re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', text)
    if not date_match:
        return "ไม่สามารถระบุวันเวลาจากรูปได้"
    
    d_str = date_match.group(1)
    parts = d_str.split('/')
    
    # ดึงส่วนเวลา (ถ้ามี)
    time_match = re.search(r'(\d{1,2}:\d{2}\s*(?:AM|PM)?)', text, re.IGNORECASE)
    t_str = time_match.group(1).strip() if time_match else "12:00 PM"
    
    try:
        year = int(parts[2])
        if year < 100:
            year += 2000
        month = int(parts[0])
        day = int(parts[1])
        
        # จัดรูปแบบเวลา
        if "AM" in t_str.upper() or "PM" in t_str.upper():
            time_parts = re.split(r'[:\s]', t_str)
            hour = int(time_parts[0])
            minute = int(time_parts[1])
            if "PM" in t_str.upper() and hour < 12:
                hour += 12
            elif "AM" in t_str.upper() and hour == 12:
                hour = 0
        else:
            time_parts = t_str.split(':')
            hour = int(time_parts[0])
            minute = int(time_parts[1])

        txn_date = datetime.datetime(year, month, day, hour, minute)
        reset_date = txn_date + datetime.timedelta(days=30)
        return reset_date.strftime("%d/%m/%Y เวลา %H:%M น.")
    except Exception:
        return "ไม่สามารถระบุวันเวลาจากรูปได้"

def preprocess_image_for_ocr(image):
    """
    ปรับแต่งรูปก่อนส่งเข้า OCR เพื่อเพิ่มความแม่นยำในการอ่านตัวเลข/ตัวหนังสือ
    - แปลงเป็นขาวดำ
    - ขยายขนาดรูป (ถ้ารูปเล็ก) เพราะ Tesseract อ่านตัวหนังสือเล็กๆ ได้แย่
    - เพิ่ม contrast และ sharpen ให้ขอบตัวอักษรคมขึ้น
    """
    image = image.convert('L')  # grayscale

    # ขยายรูปถ้าด้านที่สั้นกว่ามีขนาดเล็กกว่า ~1500px (ช่วยเรื่องสกรีนช็อตความละเอียดต่ำ/ถูกบีบอัด)
    width, height = image.size
    target_min_dim = 1500
    scale = max(1.0, target_min_dim / min(width, height))
    if scale > 1.0:
        image = image.resize((int(width * scale), int(height * scale)), Image.LANCZOS)

    image = ImageOps.autocontrast(image, cutoff=1)
    image = ImageEnhance.Contrast(image).enhance(1.6)
    image = ImageEnhance.Sharpness(image).enhance(2.0)

    return image


def fix_common_ocr_digit_errors(number_text):
    """แก้ตัวอักษรที่ OCR มักอ่านสลับกับตัวเลขในสตริงตัวเลข เช่น O->0, l/I->1, S->5, B->8"""
    replacements = {'O': '0', 'o': '0', 'l': '1', 'I': '1', 'S': '5', 's': '5', 'B': '8'}
    for wrong, right in replacements.items():
        number_text = number_text.replace(wrong, right)
    return number_text


def process_roblox_image_accurate(image_bytes, min_confidence=40):
    raw_image = Image.open(io.BytesIO(image_bytes)).convert('RGB')
    image = preprocess_image_for_ocr(raw_image)

    # อ่าน OCR แบบดึงตำแหน่ง bounding box (TSV Format) เพื่อจับคู่ตาม Y-position
    # psm 6 = มองรูปเป็นบล็อกข้อความเรียงบรรทัด เหมาะกับรายการธุรกรรมแบบนี้
    # ใช้ lang='eng' อย่างเดียวเพราะข้อความในสลิปเป็นภาษาอังกฤษ การใส่ jpn เข้ามาด้วยทำให้ OCR สับสนและอ่านผิดบ่อยขึ้น
    custom_config = r'--oem 3 --psm 6'
    tsv_data = pytesseract.image_to_data(
        image, lang='eng', config=custom_config, output_type=pytesseract.Output.DICT
    )

    n_boxes = len(tsv_data['text'])
    words = []

    for i in range(n_boxes):
        text = tsv_data['text'][i].strip()
        try:
            conf = float(tsv_data['conf'][i])
        except (ValueError, TypeError):
            conf = -1
        # ตัดคำที่ OCR มั่นใจต่ำมากทิ้ง (ลดโอกาสอ่านตัวอักษรมั่วมาปนในบรรทัด)
        if text and conf >= min_confidence:
            words.append({
                'text': text,
                'top': tsv_data['top'][i],
                'left': tsv_data['left'][i],
                'height': tsv_data['height'][i]
            })

    # จัดกลุ่มคำให้อยู่ในแถวเดียวกัน โดยใช้ tolerance ที่ปรับตามความสูงตัวหนังสือจริง
    # (แทนค่าคงที่ 35px ซึ่งใช้ไม่ได้กับสกรีนช็อตความละเอียด/ขนาดตัวอักษรต่างกัน)
    rows = []
    words_sorted = sorted(words, key=lambda w: w['top'])

    for w in words_sorted:
        matched_row = False
        row_tolerance = max(15, w['height'] * 0.8)
        for row in rows:
            # ถ้าตำแหน่ง top ใกล้เคียงกันถือว่าอยู่แถวเดียวกัน
            if abs(row['avg_top'] - w['top']) < row_tolerance:
                row['words'].append(w)
                row['avg_top'] = sum(item['top'] for item in row['words']) / len(row['words'])
                matched_row = True
                break
        if not matched_row:
            rows.append({'avg_top': w['top'], 'words': [w]})

    # เรียงแถวจากบนลงล่าง
    rows = sorted(rows, key=lambda r: r['avg_top'])

    transactions = []
    total_spent_in_image = 0

    for row in rows:
        # เรียงคำในแถวเดียวกันจากซ้ายไปขวา
        row_words = sorted(row['words'], key=lambda w: w['left'])
        full_line_text = " ".join([w['text'] for w in row_words])

        # กรองเฉพาะแถวที่เป็นรายการโอนเงินออกเท่านั้น (มีคำว่า Sent และ -)
        # ยอมรับ "5ent"/"Sen†" ฯลฯ แบบหลวมขึ้นเล็กน้อยด้วยการเทียบแบบไม่สนตัวพิมพ์ใหญ่เล็ก
        if re.search(r'sent', full_line_text, re.IGNORECASE) and "-" in full_line_text:
            # ดึงยอดเงิน: หาเฉพาะตัวเลขที่อยู่ติดกับ "-" ทันที (กัน false-positive จากเลขวันที่/เวลาในบรรทัดเดียวกัน)
            amount_match = re.search(r'-\s*\$?\s*([A-Za-z0-9,]{2,})', full_line_text)
            amount = None
            if amount_match:
                raw_amount = fix_common_ocr_digit_errors(amount_match.group(1))
                digits_only = re.sub(r'[^\d]', '', raw_amount)
                if digits_only:
                    amount = int(digits_only)

            if amount is not None:
                # ดึงชื่อผู้รับ (ข้อความระหว่าง to และ -)
                recipient_match = re.search(r'to\s+(.+?)\s*-\s*@?', full_line_text, re.IGNORECASE)
                recipient = recipient_match.group(1).strip() if recipient_match else "ไม่ระบุชื่อ"
                recipient = re.sub(r'[\.\-\s]+$', '', recipient)

                # ดึงวันเวลาจากข้อความในแถวเดียวกัน
                reset_date_str = parse_date_and_add_30_days(full_line_text)

                total_spent_in_image += amount
                transactions.append({
                    "amount": amount,
                    "recipient": recipient,
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
            results, total_spent = await asyncio.to_thread(process_roblox_image_accurate, image_bytes)
            
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
            title="📊 สรุปประวัติและโควตาคงเหลือ (ระบบแม่นยำสูง)",
            color=discord.Color.green(),
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
