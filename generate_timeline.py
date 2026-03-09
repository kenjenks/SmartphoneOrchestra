# generate_timeline.py
#
# Creates the multi-movement visual_timeline.json file.
# Supports overlapping movements, designer-defined buffers
# (float seconds), different instrument/light configurations
# for each movement, and planned real-world start times.
#
# 1) Run generate_lighting.py first to create lsq files
#    with .txt extensions.
# 2) Upload those files to Wix
# 3) Copy the Wix URL for each file into movement_manifest_*.json
#    file for that movement.
# 4) Create show_manifest.json, linking to each
#    movement_manifest_*.json file
# 5) Then run this program

import json
import os


def generate_multi_movement_timeline(manifest_path='show_manifest.json', default_inst_path='instrument_config.json'):
    try:
        if not os.path.exists(manifest_path):
            print(f"Error: {manifest_path} not found.")
            return

        with open(manifest_path, 'r') as f:
            show_manifest = json.load(f)

        final_timeline = {
            "show_title": show_manifest.get("show_title", "Unnamed Production"),
            "movements": []
        }

        for mov_entry in show_manifest.get("movements", []):
            m_file = mov_entry.get("manifest_file")
            # USE MOVEMENT-SPECIFIC CONFIG IF PROVIDED, ELSE USE DEFAULT
            inst_path = mov_entry.get("instrument_config", default_inst_path)

            if not os.path.exists(m_file) or not os.path.exists(inst_path):
                print(f"Skipping {m_file}: Missing manifest or config ({inst_path})")
                continue

            with open(m_file, 'r') as f:
                m_data = json.load(f)

            with open(inst_path, 'r') as f:
                inst_config = json.load(f)

            sections = inst_config.get("sections", [])
            audio_map = m_data.get("audio_assets", {})
            lsq_map = m_data.get("visual_assets", {})

            movement_assets = []

            for section_key in sections:
                audio_url = audio_map.get(section_key)
                lsq_url = lsq_map.get(section_key)

                if audio_url and lsq_url:
                    # The assets now inherit the properties (like color)
                    # from the movement's specific instrument_config
                    movement_assets.append({
                        "id": section_key,
                        "lsq_url": lsq_url,
                        "audio_url": audio_url
                    })

            movement_obj = {
                "movement_id": mov_entry.get("movement_id"),
                "movement_name": mov_entry.get("movement_name"),
                "planned_start": mov_entry.get("planned_start"),
                "buffer_s": float(mov_entry.get("buffer_s", 1.0)),
                "cues": [
                    {
                        "time": 0.0,
                        "label": m_data.get("movement_title", "Start"),
                        "type": "MASTER_MEDIA_SYNC",
                        "assets": movement_assets
                    }
                ]
            }

            final_timeline["movements"].append(movement_obj)
            print(f"Added Movement: {mov_entry['movement_name']} using {inst_path}")

        with open('visual_timeline.json', 'w') as f:
            json.dump(final_timeline, f, indent=2)

        print("\nSUCCESS: Multi-config timeline generated.")

    except Exception as e:
        print(f"CRITICAL ERROR: {str(e)}")


if __name__ == "__main__":
    generate_multi_movement_timeline()