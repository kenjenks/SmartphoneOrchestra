# generate_timeline.py - Creates the visual_timeline.json file
#
# 1) Run generate_lighting.py first to create lsq files
#    with .txt extensions.
# 2) Upload those files to Wix
# 3) Copy the Wix URL for each file into movement_manifest.json
# 4) Then run this program

import json

def generate_timeline_only(manifest_path='movement_manifest.json', config_path='instrument_config.json'):
    try:
        # 1. Load the Manifest (This now contains your confirmed Wix .txt URLs)
        with open(manifest_path, 'r') as f:
            manifest = json.load(f)

        # 2. Load the Config (To get the start time and section list)
        with open(config_path, 'r') as f:
            config = json.load(f)

        sections = config.get("sections", {})
        start_time_base = config.get("start_time_base", "19:30:00")
        movement_title = manifest.get("movement_title", "Unknown Movement")

        # We need both maps from the manifest
        audio_map = manifest.get("audio_assets", {})
        lsq_map = manifest.get("lsq_assets", {})  # New expected key in manifest

        master_assets = []

        # 3. Build Asset List
        # We iterate through the sections defined in your config
        for section_key in sections:
            # Check if we have BOTH an MP3 and an LSQ (.txt) URL for this instrument
            audio_url = audio_map.get(section_key)
            lsq_url = lsq_map.get(section_key)

            if audio_url and lsq_url:
                master_assets.append({
                    "id": section_key,
                    "lsq_url": lsq_url,
                    "audio_url": audio_url
                })
            else:
                print(f"Skipping {section_key}: Missing audio or lsq URL in manifest.")

        # 4. Generate the Final Timeline
        timeline = [{
            "time": start_time_base,
            "type": "MASTER_MEDIA_SYNC",
            "label": movement_title,
            "assets": master_assets
        }]

        with open('visual_timeline.json', 'w') as f:
            json.dump(timeline, f, indent=2)

        print(f"Successfully generated visual_timeline.json for: {movement_title}")
        print(f"Linked {len(master_assets)} instrument assets.")

    except Exception as e:
        print(f"Error generating timeline: {e}")


if __name__ == "__main__":
    generate_timeline_only()