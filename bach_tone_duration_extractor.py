"""
bach_tone_duration_extractor.py

Extracts note timing from MusicXML for both piccolo and pillar parts.
Uses fixed tempo from config file for perfect synchronization.

This version:
- Reads configuration from bach_tone_duration_extractor.json
- Parses piccolo MusicXML to get exact note sequence
- Parses quad piano MusicXML to extract pillar notes for Phase 1 mic calibration
- Creates tuning_mode.json with perfect timing from the score
- Pillar calibration windows are sequential (2 measures per pillar)
- SUPPORTS STAFF FILTERING: can extract notes from specific staff (e.g., staff 1 for piccolo parts)
- FILTERS OUT pillar notes that overlap with string quartet parts (violin, viola, cello, bass)

REQUIREMENTS
------------
pip install music21
"""

import os
import json
import music21
from datetime import datetime


# ---------------------------------------------------------------------------
# LOAD CONFIGURATION
# ---------------------------------------------------------------------------

def load_config():
    """Load configuration from JSON file."""
    config_path = os.path.join(os.path.dirname(__file__), "bach_tone_duration_extractor.json")

    if not os.path.exists(config_path):
        print(f"❌ Config file not found: {config_path}")
        print("   Please create bach_tone_duration_extractor.json")
        return None

    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    return config


# ---------------------------------------------------------------------------
# MAIN EXTRACTOR CLASS
# ---------------------------------------------------------------------------

class BachToneExtractor:
    def __init__(self, config):
        self.config = config

        # Extract paths
        base_dir = config["paths"]["base_dir"]
        self.piccolo_path = os.path.join(base_dir, config["paths"]["input_musicxml"])
        self.pillars_path = os.path.join(base_dir, config["paths"]["input_musicxml_pillars"])
        self.output_path = config["paths"]["output_file"]

        # Extract tempo and timing
        self.tempo_bpm = config["tempo"]["bpm"]
        self.beats_per_measure = config["time_signature"]["beats_per_measure"]
        self.beat_unit = config["time_signature"]["beat_unit"]
        self.window_measures = config["calibration"]["window_measures"]

        # Pillar order for calibration windows
        self.pillar_order = config["pillar_order"]

        # Pillar mapping - handle both old (string) and new (dict) formats
        self.pillar_mapping = config["pillar_musicxml_mapping"]

        # Build part_id_to_pillar mapping from the config
        self.part_id_to_pillar = {}
        self.pillar_staff_map = {}
        self.pillar_instrument_map = {}  # Map pillar_id to instrument name for filtering

        for pillar_id, mapping in self.pillar_mapping.items():
            if isinstance(mapping, dict):
                # New format with part_id and staff
                part_id = mapping.get("part_id")
                staff = mapping.get("staff", 1)
                instrument = mapping.get("instrument", "Piccolo")
                if part_id:
                    self.part_id_to_pillar[part_id] = pillar_id
                    self.pillar_staff_map[pillar_id] = staff
                    self.pillar_instrument_map[pillar_id] = instrument
            else:
                # Old format - just part_id string
                self.part_id_to_pillar[mapping] = pillar_id
                self.pillar_staff_map[pillar_id] = 1
                self.pillar_instrument_map[pillar_id] = "Piccolo"

        # String quartet part IDs to filter out (from config or defaults)
        self.string_quartet_parts = config.get("string_quartet_parts", [
            "Violin", "Viola", "Violoncello", "Cello", "Contrabass", "Bass"
        ])

        # URLs for assets
        self.urls = config["urls"]

        # Window start measures
        self.window_start_measures = config.get("window_start_measures", [1, 3, 5, 7])

        # Default pillar staff
        self.default_pillar_staff = config.get("analysis_params", {}).get("pillar_staff", 1)

        # Calculate durations
        self.quarter_duration_ms = 60000 / self.tempo_bpm
        self.measure_duration_ms = self.beats_per_measure * self.quarter_duration_ms

        print(f"✅ Configuration loaded:")
        print(f"   Tempo: {self.tempo_bpm} BPM")
        print(f"   Time signature: {self.beats_per_measure}/{self.beat_unit}")
        print(f"   Measure duration: {self.measure_duration_ms:.1f}ms")
        print(f"   Window duration: {self.window_measures * self.measure_duration_ms:.1f}ms")
        print(f"   Pillar mapping: {self.part_id_to_pillar}")
        print(f"   Pillar staff map: {self.pillar_staff_map}")
        print(f"   String quartet parts to filter: {self.string_quartet_parts}")

    def get_note_staff(self, element):
        """
        Get the staff number for a note element.
        Returns None if staff not specified.
        """
        # Try to get staff from element's staff attribute
        if hasattr(element, 'staff') and element.staff:
            staff = element.staff
            if isinstance(staff, list) and len(staff) > 0:
                return staff[0]
            elif isinstance(staff, (int, str)):
                return int(staff)

        # Try to get staff from the element's parent context
        try:
            context = element
            while context:
                if hasattr(context, 'staff') and context.staff:
                    staff = context.staff
                    if isinstance(staff, list) and len(staff) > 0:
                        return staff[0]
                    elif isinstance(staff, (int, str)):
                        return int(staff)
                context = getattr(context, '_activeSite', None) if hasattr(context, '_activeSite') else None
        except:
            pass

        return None

    def parse_piccolo_musicxml(self):
        """Extract perfect note sequence from piccolo MusicXML."""
        print(f"\n📄 Loading piccolo MusicXML: {self.piccolo_path}")

        try:
            score = music21.converter.parse(self.piccolo_path)
        except Exception as e:
            print(f"❌ Error parsing MusicXML: {e}")
            return None, None

        # Get the first part (the piccolo)
        if len(score.parts) > 0:
            part = score.parts[0]
        else:
            print("❌ No parts found in MusicXML")
            return None, None

        # Extract notes
        notes = []
        current_offset = 0.0  # in quarter notes

        for element in part.flatten().notesAndRests:
            if element.isNote:
                # Get duration in quarters
                duration_q = element.duration.quarterLength

                # Get pitch
                pitch = element.pitch.nameWithOctave
                midi = element.pitch.midi

                notes.append({
                    'pitch': pitch,
                    'midi': midi,
                    'duration_q': duration_q,
                    'offset_q': current_offset,
                })

            current_offset += element.duration.quarterLength

        print(f"   Found {len(notes)} notes in MusicXML")
        print(f"   Total duration: {current_offset:.2f} quarter notes")

        return notes, current_offset

    def create_timeline_from_musicxml(self, xml_notes):
        """Create timeline directly from MusicXML using fixed tempo."""
        if xml_notes is None:
            return None

        print(f"\n📝 Creating timeline from MusicXML (tempo: {self.tempo_bpm} BPM)")

        timeline = []
        total_duration_ms = 0

        for i, xml_note in enumerate(xml_notes):
            offset_ms = xml_note['offset_q'] * self.quarter_duration_ms
            duration_ms = xml_note['duration_q'] * self.quarter_duration_ms
            pulse_ms = offset_ms + (duration_ms / 2)
            measure = int(xml_note['offset_q'] / self.beats_per_measure) + 1

            timeline.append({
                "id": f"m{measure}_n{i}",
                "index": i,
                "measure": measure,
                "ms_offset": round(offset_ms, 1),
                "duration_ms": round(duration_ms, 1),
                "pulse_ms": round(pulse_ms, 1),
                "pitch": xml_note['pitch'],
                "midi": xml_note['midi'],
                "has_fsk": False,
            })

            total_duration_ms = max(total_duration_ms, offset_ms + duration_ms)

        print(f"   Created {len(timeline)} notes from MusicXML")
        print(f"   Total duration: {total_duration_ms / 1000:.2f}s at {self.tempo_bpm} BPM")

        print("\n   First few notes:")
        for i, note in enumerate(timeline[:5]):
            print(f"     {i}: {note['pitch']} at {note['ms_offset']:.1f}ms, duration {note['duration_ms']:.1f}ms")

        return timeline

    def collect_all_notes(self, score):
        """Collect all notes from all parts for overlap detection."""
        all_notes = []

        for part in score.parts:
            part_id = part.id
            current_offset_q = 0.0

            for element in part.flatten().notesAndRests:
                if element.isNote:
                    measure = element.measureNumber
                    staff_num = self.get_note_staff(element)
                    pitch = element.pitch.nameWithOctave
                    midi = element.pitch.midi
                    duration_q = element.duration.quarterLength

                    ms_offset = current_offset_q * self.quarter_duration_ms
                    duration_ms = duration_q * self.quarter_duration_ms
                    ms_end = ms_offset + duration_ms

                    all_notes.append({
                        "part_id": part_id,
                        "measure": measure,
                        "ms_offset": ms_offset,
                        "ms_end": ms_end,
                        "duration_ms": duration_ms,
                        "pitch": pitch,
                        "midi": midi,
                        "staff": staff_num
                    })

                current_offset_q += element.duration.quarterLength

        return all_notes

    def note_overlaps_with_string_quartet(self, note, all_notes):
        """
        Check if a note overlaps with any string quartet part.
        Returns True if overlap found, False otherwise.
        """
        for other in all_notes:
            # Skip if it's the same note or same part
            if other["part_id"] == note["part_id"]:
                continue

            # Check if other part is in string quartet
            is_string_quartet = False
            for sq_part in self.string_quartet_parts:
                if sq_part in other["part_id"]:
                    is_string_quartet = True
                    break

            if not is_string_quartet:
                continue

            # Check for time overlap
            if not (note["ms_end"] <= other["ms_offset"] or other["ms_end"] <= note["ms_offset"]):
                return True

        return False

    def extract_pillar_notes(self):
        """
        Extract pillar notes from quad piano MusicXML.

        Filters out notes that overlap with string quartet parts to ensure
        clean, unambiguous detection.
        """
        print(f"\n🎹 Loading pillar MusicXML: {self.pillars_path}")

        try:
            score = music21.converter.parse(self.pillars_path)
        except Exception as e:
            print(f"❌ Error parsing pillar MusicXML: {e}")
            return None, None, 0

        # Get time signature
        time_sig = None
        for part in score.parts:
            for ts in part.flatten().getElementsByClass(music21.meter.TimeSignature):
                time_sig = (ts.numerator, ts.denominator)
                break
            if time_sig:
                break

        if not time_sig:
            time_sig = (3, 4)
            print("   ⚠️ No time signature found, assuming 3/4")

        print(f"   Time signature: {time_sig[0]}/{time_sig[1]}")

        # Collect all notes from all parts for overlap detection
        all_notes = self.collect_all_notes(score)
        print(f"   Collected {len(all_notes)} total notes from all parts")

        # Group notes by part for processing
        notes_by_part = {}
        for note in all_notes:
            if note["part_id"] not in notes_by_part:
                notes_by_part[note["part_id"]] = []
            notes_by_part[note["part_id"]].append(note)

        # Process each pillar part
        pillar_notes_by_id = {pid: [] for pid in self.pillar_order}
        overlap_counts = {pid: 0 for pid in self.pillar_order}

        for part_id, pillar_id in self.part_id_to_pillar.items():
            print(f"\n   Processing pillar part: {part_id} -> {pillar_id}")

            if part_id not in notes_by_part:
                print(f"      ⚠️ No notes found for part {part_id}")
                continue

            part_notes = notes_by_part[part_id]
            target_staff = self.pillar_staff_map.get(pillar_id, self.default_pillar_staff)

            filtered_notes = []
            overlap_count = 0

            for note in part_notes:
                # Skip if staff doesn't match target
                if note["staff"] is not None and note["staff"] != target_staff:
                    continue

                # Check for overlap with string quartet
                if self.note_overlaps_with_string_quartet(note, all_notes):
                    overlap_count += 1
                    if overlap_count <= 10:
                        print(f"         ⚠️ Filtering note: {note['pitch']} at {note['ms_offset']:.1f}ms "
                              f"(overlaps with string quartet)")
                else:
                    # Clean note - no overlap with strings
                    filtered_notes.append({
                        "pillar_id": pillar_id,
                        "index": None,
                        "measure": note["measure"],
                        "ms_offset": round(note["ms_offset"], 1),
                        "duration_ms": round(note["duration_ms"], 1),
                        "pitch": note["pitch"],
                        "midi": note["midi"],
                        "pulse_ms": round(note["ms_offset"] + note["duration_ms"] / 2, 1)
                    })

            pillar_notes_by_id[pillar_id] = filtered_notes
            overlap_counts[pillar_id] = overlap_count
            print(f"      Added {len(filtered_notes)} clean notes (filtered {overlap_count} overlapping with strings)")

        # Combine all pillar notes into a single list with global indices
        all_pillar_notes = []
        for pillar_id in self.pillar_order:
            notes = pillar_notes_by_id.get(pillar_id, [])
            # Sort by measure number
            notes.sort(key=lambda x: x['measure'])

            for note in notes:
                note['index'] = len(all_pillar_notes)
                all_pillar_notes.append(note)

            print(f"\n   {pillar_id}: {len(notes)} clean notes extracted")
            if notes:
                print(f"      First note: {notes[0]['pitch']} at {notes[0]['ms_offset']:.1f}ms (measure {notes[0]['measure']})")
                print(f"      Last note: {notes[-1]['pitch']} at {notes[-1]['ms_offset']:.1f}ms (measure {notes[-1]['measure']})")

        # Create calibration windows
        pillar_windows = []
        for i, pillar_id in enumerate(self.pillar_order):
            if i < len(self.window_start_measures):
                start_measure = self.window_start_measures[i]
            else:
                start_measure = i * self.window_measures + 1

            end_measure = start_measure + self.window_measures - 1
            start_ms = (start_measure - 1) * self.measure_duration_ms
            end_ms = end_measure * self.measure_duration_ms

            pillar_notes = pillar_notes_by_id.get(pillar_id, [])
            window_notes = [n for n in pillar_notes if start_measure <= n["measure"] <= end_measure]

            pillar_windows.append({
                "pillar_id": pillar_id,
                "start_measure": start_measure,
                "end_measure": end_measure,
                "start_ms": round(start_ms, 1),
                "end_ms": round(end_ms, 1),
                "note_count": len(window_notes)
            })

            print(f"\n   {pillar_id}: {len(window_notes)} clean notes in measures {start_measure}-{end_measure}")
            print(f"      Window: {start_ms:.1f}ms → {end_ms:.1f}ms")

        # Calculate total duration
        if pillar_windows:
            last_window = pillar_windows[-1]
            total_duration_ms = last_window["end_ms"]
        else:
            total_duration_ms = len(self.pillar_order) * self.window_measures * self.measure_duration_ms

        print(f"\n✅ Extracted {len(all_pillar_notes)} total clean pillar notes")
        print(f"   Total duration: {total_duration_ms / 1000:.2f}s")

        return all_pillar_notes, pillar_windows, total_duration_ms

    def create_manifest(self, piccolo_timeline, pillar_notes, pillar_windows):
        """Create the final tuning_mode.json manifest."""

        # Build asset URLs
        piccolo_full_url = self.urls["piccolo"]
        test_pulse_full_url = self.urls["test_pulse"]
        pillar_full_urls = self.urls["pillars"]

        manifest = {
            "metadata": {
                "source_musicxml": os.path.basename(self.piccolo_path),
                "source_musicxml_pillars": os.path.basename(self.pillars_path),
                "source_audio_url": piccolo_full_url,
                "test_pulse_url": test_pulse_full_url,
                **{f"source_audio_{pos}": url for pos, url in pillar_full_urls.items()},
                "tempo": self.tempo_bpm,
                "time_signature": f"{self.beats_per_measure}/{self.beat_unit}",
                "total_duration_ms": round(piccolo_timeline[-1]["ms_offset"] + piccolo_timeline[-1]["duration_ms"], 1),
                "total_notes": len(piccolo_timeline),
                "total_measures": piccolo_timeline[-1]["measure"],
                "generated_by": "bach_tone_duration_extractor.py",
                "generated_at": datetime.now().isoformat(),
                "asset_version": "1.0",
                "asset_description": f"Perfect timing from MusicXML at {self.tempo_bpm} BPM",
                "analysis_params": {
                    "method": "musicxml_only",
                    "tempo": self.tempo_bpm,
                    "beat_unit_ms": round(self.quarter_duration_ms, 2),
                    "filtered_overlapping_with_strings": True,
                },
                "pillar_cal_windows": pillar_windows if pillar_windows else [],
            },
            "piano_notes": piccolo_timeline,
            "assets": {
                "piccolo": piccolo_full_url,
                "test_pulse": test_pulse_full_url,
                "pillars": pillar_full_urls,
            },
            "performance": {
                "calibration_strategy": {
                    "method": "musicxml_timing",
                    "note_source": "MusicXML",
                    "timing_source": "MusicXML (no audio)",
                    "philosophy": "Perfect score timing at original tempo"
                }
            }
        }

        # Add pillar notes if extracted
        if pillar_notes:
            manifest["pillar_notes"] = pillar_notes
            manifest["metadata"]["pillar_total_duration_ms"] = round(
                len(self.pillar_order) * self.window_measures * self.measure_duration_ms, 1
            )
            manifest["metadata"]["pillar_notes_count"] = len(pillar_notes)

        return manifest

    def save_manifest(self, manifest):
        """Save the manifest to file."""
        with open(self.output_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

        print(f"\n✅ Manifest saved: {self.output_path}")

        print(f"\n📊 Statistics:")
        print(f"   • Piccolo notes: {len(manifest['piano_notes'])}")
        if manifest["metadata"].get("pillar_notes_count"):
            print(f"   • Pillar notes:  {manifest['metadata']['pillar_notes_count']}")
        print(f"   • Tempo: {self.tempo_bpm} BPM")
        print(f"   • Time signature: {manifest['metadata']['time_signature']}")
        print(f"   • Total duration: {manifest['metadata']['total_duration_ms'] / 1000:.2f}s")

        print(f"\n🎯 Pillar Calibration Windows:")
        for w in manifest["metadata"]["pillar_cal_windows"]:
            print(f"   {w['pillar_id']:12s}: measures {w['start_measure']}-{w['end_measure']} → {w['start_ms']:.1f}ms → {w['end_ms']:.1f}ms")

    def run(self):
        """Main execution pipeline."""
        print("=" * 60)
        print("bach_tone_duration_extractor.py - MusicXML Timing Mode")
        print("=" * 60)
        print(f"\n🎯 Using MusicXML timing (fixed tempo: {self.tempo_bpm} BPM)")

        # Parse piccolo MusicXML
        xml_notes, _ = self.parse_piccolo_musicxml()
        if xml_notes is None:
            return None

        # Create timeline from MusicXML
        piccolo_timeline = self.create_timeline_from_musicxml(xml_notes)
        if piccolo_timeline is None:
            return None

        # Extract pillar notes (if file exists)
        if os.path.exists(self.pillars_path):
            pillar_notes, pillar_windows, _ = self.extract_pillar_notes()
        else:
            print(f"\n⚠️ Pillar MusicXML not found: {self.pillars_path}")
            pillar_notes = None
            pillar_windows = []

        # Create manifest
        manifest = self.create_manifest(piccolo_timeline, pillar_notes, pillar_windows)

        # Save manifest
        self.save_manifest(manifest)

        print(f"\n✅ Done!")
        return manifest


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    config = load_config()
    if config:
        extractor = BachToneExtractor(config)
        extractor.run()