"""
bach_tone_duration_extractor.py

Uses both MusicXML (for perfect note structure) and MP3 (for performance timing)
to create the most accurate tuning_mode.json possible.

This version:
- Parses MusicXML to get exact note sequence (pitches, durations)
- Loads MP3 and detects onsets for performance timing
- Aligns each MusicXML note to an audio onset
- EXTRACTS PILLAR NOTES from quad piano MusicXML for mic delay calibration
- Result: Perfect notes with performance timing, no warbles/modulations

Also parses piano notes to generate quad piano note timing for tuning Phase 1, where the devices listen to the pillars for mic delay cal.

REQUIREMENTS
------------
pip install librosa music21 numpy
"""

import os
import json
import numpy as np
import librosa
import music21
from datetime import datetime

# ---------------------------------------------------------------------------
# CONFIGURATION — UPDATE THESE BEFORE RUNNING
# ---------------------------------------------------------------------------

# Base directory
BASE_DIR = r"C:\\Users\\ken\\Documents\\Ken\\Fiction\\HMP_SW\\Script_Analysis_and_Production\\SmartphoneOrchestra stuff\\Jesu Joy"

# Input files
INPUT_MP3 = os.path.join(BASE_DIR, "Tuning-Jesu - Stacatto-Piccolo_in_C.mp3")
INPUT_MUSICXML = os.path.join(BASE_DIR, "Tuning-Jesu - Stacatto - Piccolo.musicxml")
INPUT_MUSICXML_PILLARS = os.path.join(BASE_DIR, "Tuning-Jesu - Stacatto - quad piano.musicxml")  # NEW: Quad piano file

# --- WIX CDN ASSET URLS ---
WIX_AUDIO_BASE = "https://static.wixstatic.com/mp3/"

# Update these with your actual uploaded filenames
FLUTE_URL      = "9178d1_15f2dad109ba41678cc46d01123b1dc5.mp3"  # Replace with actual filename after uploading
TEST_PULSE_URL = "9178d1_63e58ac50ec7434eba6785cd3291c227.mp3"

PILLAR_URLS = {
    "FL": "9178d1_9aba02e74fc344808064f364bb505086.mp3",
    "FR": "9178d1_08a292cbcee448d7b83e464d6a94b377.mp3",
    "BL": "9178d1_8987080abdb14616a0e9562387923614.mp3",
    "BR": "9178d1_8b53f52bbce6406ebacae8288777bad5.mp3",
}

# ---------------------------------------------------------------------------
# ANALYSIS PARAMETERS
# ---------------------------------------------------------------------------

# Audio sample rate
SR = 22050

# Hop length for onset detection (higher = faster but less precise)
HOP_LENGTH = 512

# Onset detection parameters
ONSET_THRESHOLD = 0.05      # Lower = more sensitive
ONSET_BACKTRACK = True       # Backtrack to nearest local minimum

# Minimum gap between notes (if less than this, merge them)
MIN_GAP_MS = 30

# Frequency range for piccolo (C5 to C7)
FMIN = librosa.note_to_hz("C5")      # ~523 Hz
FMAX = librosa.note_to_hz("C7")      # ~2093 Hz

# Tempo from MusicXML (70 BPM for Jesu)
TEMPO_BPM = 70

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

def note_name_to_midi(note_name: str) -> int:
    """Convert a note name (e.g., 'C5') to MIDI number."""
    return music21.pitch.Pitch(note_name).midi

# ---------------------------------------------------------------------------
# PARSE MUSICXML (PICCOLO)
# ---------------------------------------------------------------------------

def parse_musicxml(xml_path: str) -> tuple:
    """
    Extract perfect note sequence from MusicXML.
    Returns:
        - notes: list of dicts with pitch, duration_q, offset_q
        - total_quarters: total duration in quarter notes
        - time_signature: tuple of (numerator, denominator)
    """
    print(f"📄 Loading MusicXML: {xml_path}")

    try:
        score = music21.converter.parse(xml_path)
    except Exception as e:
        print(f"❌ Error parsing MusicXML: {e}")
        return None, None, None

    # Get the first part (assuming it's the piccolo)
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
        time_sig = (4, 4)  # Default to 4/4
        print("   ⚠️ No time signature found, assuming 4/4")

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
# EXTRACT PILLAR NOTES FROM QUAD PIANO MUSICXML (NEW)
# ---------------------------------------------------------------------------

def extract_pillar_notes(xml_path: str) -> tuple:
    """
    Extract pillar notes from quad piano MusicXML.
    Maps parts: P7→PILLAR_FL, P8→PILLAR_FR, P10→PILLAR_BR, P9→PILLAR_BL

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

    # Map part IDs to pillar IDs (clockwise: FL, FR, BR, BL)
    # Based on your MusicXML: P7=FL, P8=FR, P10=BR, P9=BL
    PART_TO_PILLAR = {
        "P7": "PILLAR_FL",
        "P8": "PILLAR_FR",
        "P10": "PILLAR_BR",
        "P9": "PILLAR_BL"
    }

    # Also track order for windows (clockwise)
    PILLAR_ORDER = ["PILLAR_FL", "PILLAR_FR", "PILLAR_BR", "PILLAR_BL"]

    # Get time signature (should be 3/4 for Jesu)
    time_sig = None
    for part in score.parts:
        for ts in part.flatten().getElementsByClass(music21.meter.TimeSignature):
            time_sig = (ts.numerator, ts.denominator)
            break
        if time_sig:
            break

    if not time_sig:
        time_sig = (3, 4)  # Jesu is in 3/4
        print("   ⚠️ No time signature found, assuming 3/4")

    print(f"   Time signature: {time_sig[0]}/{time_sig[1]}")

    # Calculate beat duration in ms
    quarter_duration_ms = 60000 / TEMPO_BPM
    beat_duration_ms = quarter_duration_ms * (4 / time_sig[1])  # Adjust for time signature

    # Collect all pillar notes
    all_pillar_notes = []
    pillar_windows = []

    # Track cumulative offset for window calculation
    total_duration_q = 0

    for part in score.parts:
        part_id = part.id
        if part_id not in PART_TO_PILLAR:
            print(f"   ⚠️ Skipping part {part_id} - not mapped to a pillar")
            continue

        pillar_id = PART_TO_PILLAR[part_id]
        print(f"\n   Processing {pillar_id} (part {part_id})...")

        notes_in_part = []
        current_offset_q = 0.0
        measure_count = 0
        last_measure = 0

        for element in part.flatten().notesAndRests:
            if element.isNote:
                # Get measure number
                measure = element.measureNumber
                if measure != last_measure:
                    measure_count += 1
                    last_measure = measure

                # Get pitch
                pitch = element.pitch.nameWithOctave
                midi = element.pitch.midi

                # Duration in quarter notes
                duration_q = element.duration.quarterLength

                # Calculate time in ms
                ms_offset = current_offset_q * quarter_duration_ms
                duration_ms = duration_q * quarter_duration_ms
                pulse_ms = ms_offset + (duration_ms / 2)

                notes_in_part.append({
                    "pillar_id": pillar_id,
                    "index": len(all_pillar_notes),
                    "measure": measure,
                    "ms_offset": round(ms_offset, 1),
                    "duration_ms": round(duration_ms, 1),
                    "pitch": pitch,
                    "midi": midi,
                    "pulse_ms": round(pulse_ms, 1)
                })

                all_pillar_notes.append(notes_in_part[-1])

            current_offset_q += element.duration.quarterLength

        # Track total duration for this part
        part_duration_q = current_offset_q
        if part_duration_q > total_duration_q:
            total_duration_q = part_duration_q

        print(f"      Extracted {len(notes_in_part)} notes")
        if notes_in_part:
            print(f"      First note: {notes_in_part[0]['pitch']} at {notes_in_part[0]['ms_offset']:.1f}ms")
            print(f"      Last note: {notes_in_part[-1]['pitch']} at {notes_in_part[-1]['ms_offset']:.1f}ms")

    # Calculate total duration in ms
    total_duration_ms = total_duration_q * quarter_duration_ms

    # Create calibration windows (2 measures each, using the notes we extracted)
    # Find start and end times for each pillar (first 2 measures of each part)
    for pillar_id in PILLAR_ORDER:
        pillar_notes = [n for n in all_pillar_notes if n["pillar_id"] == pillar_id]
        if not pillar_notes:
            print(f"   ⚠️ No notes found for {pillar_id}")
            continue

        # Find notes in measure 1 and 2
        window_notes = [n for n in pillar_notes if n["measure"] <= 2]
        if window_notes:
            start_ms = min(n["ms_offset"] for n in window_notes)
            end_ms = max(n["ms_offset"] + n["duration_ms"] for n in window_notes)
        else:
            # Fallback: use first few notes
            start_ms = pillar_notes[0]["ms_offset"]
            end_ms = pillar_notes[min(4, len(pillar_notes)-1)]["ms_offset"] + 500

        pillar_windows.append({
            "pillar_id": pillar_id,
            "start_ms": round(start_ms, 1),
            "end_ms": round(end_ms, 1)
        })

        print(f"\n   {pillar_id} calibration window: {start_ms:.1f}ms → {end_ms:.1f}ms")

    print(f"\n✅ Extracted {len(all_pillar_notes)} total pillar notes")
    print(f"   Total duration: {total_duration_ms/1000:.2f}s")

    return all_pillar_notes, pillar_windows, total_duration_ms

# ---------------------------------------------------------------------------
# AUDIO ONSET DETECTION
# ---------------------------------------------------------------------------

def detect_audio_onsets(mp3_path: str) -> tuple:
    """
    Get performance timings from audio.
    Returns:
        - onset_times: list of onset times in seconds
        - audio_duration: total duration in seconds
        - onset_env: onset strength envelope (for debugging)
    """
    print(f"\n🎵 Loading audio: {mp3_path}")

    try:
        y, sr = librosa.load(mp3_path, sr=SR, mono=True)
    except Exception as e:
        print(f"❌ Error loading audio: {e}")
        return None, None, None

    audio_duration = len(y) / sr
    print(f"   Duration: {audio_duration:.2f}s, SR: {sr} Hz")

    print("   Computing onset strength...")

    # Compute onset strength
    onset_env = librosa.onset.onset_strength(
        y=y,
        sr=sr,
        hop_length=HOP_LENGTH,
        aggregate=np.median,
        fmin=FMIN,
        fmax=FMAX
    )

    print("   Detecting onsets...")

    # Use peak picking with explicit threshold
    onset_frames = librosa.util.peak_pick(
        onset_env,
        pre_max=3,
        post_max=3,
        pre_avg=5,
        post_avg=5,
        delta=ONSET_THRESHOLD,
        wait=10
    )

    onset_times = librosa.frames_to_time(onset_frames, sr=sr, hop_length=HOP_LENGTH)

    print(f"   Detected {len(onset_times)} onsets in audio")

    # Show first few onsets
    if len(onset_times) > 0:
        print("\n   First few onsets (seconds):")
        for i, t in enumerate(onset_times[:5]):
            print(f"     {i}: {t:.3f}s")

    return onset_times, audio_duration, onset_env

# ---------------------------------------------------------------------------
# HYBRID ALIGNMENT
# ---------------------------------------------------------------------------

def align_notes(xml_notes, onset_times, audio_duration, time_sig):
    """
    Match MusicXML notes to audio onsets.
    Returns timeline of notes with performance timing.
    """
    if xml_notes is None or onset_times is None:
        return None, 0, 0

    print(f"\n🔄 Aligning {len(xml_notes)} MusicXML notes with {len(onset_times)} audio onsets...")

    # Check if counts match reasonably
    if abs(len(xml_notes) - len(onset_times)) > 5:
        print(f"⚠️  Large mismatch: MusicXML={len(xml_notes)}, Audio={len(onset_times)}")
        print("   This might indicate different arrangements or pickup notes.")
        print("   Using the smaller count and hoping for the best.")

        # Use the smaller count and warn
        n = min(len(xml_notes), len(onset_times))
        xml_notes = xml_notes[:n]
        onset_times = onset_times[:n]
        print(f"   Using first {n} notes")

    # Calculate tempo from inter-onset intervals
    if len(onset_times) > 1:
        # Use first 10 onsets to estimate tempo
        n_for_tempo = min(10, len(onset_times) - 1)
        iois = np.diff(onset_times[:n_for_tempo + 1]) * 1000
        beat_unit_ms = np.median(iois)
        tempo = 60000 / beat_unit_ms

        # Adjust for time signature
        if time_sig[1] == 4:  # Quarter note beat
            beats_per_measure = time_sig[0]
        else:
            beats_per_measure = time_sig[0] * (4 / time_sig[1])
    else:
        beat_unit_ms = 500
        tempo = 120
        beats_per_measure = time_sig[0]

    print(f"\n📊 Estimated tempo: {tempo:.1f} BPM")
    print(f"   Beat unit: {beat_unit_ms:.1f}ms")

    # Build timeline
    timeline = []

    for i, (xml_note, onset) in enumerate(zip(xml_notes, onset_times)):
        onset_ms = onset * 1000

        # Estimate duration from next onset or end of piece
        if i < len(onset_times) - 1:
            next_onset = onset_times[i + 1] * 1000
            duration_ms = next_onset - onset_ms
        else:
            duration_ms = (audio_duration - onset) * 1000

        # Ensure duration is reasonable (between 30ms and 2s)
        duration_ms = max(30, min(duration_ms, 2000))

        # Calculate pulse at center of note
        pulse_ms = int(round(onset_ms + duration_ms / 2))

        # Calculate measure number (approximate)
        measure = int(onset_ms / (beat_unit_ms * beats_per_measure)) + 1

        timeline.append({
            "id": f"m{measure}_n{i}",
            "index": i,
            "measure": measure,
            "ms_offset": round(onset_ms, 1),
            "duration_ms": round(duration_ms, 1),
            "pulse_ms": pulse_ms,
            "pitch": xml_note['pitch'],
            "midi": xml_note['midi'],
            "has_fsk": False,  # No FSK encoding in this version
        })

    # Verify alignment
    print(f"\n✅ Created timeline with {len(timeline)} notes")

    # Show first few notes
    print("\n   First few aligned notes:")
    for i, note in enumerate(timeline[:5]):
        print(f"     {i}: {note['pitch']} at {note['ms_offset']:.1f}ms, duration {note['duration_ms']:.1f}ms")

    return timeline, beat_unit_ms, tempo

# ---------------------------------------------------------------------------
# MAIN PIPELINE
# ---------------------------------------------------------------------------

def create_hybrid_manifest():
    """Main pipeline: combine MusicXML and audio."""
    print("=" * 60)
    print("bach_tone_duration_extractor.py - Hybrid Mode")
    print("=" * 60)
    print(f"\n🎯 Using both MusicXML and MP3 for perfect note timing")
    print(f"\n📁 Input files:")
    print(f"   • Piccolo MP3:      {INPUT_MP3}")
    print(f"   • Piccolo MusicXML: {INPUT_MUSICXML}")
    print(f"   • Pillar MusicXML:  {INPUT_MUSICXML_PILLARS}")

    # Check if files exist
    if not os.path.exists(INPUT_MP3):
        print(f"\n❌ MP3 file not found: {INPUT_MP3}")
        return None

    if not os.path.exists(INPUT_MUSICXML):
        print(f"\n❌ Piccolo MusicXML file not found: {INPUT_MUSICXML}")
        print("   Falling back to audio-only mode...")
        return None

    if not os.path.exists(INPUT_MUSICXML_PILLARS):
        print(f"\n❌ Pillar MusicXML file not found: {INPUT_MUSICXML_PILLARS}")
        print("   Continuing without pillar calibration data...")
        pillar_notes = None
        pillar_windows = None
        pillar_total_duration = 0
    else:
        # 1b. Extract pillar notes from quad piano MusicXML
        pillar_notes, pillar_windows, pillar_total_duration = extract_pillar_notes(INPUT_MUSICXML_PILLARS)

    # 1a. Parse piccolo MusicXML for perfect note structure
    xml_notes, xml_duration_q, time_sig = parse_musicxml(INPUT_MUSICXML)
    if xml_notes is None:
        return None

    # 2. Detect audio onsets for performance timing
    onset_times, audio_duration, onset_env = detect_audio_onsets(INPUT_MP3)
    if onset_times is None:
        return None

    # 3. Align them
    notes_timeline, beat_unit_ms, tempo = align_notes(
        xml_notes, onset_times, audio_duration, time_sig
    )

    if notes_timeline is None:
        return None

    # 4. Calculate final duration
    if notes_timeline:
        last_note = notes_timeline[-1]
        total_duration_ms = last_note["ms_offset"] + last_note["duration_ms"]
    else:
        total_duration_ms = audio_duration * 1000

    # 5. Build manifest
    flute_full_url = f"{WIX_AUDIO_BASE}{FLUTE_URL}"
    test_pulse_full_url = f"{WIX_AUDIO_BASE}{TEST_PULSE_URL}" if TEST_PULSE_URL else None
    pillar_full_urls = {pos: f"{WIX_AUDIO_BASE}{fn}" for pos, fn in PILLAR_URLS.items()}

    manifest = {
        "metadata": {
            "source_audio": os.path.basename(INPUT_MP3),
            "source_musicxml": os.path.basename(INPUT_MUSICXML),
            "source_musicxml_pillars": os.path.basename(INPUT_MUSICXML_PILLARS) if INPUT_MUSICXML_PILLARS else None,
            "source_audio_url": flute_full_url,
            "test_pulse_url": test_pulse_full_url,
            **{f"source_audio_{pos}": url for pos, url in pillar_full_urls.items()},
            "tempo": round(tempo, 2),
            "time_signature": f"{time_sig[0]}/{time_sig[1]}",
            "total_duration_ms": round(total_duration_ms, 1),
            "total_notes": len(notes_timeline),
            "total_measures": notes_timeline[-1]["measure"] if notes_timeline else 0,
            "generated_by": "bach_tone_duration_extractor.py (MusicXML + audio hybrid)",
            "generated_at": datetime.now().isoformat(),
            "asset_version": "1.0",
            "asset_description": "Hybrid analysis: perfect notes from MusicXML, timing from staccato piccolo audio - NO WARBLES",
            "analysis_params": {
                "method": "hybrid_xml_audio",
                "hop_length": HOP_LENGTH,
                "onset_threshold": ONSET_THRESHOLD,
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
                "method": "hybrid_xml_audio",
                "note_source": "MusicXML",
                "timing_source": "Audio onsets",
                "philosophy": "Perfect notes with performance timing - ideal for delay calibration"
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
    print(f"   • Piccolo notes: {len(notes_timeline)}")
    if pillar_notes:
        print(f"   • Pillar notes:  {len(pillar_notes)}")
    print(f"   • Timing: from audio onsets")
    print(f"   • Tempo: {tempo:.2f} BPM")
    print(f"   • Time signature: {time_sig[0]}/{time_sig[1]}")
    print(f"   • Beat unit: {beat_unit_ms:.2f}ms")
    print(f"   • Total duration: {total_duration_ms/1000:.2f}s")

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
    print(f"   1. Upload your MP3 to Wix and update FLUTE_URL in this script")
    print(f"   2. Run this script again to regenerate tuning_mode.json")
    print(f"   3. Upload tuning_mode.json to Firebase using admin.html")
    print()
    print("✅ The resulting tuning_mode.json contains NO warbles or modulations")
    print("   - just pure, clean note timing data for accurate delay calibration.")
    print("   - Also includes pillar note data for mic delay calibration phase.")
    
    return manifest_path

# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    create_hybrid_manifest()