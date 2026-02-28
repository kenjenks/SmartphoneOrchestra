# Smartphone Orchestra (The Texture Machine)

A decentralized, spatialized audio-visual engine designed to transform an audience's smartphones into a high-density, distributed speaker array for live performance. 

Instead of traditional surround sound coming *at* an audience, this system allows sound to breathe *through* the audience. By treating each patron's device as a unique coordinate in a "Supercomputer Grid," the Conductor can trigger localized audio textures, moving sound waves, and synchronized light patterns that travel physically across the room.

## 🚀 Technical Core
* **Precision Synchronization:** Implements a custom NTP-lite protocol to maintain a unified Master Clock across hundreds of heterogeneous devices, ensuring sample-accurate triggers within a 10-20ms window.
* **Autonomous Acoustic Mapping:** Uses four corner "Anchor" tablets (Samsung A9s) that employ a "Cocktail Party" heuristic to negotiate audible frequencies (1kHz–4kHz) for real-time XY seat triangulation.
* **Hybrid Audio Pipeline:** Features a tiered Web Audio API engine that applies 350Hz high-pass filtering to patron phones to protect hardware, while enabling full-range sub-bass and quadraphonic support on Anchor Pillars.
* **Real-time Command & Control:** Built on Firebase Realtime Database for low-latency state management, allowing a single Master Conductor to orchestrate global "Cues" across the entire mesh.

## 🛠 Hardware & Software Stack
* **Frontend:** HTML5, CSS3, JavaScript (ES6+)
* **Audio:** Web Audio API (Spatial Panning, Oscillators, AnalyserNode)
* **Backend:** Firebase Realtime Database & Hosting
* **Tools:** PyCharm, Git, GitHub Actions (CI/CD)

## 📂 Project Structure
* `laptopdist.html`: The "Conductor" interface for triggering cues.
* `devicedist.html`: The "Performer" app for patron smartphones.
* `seat_survey_beacon.html`: Autonomous acoustic anchors for room calibration.
* `seat_survey.html`: The crew-facing tool for mapping seat coordinates.
* `admin.html`: System-wide reset and database maintenance utility.
* `pillar_config.json`: Master coordinate map for the theater anchors.

## 📖 Operational Workflow
1. **Calibration:** Deploy A9 Tablets at theater corners running `seat_survey_beacon.html`.
2. **Survey:** Map the house using `seat_survey.html` to generate the `seat_config.tsv`.
3. **Onboarding:** Patrons scan QR codes at Activation Stations to download local assets and register their seat ID.
4. **Performance:** The Conductor triggers JSON-based cues that propagate through the audience mesh in real-time.

---
*Developed for high-density spatialized performance environments.*
