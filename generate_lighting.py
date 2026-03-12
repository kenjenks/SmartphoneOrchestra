# generate_lighting.py - Creates light sequences (lsq) files
#
# Creates lsq txt files for each movement described in
# show_manifest.json, which has file names for
# instrument_*.musicxml (uncompressed),
# movement_manifest_*.json and instrument_config_*.json
# for each movement.
#
# Performs spatial coordinate calculation and
# Light Sequence (LSQ) generation for the instruments
# described in instrument_config_*.json. Also uses the
# motion cues from the "choreography" block.
#
# It outputs .txt files that are referenced in movement manifests.
#
# After creating the lsq .txt files, upload them to a web host
# then paste the URLs into the "visual_assets" section of
# movement_manifest_*.json.

import xml.etree.ElementTree as ET
import json
import math
import os


class MusicXMLLightingGenerator:
    def __init__(self, fps=20):
        self.fps = fps

    def parse_musicxml_dynamics(self, xml_path, target_part_name):
        """Extracts a timestamped list of intensity values from MusicXML."""
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
        except Exception as e:
            print(f"Error reading XML {xml_path}: {e}")
            return [], 0

        # Find the correct part
        part_id = None
        for part in root.findall(".//part-list/score-part"):
            name_elem = part.find("part-name")
            if name_elem is not None and name_elem.text:
                name = name_elem.text
                if target_part_name.lower() in name.lower():
                    part_id = part.get("id")
                    break

        if not part_id:
            print(f"  Part '{target_part_name}' not found in MusicXML")
            return [], 0

        part_node = root.find(f".//part[@id='{part_id}']")
        if part_node is None:
            print(f"  Part node with id '{part_id}' not found")
            return [], 0

        current_time = 0.0
        dynamics_timeline = []  # List of (time, intensity_val)

        # Default starting values
        divisions = 1
        tempo_bpm = 120.0

        for measure in part_node.findall("measure"):
            # Update Tempo/Divisions if present
            attr = measure.find("attributes")
            if attr is not None:
                div_node = attr.find("divisions")
                if div_node is not None and div_node.text:
                    divisions = int(div_node.text)

            sound = measure.find(".//direction/sound")
            if sound is not None and sound.get("tempo"):
                tempo_bpm = float(sound.get("tempo"))

            # Logic: Duration of a 'division' in seconds
            # seconds_per_beat = 60 / bpm. Standard quarter note = divisions.
            sec_per_div = 60.0 / (tempo_bpm * divisions) if divisions > 0 else 0.125

            for element in measure:
                # Handle Dynamics (p, mf, f, etc.)
                dynamic_node = element.find(".//direction-type/dynamics")
                if dynamic_node is not None and len(dynamic_node) > 0:
                    # Get the first tag name (e.g., <mf/>)
                    dynamic_mark = dynamic_node[0].tag
                    dynamics_timeline.append((current_time, dynamic_mark))

                # Advance time for notes and rests
                if element.tag == "note":
                    dur_node = element.find("duration")
                    if dur_node is not None and dur_node.text:
                        current_time += int(dur_node.text) * sec_per_div

        return dynamics_timeline, current_time

    def generate_lsq_for_movement(self, mov_entry):
        """
        Processes a single movement: loads its specific manifest and config,
        parses the MusicXML, and generates unique .txt LSQ files.
        """
        # 1. Setup paths and IDs
        m_file = mov_entry.get("manifest_file")
        inst_path = mov_entry.get("instrument_config")
        m_id = mov_entry.get("movement_id", "default_mvt")
        m_name = mov_entry.get("movement_name", "Unknown Movement")

        if not m_file or not inst_path:
            print(f"Skipping {m_name}: Missing manifest_file or instrument_config")
            return

        print(f"\nProcessing: {m_name} ({m_id})")
        print(f"  Manifest: {m_file}")
        print(f"  Config: {inst_path}")

        # 2. Load the specific movement and instrument data
        try:
            with open(m_file, 'r') as f:
                manifest = json.load(f)

            with open(inst_path, 'r') as f:
                inst_config = json.load(f)
        except Exception as e:
            print(f"  Error loading manifests for {m_id}: {e}")
            return

        xml_filename = manifest.get("musicXml", {}).get("filename")
        if not xml_filename:
            print(f"  Skipping {m_id}: No musicXml defined in {m_file}.")
            return

        print(f"  MusicXML: {xml_filename}")

        dynamic_map = inst_config.get("dynamic_map", {})
        sections = inst_config.get("sections", {})

        if not sections:
            print(f"  Warning: No sections defined in {inst_path}")
            return

        # 3. Load Pillar Coordinates (Hardware anchors)
        # Defaulting to a 20x20m square if pillar_config.json is missing
        pillars = [{"x": 0, "y": 0, "id": "PILLAR_FL"},
                   {"x": 20, "y": 0, "id": "PILLAR_FR"},
                   {"x": 0, "y": 20, "id": "PILLAR_BL"},
                   {"x": 20, "y": 20, "id": "PILLAR_BR"}]

        if os.path.exists("pillar_config.json"):
            try:
                with open("pillar_config.json", 'r') as f:
                    pillar_data = json.load(f)
                    if "pillars" in pillar_data:
                        pillars = pillar_data["pillars"]
                        print(f"  Loaded {len(pillars)} pillars from config")
            except Exception as e:
                print(f"  Warning: Could not parse pillar_config.json, using defaults. Error: {e}")

        # Ensure output directory exists
        os.makedirs('lsq', exist_ok=True)

        # 4. Generate LSQ for each instrument section defined in the config
        for instrument, sec_conf in sections.items():
            print(f"  Generating for instrument: {instrument}")

            timeline, total_dur = self.parse_musicxml_dynamics(xml_filename, instrument)

            if not timeline and total_dur == 0:
                print(f"    Warning: No data found for {instrument} in {xml_filename}")
                continue

            # Convert discrete MusicXML dynamics into a continuous stream at the defined FPS
            steps = int(total_dur * self.fps)
            if steps == 0:
                print(f"    Warning: Zero steps calculated for {instrument}")
                continue

            lsq_rows = []
            current_dynamic = "mp"  # Default starting dynamic
            timeline_index = 0

            print(f"    Duration: {total_dur:.2f}s, Steps: {steps} at {self.fps}fps")

            for i in range(steps):
                curr_t = i / self.fps

                # Update current dynamic based on the timeline
                while timeline_index < len(timeline) and timeline[timeline_index][0] <= curr_t:
                    current_dynamic = timeline[timeline_index][1]
                    timeline_index += 1

                base_intensity = dynamic_map.get(current_dynamic, 0.5)

                # Spatial pillar calculation (Inverse Distance Weighting)
                sx = sec_conf.get('originX', 10)
                sy = sec_conf.get('originY', 10)
                srad = sec_conf.get('radius', 15)

                row = []
                for p in pillars:
                    dist = math.sqrt((sx - p['x']) ** 2 + (sy - p['y']) ** 2)
                    # Calculate gain: 1.0 at origin, 0.0 at radius distance
                    pillar_gain = max(0, 1 - (dist / srad)) if srad > 0 else 0
                    row.append(f"{(base_intensity * pillar_gain):.2f}")

                lsq_rows.append(",".join(row))

            # 5. Output filename uses movement_id to prevent collisions
            output_fn = f"lsq/{instrument.lower()}_{m_id}.txt"

            with open(output_fn, 'w') as out:
                out.write("\n".join(lsq_rows))
            print(f"    -> Saved {output_fn} ({len(lsq_rows)} frames)")


# Execution logic - FIXED: Now properly reads show_manifest.json
if __name__ == "__main__":
    generator = MusicXMLLightingGenerator(fps=20)

    # Check if show_manifest.json exists
    if not os.path.exists('show_manifest.json'):
        print("ERROR: show_manifest.json not found in current directory")
        print("Please ensure show_manifest.json is present with movement definitions")
        exit(1)

    try:
        with open('show_manifest.json', 'r') as f:
            show_manifest = json.load(f)

        movements = show_manifest.get('movements', [])
        if not movements:
            print("WARNING: No movements found in show_manifest.json")

        for mov_entry in movements:
            generator.generate_lsq_for_movement(mov_entry)

        print("\n✅ LSQ generation complete!")

    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON in show_manifest.json: {e}")
    except Exception as e:
        print(f"ERROR: {e}")