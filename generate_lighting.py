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
            return []

        # Find the correct part
        part_id = None
        for part in root.findall(".//part-list/score-part"):
            name = part.find("part-name").text
            if name and target_part_name.lower() in name.lower():
                part_id = part.get("id")
                break

        if not part_id:
            return []

        part_node = root.find(f".//part[@id='{part_id}']")
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
                if div_node is not None: divisions = int(div_node.text)

            sound = measure.find(".//direction/sound")
            if sound is not None and sound.get("tempo"):
                tempo_bpm = float(sound.get("tempo"))

            # Logic: Duration of a 'division' in seconds
            # seconds_per_beat = 60 / bpm. Standard quarter note = divisions.
            sec_per_div = 60.0 / (tempo_bpm * divisions)

            for element in measure:
                # Handle Dynamics (p, mf, f, etc.)
                dynamic_node = element.find(".//direction-type/dynamics")
                if dynamic_node is not None:
                    # Get the first tag name (e.g., <mf/>)
                    dynamic_mark = dynamic_node[0].tag
                    dynamics_timeline.append((current_time, dynamic_mark))

                # Advance time for notes and rests
                if element.tag == "note":
                    dur_node = element.find("duration")
                    if dur_node is not None:
                        current_time += int(dur_node.text) * sec_per_div

        return dynamics_timeline, current_time

    def generate_lsq_for_movement(self, mov_manifest_path, show_manifest_dir="."):
        with open(mov_manifest_path, 'r') as f:
            manifest = json.load(f)

        # Get XML and Config paths
        xml_filename = manifest.get("musicXml", {}).get("filename")
        if not xml_filename: return

        # Find which instrument config to use (from show_manifest context usually)
        # For this script, we'll assume it's the one linked in the manifest or default
        config_path = "instrument_config.json"  # Fallback

        with open(config_path, 'r') as f:
            inst_config = json.load(f)

        dynamic_map = inst_config.get("dynamic_map", {})
        sections = inst_config.get("sections", {})

        # We also need pillar positions to calculate spatial intensity
        with open("pillar_config.json", 'r') as f:
            pillars = json.load(f).get("pillars", [])

        os.makedirs('lsq', exist_ok=True)

        for instrument, sec_conf in sections.items():
            print(f"Processing {instrument} from {xml_filename}...")

            timeline, total_dur = self.parse_musicxml_dynamics(xml_filename, instrument)
            if not timeline: continue

            # Convert discrete dynamics into a continuous stream at 20fps
            steps = int(total_dur * self.fps)
            lsq_rows = []

            current_dynamic = "mp"  # Default starting dynamic

            for i in range(steps):
                curr_t = i / self.fps

                # Check for dynamic updates
                for event_t, mark in timeline:
                    if event_t <= curr_t:
                        current_dynamic = mark

                base_intensity = dynamic_map.get(current_dynamic, 0.5)

                # Spatial pillar calculation
                sx, sy, srad = sec_conf['originX'], sec_conf['originY'], sec_conf['radius']
                row = []
                for p in pillars:
                    dist = math.sqrt((sx - p['x']) ** 2 + (sy - p['y']) ** 2)
                    pillar_gain = max(0, 1 - (dist / srad))
                    row.append(f"{(base_intensity * pillar_gain):.2f}")

                lsq_rows.append(",".join(row))

            output_fn = f"lsq/{instrument.lower()}_mvt.txt"
            with open(output_fn, 'w') as out:
                out.write("\n".join(lsq_rows))
            print(f"  -> Saved {output_fn}")


# Execution logic
if __name__ == "__main__":
    generator = MusicXMLLightingGenerator(fps=20)
    # Iterate through your movement manifests
    manifests = ["movement_manifest_intro.json", "movement_manifest_storm.json"]
    for m in manifests:
        generator.generate_lsq_for_movement(m)