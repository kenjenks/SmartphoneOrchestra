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

    print(f"Reading seat configuration from {tsv_path}")

    with open(tsv_path, 'r', newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter='\t')

        # Print column headers for debugging
        print(f"CSV Headers: {reader.fieldnames}")

        row_count = 0
        for row in reader:
            row_count += 1

            # Handle different possible column name variations
            seat_id = None
            if 'seat_id' in row:
                seat_id = row['seat_id']
            elif 'id' in row:
                seat_id = row['id']
            elif 'seat' in row:
                seat_id = row['seat']

            if not seat_id:
                print(f"  Skipping row {row_count}: No seat identifier found")
                continue

            # Safely get X and Y values from various possible column names
            x_val = None
            y_val = None

            # Try different column name variations
            for x_col in ['x', 'X', 'x_coord', 'coord_x']:
                if x_col in row and row[x_col] and row[x_col].strip():
                    x_val = row[x_col].strip()
                    break

            for y_col in ['y', 'Y', 'y_coord', 'coord_y']:
                if y_col in row and row[y_col] and row[y_col].strip():
                    y_val = row[y_col].strip()
                    break

            # Construct the URL - FIXED: Use 'seat' parameter, not 'id'
            qr_data = f"{BASE_URL}?seat={seat_id}"

            # Generate label text
            label_xy = ""
            if x_val and y_val:
                try:
                    # Append coordinates to URL to reduce network traffic
                    qr_data += f"&x={x_val}&y={y_val}"
                    label_xy = f"({float(x_val):.1f}, {float(y_val):.1f})"
                except ValueError:
                    # Handle case where x_val or y_val aren't valid floats
                    print(f"  Warning: Invalid coordinates for {seat_id}: x='{x_val}', y='{y_val}'")
                    label_xy = "(invalid coords)"

            print(f"  Generating QR for {seat_id}: {qr_data}")

            # Create QR code
            qr = qrcode.QRCode(
                version=1,
                box_size=10,
                border=4,
                error_correction=qrcode.constants.ERROR_CORRECT_L
            )
            qr.add_data(qr_data)
            qr.make(fit=True)

            # Generate image
            img = qr.make_image(fill_color="black", back_color="white").convert('RGB')
            draw = ImageDraw.Draw(img)

            # Try to load a nicer font, fall back to default if not available
            try:
                # Try different font paths for cross-platform compatibility
                font_paths = [
                    "arial.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Linux
                    "/System/Library/Fonts/Helvetica.ttc",  # macOS
                    "C:\\Windows\\Fonts\\Arial.ttf"  # Windows
                ]

                font = None
                for path in font_paths:
                    if os.path.exists(path):
                        font = ImageFont.truetype(path, 24)
                        break

                if font is None:
                    font = ImageFont.load_default()

                # Small font for coordinate labels
                small_font = ImageFont.truetype(font.path, 16) if font != ImageFont.load_default() else font

            except:
                font = ImageFont.load_default()
                small_font = font

            # Add text labels to the sticker image
            draw.text((20, 10), f"SEAT: {seat_id}", fill="black", font=font)

            if label_xy:
                draw.text((20, 45), label_xy, fill="black", font=small_font)

            # Add a small note about the URL parameter
            draw.text((20, 75), f"URL param: seat={seat_id}", fill="gray", font=small_font)

            # Save the sticker
            output_path = f"{output_dir}/{seat_id}.png"
            img.save(output_path)
            print(f"    -> Saved to {output_path}")

    print(f"\n✅ Generated {row_count} stickers in '{output_dir}' directory")
    print(f"   URL format: {BASE_URL}?seat=SEAT_ID[&x=X&y=Y]")


if __name__ == "__main__":
    generate_stickers_from_tsv()