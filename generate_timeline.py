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
import math


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
            inst_path = mov_entry.get("instrument_config", default_inst_path)

            if not os.path.exists(m_file):
                print(f"Warning: Movement manifest {m_file} not found. Skipping.")
                continue

            with open(m_file, 'r') as f:
                m_data = json.load(f)

            # Load instrument config for this movement (spatial parameters live here)
            inst_config = {}
            if os.path.exists(inst_path):
                with open(inst_path, 'r') as f:
                    inst_config = json.load(f)
                print(f"  Loaded instrument config: {inst_path}")
            else:
                print(f"  Warning: Instrument config {inst_path} not found. Using defaults.")

            sections = inst_config.get("sections", {})
            dynamic_map = inst_config.get("dynamic_map", {})

            movement_assets = []

            # --- PROCESS STANDARD INSTRUMENT ASSETS ---
            audio_map = m_data.get("audio_assets", {})
            visual_map = m_data.get("visual_assets", {})

            for section_key in audio_map.keys():
                audio_url = audio_map.get(section_key)
                lsq_url = visual_map.get(section_key)

                # Get spatial parameters from instrument config
                spatial = sections.get(section_key, {})

                # Default values if not specified in config
                default_origin = [7.7, 12.5]  # Center of house

                # Parse color - could be hex string or RGB array
                color = spatial.get("color", "#ffffff")
                if isinstance(color, list) and len(color) == 3:
                    # Convert RGB array to hex
                    color = f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}"

                if audio_url and lsq_url:
                    asset = {
                        "id": section_key,
                        "lsq_url": lsq_url,
                        "audio_url": audio_url,
                        "type": "instrument"
                    }

                    # Add spatial parameters for distance calculation
                    if "originX" in spatial:
                        asset["originX"] = spatial["originX"]
                    if "originY" in spatial:
                        asset["originY"] = spatial["originY"]
                    if "radius" in spatial:
                        asset["radius"] = spatial["radius"]
                    if "rolloff" in spatial:
                        asset["rolloff"] = spatial["rolloff"]
                    if color:
                        asset["color"] = color

                    movement_assets.append(asset)
                    print(f"    Added instrument {section_key} with spatial params")

            # --- PROCESS PILLAR ASSETS (no spatial params — they self-identify) ---
            pillar_audio_map = m_data.get("audio_assets_pillars", {})
            for pillar_id, pillar_url in pillar_audio_map.items():
                if pillar_url:
                    # Pillars have no spatial params - they ignore distance calc entirely
                    # Their position comes from pillar_config.json, read by devices at runtime
                    movement_assets.append({
                        "id": pillar_id,
                        "lsq_url": None,  # Pillars do not use LSQ files
                        "audio_url": pillar_url,
                        "type": "pillar"
                        # No spatial params — pillars use their own position from pillar_config
                    })
                    print(f"    Added pillar {pillar_id}")

            # --- PROCESS LIGHT-ONLY ASSETS (if any) ---
            light_only = m_data.get("light_assets", {})
            for light_id, light_data in light_only.items():
                # Get spatial parameters from instrument config
                spatial = sections.get(light_id, {})

                asset = {
                    "id": light_id,
                    "lsq_url": light_data.get("lsq_url"),
                    "audio_url": None,
                    "type": "light_only"
                }

                # Add spatial parameters
                if "originX" in spatial:
                    asset["originX"] = spatial["originX"]
                if "originY" in spatial:
                    asset["originY"] = spatial["originY"]
                if "radius" in spatial:
                    asset["radius"] = spatial["radius"]

                # Light color
                color = spatial.get("color", light_data.get("color", "#ffffff"))
                if isinstance(color, list) and len(color) == 3:
                    color = f"#{color[0]:02x}{color[1]:02x}{color[2]:02x}"
                asset["color"] = color

                movement_assets.append(asset)
                print(f"    Added light-only {light_id}")

            # Calculate total duration from the longest LSQ file (optional)
            # This could be enhanced by reading LSQ file lengths if needed

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
            print(f"  Added Movement: {mov_entry['movement_name']} with {len(movement_assets)} total assets.")

        # Write the final timeline
        output_file = 'visual_timeline.json'
        with open(output_file, 'w') as f:
            json.dump(final_timeline, f, indent=2)

        print(f"\n✅ SUCCESS: Multi-config timeline with spatial parameters generated.")
        print(f"   Output: {output_file}")
        print(f"   Movements: {len(final_timeline['movements'])}")

        # Summary of spatial data included
        total_instruments = 0
        total_pillars = 0
        for mov in final_timeline['movements']:
            for cue in mov['cues']:
                for asset in cue['assets']:
                    if asset.get('type') == 'pillar':
                        total_pillars += 1
                    else:
                        total_instruments += 1
        print(f"   Instruments with spatial data: {total_instruments}")
        print(f"   Pillars (fixed position): {total_pillars}")

    except Exception as e:
        print(f"❌ CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    generate_multi_movement_timeline()