# generate_stickers.py - Crew generates stickers containing QR codes for each seat in the orchestra section.
# The QR code includes XY coordinates if they exist in the local TSV to reduce runtime network requests.

import qrcode
import csv
import os
from PIL import Image, ImageDraw, ImageFont

def generate_stickers_from_tsv(tsv_path='seat_config.tsv', output_dir='stickers'):
    # The base URL of your deployed web app
    BASE_URL = "https://smartphoneorchestra-dwt.web.app/devicedist.html"

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Open the local seat configuration file
    if not os.path.exists(tsv_path):
        print(f"Error: {tsv_path} not found.")
        return

    with open(tsv_path, 'r', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        for row in reader:
            seat_id = row['seat_id']
            # Get X and Y values from the TSV if they are present
            x_val = row.get('x', '').strip()
            y_val = row.get('y', '').strip()

            # Construct the URL. Start with the mandatory seat ID
            qr_data = f"{BASE_URL}?id={seat_id}"

            # If X and Y are present, append them to the URL to reduce network traffic
            if x_val and y_val:
                qr_data += f"&x={x_val}&y={y_val}"
                label_xy = f"X: {x_val} Y: {y_val}"
            else:
                label_xy = "XY: PENDING SURVEY"

            print(f"Generating QR for {seat_id}: {qr_data}")

            qr = qrcode.QRCode(version=1, box_size=10, border=4)
            qr.add_data(qr_data)
            qr.make(fit=True)

            img = qr.make_image(fill_color="black", back_color="white").convert('RGB')
            draw = ImageDraw.Draw(img)

            # Label the sticker so crew knows where to stick it physically
            try:
                font = ImageFont.truetype("arial.ttf", 24)
            except:
                font = ImageFont.load_default()

            # Add text labels to the sticker image
            draw.text((20, 10), f"SEAT: {seat_id}", fill="black", font=font)
            draw.text((20, 40), label_xy, fill="black", font=font)

            img.save(f"{output_dir}/{seat_id}.png")

if __name__ == "__main__":
    generate_stickers_from_tsv()