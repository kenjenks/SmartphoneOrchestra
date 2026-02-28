# generate_stickers.py - Helps crew generate stickers containing QR codes for each seat in the orchestra section.

import qrcode
import csv
import os
from PIL import Image, ImageDraw, ImageFont

def generate_stickers_from_tsv(tsv_path='seat_config.tsv', output_dir='stickers'):
    # The base URL of your deployed web app
    BASE_URL = "https://smartphoneorchestra-dwt.web.app/devicedist.html"

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    with open(tsv_path, 'r', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            seat_id = row['seat_id']
            x = row['x']
            y = row['y']

            # Construct the URL with query parameters
            # Format: https://.../devicedist.html?id=A1&x=5.5&y=10.2
            qr_data = f"{BASE_URL}?id={seat_id}&x={x}&y={y}"

            print(f"Generating QR for {seat_id}: {qr_data}")

            qr = qrcode.QRCode(version=1, box_size=10, border=4)
            qr.add_data(qr_data)
            qr.make(fit=True)

            img = qr.make_image(fill_color="black", back_color="white").convert('RGB')
            draw = ImageDraw.Draw(img)

            # Label the sticker so crew knows where to stick it
            try:
                # Common paths for fonts
                font = ImageFont.truetype("arial.ttf", 24)
            except:
                font = ImageFont.load_default()

            # Add text labels to the sticker image for the physical crew
            draw.text((20, 10), f"SEAT: {seat_id}", fill="black", font=font)
            draw.text((20, 40), f"X: {x} Y: {y}", fill="black", font=font)

            img.save(f"{output_dir}/{seat_id}.png")


if __name__ == "__main__":
    generate_stickers_from_tsv()