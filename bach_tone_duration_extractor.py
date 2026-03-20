"""
bach_tone_duration_extractor.py

Extracts note timing from MusicXML for both piccolo and pillar parts.
Uses fixed tempo (70 BPM) from the score for perfect synchronization.

This version:
- Parses piccolo MusicXML to get exact note sequence (pitches, durations, offsets)
- Parses quad piano MusicXML to extract pillar notes for Phase 1 mic calibration
- Creates tuning_mode.json with perfect timing from the score
- No audio alignment needed - devices play at the correct tempo

REQUIREMENTS
------------
pip install music21
"""

import os
import json
import music21
from datetime import datetime

# ---------------------------------------------------------------------------
# CONFIGURATION — UPDATE THESE BEFORE RUNNING
# ---------------------------------------------------------------------------

# Base directory
BASE_DIR = r"C:\\Users\\ken\\Documents\\Ken\\Fiction\\HMP_SW\\Script_Analysis_and_Production\\SmartphoneOrchestra stuff\\Jesu Joy"

# Input files
INPUT_MUSICXML = os.path.join(BASE_DIR, "Tuning-Jesu - Stacatto - Piccolo.musicxml")
INPUT_MUSICXML_PILLARS = os.path.join(BASE_DIR, "Tuning-Jesu - Stacatto - quad piano.musicxml")

# Tempo from MusicXML (70 BPM for Jesu, Joy of Man's Desiring)
TEMPO_BPM = 70

# --- WIX CDN ASSET URLS ---
WIX_AUDIO_BASE = "https://static.wixstatic.com/mp3/"

# Update these with your actual uploaded filenames
FLUTE_URL = "9178d1_15f2dad109ba41678cc46d01123b1dc5.mp3"
TEST_PULSE_URL = "9178d1_63e58ac50ec7434eba6785cd3291c227.mp3"

PILLAR_URLS = {
    "FL": "9178d1_9aba02e74fc344808064f364bb505086.mp3",
    "FR": "9178d1_08a292cbcee448d7b83e464d6a94b377.mp3",
    "BL": "9178d1_8987080abdb14616a0e9562387923614.mp3",
    "BR": "9178d1_8b53f52bbce6406ebacae8288777bad5.mp3",
}

# ---------------------------------------------------------------------------
# PITCH UTILITIES
# ---------------------------------------------------------------------------

_SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
_FLAT_OVERRIDE = {10: "Bb", 3: "Eb", 8: "Ab", 1: "Db", 6: "Gb"}


def midi_to_note_name(midi_val: int) -> str:
    """Convert a rounded MIDI note number to a pitch string."""
    pc = midi_val % 12
    oct_ = midi_val // 12 - 1
    return f"{_FLAT_OVERRIDE.get(pc, _SHARP_NAMES[pc])}{oct_}"


# ---------------------------------------------------------------------------
# PARSE PICCOLO MUSICXML
# ---------------------------------------------------------------------------

def parse_piccolo_musicxml(xml_path: str) -> tuple:
    """
    Extract perfect note sequence from piccolo MusicXML.
    Returns:
        - notes: list of dicts with pitch, duration_q, offset_q
        - total_quarters: total duration in quarter notes
        - time_signature: tuple of (numerator, denominator)
    """
    print(f"📄 Loading piccolo MusicXML: {xml_path}")

    try:
        score = music21.converter.parse(xml_path)
    except Exception as e:
        print(f"❌ Error parsing MusicXML: {e}")
        return None, None, None

    # Get the first part (the piccolo)
    if len(score.parts) > 0:
        part = score.parts[0]
    else:
        print("❌ No parts found in MusicXML")
        return None, None, None

    # Get time signature
    time_sig = None
    for ts in part.flatten().getElementsByClass(music21.meter.TimeSignature):
        time_sig = (ts.numerator, ts.denominator)
        break

    if not time_sig:
        time_sig = (3, 4)  # Jesu is in 3/4
        print("   ⚠️ No time signature found, assuming 3/4")

    print(f"   Time signature: {time_sig[0]}/{time_sig[1]}")

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

    return notes, current_offset, time_sig


# ---------------------------------------------------------------------------
# CREATE TIMELINE FROM MUSICXML (FIXED TEMPO)
# ---------------------------------------------------------------------------

def create_timeline_from_musicxml(xml_notes, time_sig):
    """
    Create timeline directly from MusicXML using fixed tempo.
    This ensures exact note timing matches the musical score.
    """
    if xml_notes is None:
        return None, 0, 0

    print(f"\n📝 Creating timeline from MusicXML (tempo: {TEMPO_BPM} BPM)")

    # Calculate beat duration in ms
    quarter_duration_ms = 60000 / TEMPO_BPM

    # Build timeline
    timeline = []
    total_duration_ms = 0

    for i, xml_note in enumerate(xml_notes):
        # Time in ms from quarter note offset
        offset_ms = xml_note['offset_q'] * quarter_duration_ms
        duration_ms = xml_note['duration_q'] * quarter_duration_ms

        # Calculate pulse at center of note
        pulse_ms = offset_ms + (duration_ms / 2)

        # Calculate measure number (3/4 time signature: each measure is 3 quarter notes)
        beats_per_measure = time_sig[0]
        measure = int(xml_note['offset_q'] / beats_per_measure) + 1

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
    print(f"   Total duration: {total_duration_ms / 1000:.2f}s at {TEMPO_BPM} BPM")

    # Show first few notes
    print("\n   First few notes:")
    for i, note in enumerate(timeline[:5]):
        print(f"     {i}: {note['pitch']} at {note['ms_offset']:.1f}ms, duration {note['duration_ms']:.1f}ms")

    return timeline, quarter_duration_ms, TEMPO_BPM


# ---------------------------------------------------------------------------
# EXTRACT PILLAR NOTES FROM QUAD PIANO MUSICXML
# ---------------------------------------------------------------------------

def extract_pillar_notes(xml_path: str) -> tuple:
    """
    Extract pillar notes from quad piano MusicXML.

    Mapping based on MuseScore staff names:
    - P7 (Piano FL) → PILLAR_FL (measures 1-2)
    - P8 (Piano FR) → PILLAR_FR (measures 3-4)
    - P9 (Piano BL) → PILLAR_BL (measures 5-6)
    - P10 (Piano BR) → PILLAR_BR (measures 7-8)

    Note: The parts are named like "P7-Staff1" and "P7-Staff2" (treble and bass clef)
    We combine both staves for each part.

    Returns:
        - pillar_notes: list of dicts with pillar_id, index, measure, ms_offset, duration_ms, pitch, pulse_ms
        - pillar_cal_windows: list of dicts with pillar_id, start_ms, end_ms (2-measure windows per pillar)
        - total_duration_ms: total duration of all pillar notes
    """
    print(f"\n🎹 Loading pillar MusicXML: {xml_path}")

    try:
        score = music21.converter.parse(xml_path)
    except Exception as e:
        print(f"❌ Error parsing pillar MusicXML: {e}")
        return None, None, 0

    # Map part name prefixes to pillar IDs
    PART_PREFIX_TO_PILLAR = {
        "P7": "PILLAR_FL",  # Piano FL (measures 1-2)
        "P8": "PILLAR_FR",  # Piano FR (measures 3-4)
        "P9": "PILLAR_BL",  # Piano BL (measures 5-6)
        "P10": "PILLAR_BR",  # Piano BR (measures 7-8)
    }

    # Order for calibration windows (clockwise from front left)
    PILLAR_ORDER = ["PILLAR_FL", "PILLAR_FR", "PILLAR_BR", "PILLAR_BL"]

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

    # Calculate beat duration in ms
    quarter_duration_ms = 60000 / TEMPO_BPM

    # Collect all pillar notes, grouped by pillar
    pillar_notes_by_id = {pid: [] for pid in PART_PREFIX_TO_PILLAR.values()}

    # Process each part (including both staff1 and staff2)
    for part in score.parts:
        part_id = part.id
        print(f"\n   Processing part: {part_id}")

        # Check if this part belongs to one of our pillar parts
        pillar_id = None
        for prefix, pid in PART_PREFIX_TO_PILLAR.items():
            if part_id.startswith(prefix):
                pillar_id = pid
                break

        if not pillar_id:
            print(f"      ⚠️ Skipping part {part_id} - not mapped to a pillar")
            continue

        notes_in_part = []
        current_offset_q = 0.0

        print(f"      → {part_id} belongs to {pillar_id}")

        for element in part.flatten().notesAndRests:
            if element.isNote:
                # Get measure number
                measure = element.measureNumber

                # Get pitch
                pitch = element.pitch.nameWithOctave
                midi = element.pitch.midi

                # Duration in quarter notes
                duration_q = element.duration.quarterLength

                # Calculate time in ms
                ms_offset = current_offset_q * quarter_duration_ms
                duration_ms = duration_q * quarter_duration_ms
                pulse_ms = ms_offset + (duration_ms / 2)

                note_data = {
                    "pillar_id": pillar_id,
                    "index": None,  # Will set later after sorting
                    "measure": measure,
                    "ms_offset": round(ms_offset, 1),
                    "duration_ms": round(duration_ms, 1),
                    "pitch": pitch,
                    "midi": midi,
                    "pulse_ms": round(pulse_ms, 1)
                }

                notes_in_part.append(note_data)

            current_offset_q += element.duration.quarterLength

        # Add notes to the pillar's list
        if notes_in_part:
            pillar_notes_by_id[pillar_id].extend(notes_in_part)
            print(f"      Added {len(notes_in_part)} notes to {pillar_id}")
        else:
            print(f"      ⚠️ No notes found in {part_id}")

    # Combine all pillar notes into a single list with global indices
    all_pillar_notes = []
    for pillar_id in PILLAR_ORDER:
        notes = pillar_notes_by_id.get(pillar_id, [])
        # Sort by measure number to ensure correct order
        notes.sort(key=lambda x: x['measure'])

        for i, note in enumerate(notes):
            note['index'] = len(all_pillar_notes)
            all_pillar_notes.append(note)

        print(f"\n   {pillar_id}: {len(notes)} notes extracted")
        if notes:
            print(
                f"      First note: {notes[0]['pitch']} at {notes[0]['ms_offset']:.1f}ms (measure {notes[0]['measure']})")
            print(
                f"      Last note: {notes[-1]['pitch']} at {notes[-1]['ms_offset']:.1f}ms (measure {notes[-1]['measure']})")

    # Calculate total duration from the last note of the last pillar
    total_duration_ms = 0
    if all_pillar_notes:
        last_note = all_pillar_notes[-1]
        total_duration_ms = last_note["ms_offset"] + last_note["duration_ms"]

    # Create calibration windows (2 measures each)
    pillar_windows = []

    for pillar_id in PILLAR_ORDER:
        pillar_notes = pillar_notes_by_id.get(pillar_id, [])
        if not pillar_notes:
            print(f"\n   ⚠️ No notes found for {pillar_id} - skipping window")
            continue

        # Find notes in measures 1 and 2 (the solo sections)
        window_notes = [n for n in pillar_notes if n["measure"] <= 2]

        if window_notes:
            start_ms = min(n["ms_offset"] for n in window_notes)
            end_ms = max(n["ms_offset"] + n["duration_ms"] for n in window_notes)
        else:
            # Fallback: use first few notes (first 2 measures worth)
            measure_duration_ms = 3 * quarter_duration_ms
            start_ms = pillar_notes[0]["ms_offset"]
            end_ms = start_ms + (2 * measure_duration_ms)

        pillar_windows.append({
            "pillar_id": pillar_id,
            "start_ms": round(start_ms, 1),
            "end_ms": round(end_ms, 1)
        })

        print(f"\n   {pillar_id} calibration window: {start_ms:.1f}ms → {end_ms:.1f}ms")

    print(f"\n✅ Extracted {len(all_pillar_notes)} total pillar notes")
    print(f"   Total duration: {total_duration_ms / 1000:.2f}s")

    return all_pillar_notes, pillar_windows, total_duration_ms


# ---------------------------------------------------------------------------
# MAIN PIPELINE
# ---------------------------------------------------------------------------

def create_hybrid_manifest():
    """Main pipeline: extract MusicXML timing only."""
    print("=" * 60)
    print("bach_tone_duration_extractor.py - MusicXML Timing Mode")
    print("=" * 60)
    print(f"\n🎯 Using MusicXML timing (fixed tempo: {TEMPO_BPM} BPM)")
    print(f"\n📁 Input files:")
    print(f"   • Piccolo MusicXML: {INPUT_MUSICXML}")
    print(f"   • Pillar MusicXML:  {INPUT_MUSICXML_PILLARS}")

    # Check if files exist
    if not os.path.exists(INPUT_MUSICXML):
        print(f"\n❌ Piccolo MusicXML file not found: {INPUT_MUSICXML}")
        return None

    if not os.path.exists(INPUT_MUSICXML_PILLARS):
        print(f"\n❌ Pillar MusicXML file not found: {INPUT_MUSICXML_PILLARS}")
        print("   Continuing without pillar calibration data...")
        pillar_notes = None
        pillar_windows = None
        pillar_total_duration = 0
    else:
        # Extract pillar notes from quad piano MusicXML
        pillar_notes, pillar_windows, pillar_total_duration = extract_pillar_notes(INPUT_MUSICXML_PILLARS)

    # Parse piccolo MusicXML
    xml_notes, xml_duration_q, time_sig = parse_piccolo_musicxml(INPUT_MUSICXML)
    if xml_notes is None:
        return None

    # Create timeline from MusicXML only (no audio alignment)
    notes_timeline, beat_unit_ms, tempo = create_timeline_from_musicxml(xml_notes, time_sig)

    if notes_timeline is None:
        return None

    # Calculate final duration
    if notes_timeline:
        last_note = notes_timeline[-1]
        total_duration_ms = last_note["ms_offset"] + last_note["duration_ms"]
    else:
        total_duration_ms = 0

    # Build manifest
    flute_full_url = f"{WIX_AUDIO_BASE}{FLUTE_URL}"
    test_pulse_full_url = f"{WIX_AUDIO_BASE}{TEST_PULSE_URL}" if TEST_PULSE_URL else None
    pillar_full_urls = {pos: f"{WIX_AUDIO_BASE}{fn}" for pos, fn in PILLAR_URLS.items()}

    manifest = {
        "metadata": {
            "source_musicxml": os.path.basename(INPUT_MUSICXML),
            "source_musicxml_pillars": os.path.basename(INPUT_MUSICXML_PILLARS) if INPUT_MUSICXML_PILLARS else None,
            "source_audio_url": flute_full_url,
            "test_pulse_url": test_pulse_full_url,
            **{f"source_audio_{pos}": url for pos, url in pillar_full_urls.items()},
            "tempo": TEMPO_BPM,
            "time_signature": f"{time_sig[0]}/{time_sig[1]}",
            "total_duration_ms": round(total_duration_ms, 1),
            "total_notes": len(notes_timeline),
            "total_measures": notes_timeline[-1]["measure"] if notes_timeline else 0,
            "generated_by": "bach_tone_duration_extractor.py (MusicXML timing only)",
            "generated_at": datetime.now().isoformat(),
            "asset_version": "1.0",
            "asset_description": "Perfect timing from MusicXML at 70 BPM - no audio alignment",
            "analysis_params": {
                "method": "musicxml_only",
                "tempo": TEMPO_BPM,
                "beat_unit_ms": round(beat_unit_ms, 2),
            },
        },
        "piano_notes": notes_timeline,
        "assets": {
            "flute": flute_full_url,
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

    # Add pillar notes and windows if extracted
    if pillar_notes:
        manifest["pillar_notes"] = pillar_notes
        manifest["metadata"]["pillar_cal_windows"] = pillar_windows
        manifest["metadata"]["pillar_total_duration_ms"] = round(pillar_total_duration, 1)
        manifest["metadata"]["pillar_notes_count"] = len(pillar_notes)
        print(f"\n✅ Added {len(pillar_notes)} pillar notes to manifest")

    # Save manifest
    manifest_path = os.path.join(os.getcwd(), "tuning_mode.json")
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    # Report
    print(f"\n✅ Manifest saved: {manifest_path}")
    print(f"\n📊 Statistics:")
    print(f"   • Piccolo notes: {len(notes_timeline)} (from MusicXML)")
    if pillar_notes:
        print(f"   • Pillar notes:  {len(pillar_notes)}")
    print(f"   • Tempo: {TEMPO_BPM} BPM (fixed)")
    print(f"   • Time signature: {time_sig[0]}/{time_sig[1]}")
    print(f"   • Beat unit: {beat_unit_ms:.2f}ms")
    print(f"   • Total duration: {total_duration_ms / 1000:.2f}s")

    print(f"\n📦 Assets:")
    print(f"   • Flute:      {flute_full_url}")
    if test_pulse_full_url:
        print(f"   • Test pulse: {test_pulse_full_url}")
    for pos, url in pillar_full_urls.items():
        print(f"   • Pillar {pos}:  {url}")

    print(f"\n📋 First 5 piccolo notes:")
    for n in notes_timeline[:5]:
        print(f"   {n['index']:3d}  {n['pitch']:6s}  "
              f"offset={n['ms_offset']:8.1f}ms  dur={n['duration_ms']:.1f}ms  "
              f"pulse={n['pulse_ms']:6d}ms")

    if pillar_notes:
        print(f"\n📋 First 5 pillar notes:")
        for n in pillar_notes[:5]:
            print(f"   {n['pillar_id']:12s}  {n['pitch']:6s}  "
                  f"offset={n['ms_offset']:8.1f}ms  dur={n['duration_ms']:.1f}ms")

        print(f"\n🎯 Pillar Calibration Windows (2 measures each):")
        for w in pillar_windows:
            print(f"   {w['pillar_id']:12s}: {w['start_ms']:.1f}ms → {w['end_ms']:.1f}ms")

    print(f"\n🎯 Next steps:")
    print(f"   1. Upload tuning_mode.json to Firebase using admin.html")
    print()
    print("✅ The resulting tuning_mode.json contains perfect timing from MusicXML")
    print("   - No audio alignment needed - devices will play at 70 BPM")
    print("   - Includes pillar note data for Phase 1 mic delay calibration")

    return manifest_path


# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    create_hybrid_manifest()