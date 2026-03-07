# generate_lighting.py - Creates lighting cues and timeline entries for musical instruments from an uncompressed musicXml file.

import xml.etree.ElementTree as ET
import json


def seconds_to_timestamp(base_time, offset_seconds):
    """Converts seconds into HH:MM:SS.mmm format for sub-second precision."""
    h, m, s = map(int, base_time.split(':'))

    # Calculate total seconds as a float
    total_seconds = (h * 3600) + (m * 60) + s + offset_seconds

    new_h = int((total_seconds // 3600) % 24)
    new_m = int((total_seconds % 3600) // 60)
    new_s = int(total_seconds % 60)
    # Extract milliseconds
    milli = int((total_seconds % 1) * 1000)

    return f"{new_h:02d}:{new_m:02d}:{new_s:02d}.{milli:03d}"


def generate_lighting(xml_path, config_path):
    with open(config_path, 'r') as f:
        config = json.load(f)

    tree = ET.parse(xml_path)
    root = tree.getroot()

    # Default constants (will be updated by XML scan)
    current_bpm = 120.0
    time_numerator = 4
    running_total_seconds = 0.0

    timeline_events, cues_data = [], []
    part_mapping = {}

    # Map XML Parts to Sections
    for score_part in root.findall(".//score-part"):
        p_id, p_name = score_part.get('id'), score_part.find('part-name').text
        for key in config["sections"].keys():
            if key.lower() in p_name.lower():
                part_mapping[p_id] = key

    # Iterate by measure using the first part as the master timeline
    first_part = root.find("part")

    for measure in first_part.findall('measure'):
        m_num = int(measure.get('number'))

        # Check for Tempo or Time Signature changes in the master part
        tempo_change = measure.find(".//per-minute")
        if tempo_change is not None:
            current_bpm = float(tempo_change.text)

        beats = measure.find(".//beats")
        if beats is not None:
            time_numerator = int(beats.text)

        # Calculate this specific measure's duration using floats
        measure_duration = (60.0 / current_bpm) * time_numerator

        # Calculate timestamp for the start of this measure
        timestamp = seconds_to_timestamp(config["start_time_base"], running_total_seconds)

        for part in root.findall('part'):
            p_id = part.get('id')
            if p_id not in part_mapping: continue

            section_key = part_mapping[p_id]
            sec_conf = config["sections"][section_key]

            m_in_part = part.find(f"measure[@number='{m_num}']")
            if m_in_part is None: continue

            current_intensity = 0.5
            for dyn in m_in_part.findall('.//dynamics/*'):
                if dyn.tag in config["dynamic_map"]:
                    current_intensity = config["dynamic_map"][dyn.tag]

            # Check for audible notes
            if any(n.find('rest') is None for n in m_in_part.findall('.//note')):
                cue_label = f"VIS_{section_key.upper()}_M{m_num}"
                cues_data.append({
                    "label": cue_label,
                    "type": "VISUAL",
                    "color": sec_conf["color"],
                    "originX": sec_conf["originX"],
                    "originY": sec_conf["originY"],
                    "radius": sec_conf["radius"],
                    "intensity": current_intensity
                })
                timeline_events.append({
                    "time": timestamp,
                    "cueLabel": cue_label,
                    "label": f"{section_key} (M{m_num})"
                })

        # Increment time for the next measure
        running_total_seconds += measure_duration

    # Save outputs
    with open('visual_cues.json', 'w') as f:
        json.dump(cues_data, f, indent=2)
    with open('visual_timeline.json', 'w') as f:
        json.dump(timeline_events, f, indent=2)

    print(f"Generated {len(timeline_events)} cues. Final duration: {running_total_seconds:.3f}s")


if __name__ == "__main__":
    # Now using instrument.musicxml as requested
    generate_lighting('instrument.musicxml', 'instrument_config.json')