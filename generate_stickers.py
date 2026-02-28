import qrcode
import csv
import os
from PIL import Image, ImageDraw, ImageFont

def generate_stickers_from_tsv(tsv_path='seat_config.tsv', output_dir='stickers'):
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    with open(tsv_path, 'r', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            seat_id = row['seat_id']
            # Encoding the ID for the webapp to parse
            data = f"SEAT_ID:{seat_id}"
            
            qr = qrcode.QRCode(version=1, box_size=10, border=4)
            qr.add_data(data)
            qr.make(fit=True)
            
            img = qr.make_image(fill_color="black", back_color="white").convert('RGB')
            draw = ImageDraw.Draw(img)
            
            # Label the sticker so the crew knows where it goes
            try:
                # Adjust path if on Windows/Mac (e.g., "arial.ttf" or "Arial.ttf")
                font = ImageFont.truetype("arial.ttf", 24)
            except:
                font = ImageFont.load_default()
            
            draw.text((20, 10), f"Seat: {seat_id}", fill="black", font=font)
            
            filename = f"qr_{seat_id}.png"
            img.save(os.path.join(output_dir, filename))
            print(f"Generated sticker: {filename}")

if __name__ == "__main__":
    generate_stickers_from_tsv()