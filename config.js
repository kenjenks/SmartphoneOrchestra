/*
 * config.js — shared setup for every Smartphone Orchestra page.
 *
 * Load it after the Firebase scripts and before the page's own script:
 *
 *   <script src="https://www.gstatic.com/firebasejs/10.8.0/firebase-app-compat.js"></script>
 *   <script src="https://www.gstatic.com/firebasejs/10.8.0/firebase-database-compat.js"></script>
 *   <script src="https://www.gstatic.com/firebasejs/10.8.0/firebase-auth-compat.js"></script>  (crew pages only)
 *   <script src="config.js"></script>
 *
 * Then, in the page:   const db = SO.db;
 *
 * Everything lives on one global object, SO:
 *   SO.db, SO.now(), SO.serverOffset, SO.onClockSync(fn)
 *   SO.clock.sync()                          measured clock sync (startup)
 *   SO.clock.recheckBefore(startMs)          recheck right before calibration, finishing in time
 *   SO.clock.recheck()                       recheck at the end of a movement
 *   SO.requireCrew()                         sign-in gate for admin / conductor
 *   SO.loadPillarConfig(), SO.loadSeatConfig(), SO.getSeat(id), SO.loadTuningMode()
 *   SO.parseSeatTsv(text), SO.computeNeighborMap(seats)
 *   SO.validatePillarConfig(data), SO.validateSeatConfig(seats, pillarConfig)
 *   SO.audio.getBuffer(ctx, url), SO.audio.playAt(ctx, buffer, serverMs, opts), SO.audio.primeBuffer(ctx, buffer)
 *
 * Every page plays sound files through SO.audio.playAt: preloaded and decoded once, primed
 * (0.1 s at gain 0.001) before its first play, started with source.start(when) on the
 * audio clock, and joined part-way if the cue is late.
 *
 * Validators return { errors: [...], warnings: [...] }.
 * Errors mean "refuse the upload". Warnings mean "ask before uploading".
 *
 * No silent guesses. If pillar_config lacks a value, the loader fills in the
 * fallback from SO.FALLBACK (the only place those numbers live), marks it in
 * pillar.assumed, and adds a warning that pages show with SO.showWarning().
 *
 * Speed of sound, in order of preference:
 *   1. pillar_config.room_temp_c   → 331.3 + 0.606 × °C   (set it from the admin page)
 *   2. pillar_config.speed_of_sound
 *   3. SO.FALLBACK.speedOfSound, with a warning
 */
(function () {
    'use strict';

    // ---------------------------------------------------------------- Firebase
    const FIREBASE_CONFIG = {
        apiKey: "AIzaSyBnzYesqka3zruOPjEE9eJ2ty4LZjKIlIU",
        authDomain: "smartphoneorchestra-dwt.firebaseapp.com",
        databaseURL: "https://smartphoneorchestra-dwt-default-rtdb.firebaseio.com/",
        projectId: "smartphoneorchestra-dwt",
        storageBucket: "smartphoneorchestra-dwt.firebasestorage.app",
        messagingSenderId: "722537253165",
        appId: "1:722537253165:web:8e8b6495bd508de35b2ba4"
    };
    if (!firebase.apps.length) firebase.initializeApp(FIREBASE_CONFIG);
    const db = firebase.database();

    // ---------------------------------------------------------------- Limits
    const LIMITS = {
        MIN_SEAT_SPACING_M: 0.4,        // closer than this: almost certainly a units error
        NEIGHBOR_RADIUS_M: 2.0,
        MAX_NEIGHBORS: 6,
        OUTSIDE_PILLARS_WARN_M: 0.25,   // further outside the pillar outline than this: warn
        OUTSIDE_PILLARS_ERROR_M: 5.0,   // further than this: refuse (wrong units or wrong room)
        PILLAR_ROW_MISMATCH_M: 0.25,    // PILLAR_ rows in the TSV vs pillar_config
        SPEAKER_DELAY_MS: [0, 500],
        MIC_DELAY_MS: [0, 200],
        SPEED_OF_SOUND: [320, 360],
        MIN_PILLAR_SPAN_M: 3.0          // pillars closer together than this: units error
    };
    const BAD_KEY = /[.$#\[\]\/]/;      // characters Firebase does not allow in keys

    // Used only when pillar_config lacks a value, and always announced with a warning.
    const FALLBACK = Object.freeze({ speakerDelayMs: 45, micDelayMs: 18, speedOfSound: 343 });
    const ROOM_TEMP_C = [0, 45];
    const speedFromTemp = c => 331.3 + 0.606 * c;

    // ---------------------------------------------------------------- Clock
    // SO.now() is server time in ms, read from the browser's monotonic clock (performance.now)
    // plus an offset, so it never jumps when the phone corrects its own clock.
    //
    // The offset starts from Firebase's rough estimate. SO.clock.sync() replaces it with a
    // measured one from a few write-and-read round trips to the server (see clockSync).
    // After the first measured sync, Firebase's estimate is ignored (it changes on reconnects).
    // SO.clock.recheck() re-measures at quiet moments (end of a song, start of a movement);
    // sounds already scheduled are not affected, because they were placed on the audio clock.
    const perfNow = () => performance.timeOrigin + performance.now();
    let clockOffset = 0;            // server time − perfNow()
    let clockPrecise = false;
    let clockSynced = false;
    let clockDeviceId = null;
    let lastClockSync = null;       // { at, rttMs, uncertaintyMs, changeMs, samples }
    let clockSyncing = null;
    const clockListeners = [];
    const CLOCK = {
        SAMPLES: 6,                 // round trips per sync; their ranges are intersected
        SAMPLE_TIMEOUT_MS: 2000,    // give up on one round trip after this
        RECHECK_MIN_INTERVAL_MS: 60000,
        RECHECK_JITTER_MS: 1500     // spread phones out so 500 of them don't sync in the same instant
    };
    function notifyClock() {
        const legacy = now() - Date.now();   // for pages that still print serverOffset
        clockListeners.forEach(fn => { try { fn(legacy, lastClockSync); } catch (e) { console.error(e); } });
    }
    db.ref('.info/serverTimeOffset').on('value', snap => {
        if (clockPrecise) return;
        clockOffset = (snap.val() || 0) + (Date.now() - perfNow());
        clockSynced = true;
        notifyClock();
    });
    function now() { return perfNow() + clockOffset; }
    function onClockSync(fn) {
        clockListeners.push(fn);
        if (clockSynced) fn(now() - Date.now(), lastClockSync);
    }

    // One measured sync. Each sample writes the server's own timestamp and reads it back.
    // The server stamped it somewhere between sending (t0) and the acknowledgement (t1), so the
    // true offset lies in [stamp − t1, stamp − t0]. Intersecting those ranges over all samples
    // gives a range that every sample agrees on.
    //   First sync:  offset = middle of that range.
    //   Later syncs: keep the current offset if it is still inside the range (so measurement
    //                noise never moves the clock, and calibration stays valid); otherwise move
    //                it just far enough to reach the range (real drift is corrected).
    function clockSync({ samples = CLOCK.SAMPLES, deviceId, deadline } = {}) {
        if (clockSyncing) return clockSyncing;
        const id = deviceId || clockDeviceId || 'unnamed';
        const ref = db.ref('clock_sync/' + id);
        clockSyncing = (async () => {
            let best = null, got = 0, lo = -Infinity, hi = Infinity;
            const within = (p, ms) => Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), ms))]);
            for (let i = 0; i < samples; i++) {
                // With a deadline (perfNow ms), stop taking round trips in time and use what we have
                const left = deadline ? deadline - perfNow() : Infinity;
                if (left < 60) break;
                const limit = Math.min(CLOCK.SAMPLE_TIMEOUT_MS, left);
                try {
                    const t0 = perfNow();
                    await within(ref.set(firebase.database.ServerValue.TIMESTAMP), limit);
                    const t1 = perfNow();
                    const stamp = (await within(ref.get(), Math.max(30, (deadline ? deadline - perfNow() : limit)))).val();
                    if (typeof stamp !== 'number') continue;
                    got++;
                    lo = Math.max(lo, stamp - t1);
                    hi = Math.min(hi, stamp - t0);
                    const s = { rttMs: t1 - t0, offset: stamp - (t0 + t1) / 2 };
                    if (!best || s.rttMs < best.rttMs) best = s;
                } catch (e) { /* one failed sample is fine */ }
            }
            if (!best) throw new Error('clock sync: no round trips succeeded');
            if (lo > hi) {   // samples disagree (e.g. the clock was stepped mid-sync): use the fastest one
                lo = best.offset - best.rttMs / 2;
                hi = best.offset + best.rttMs / 2;
            }
            const before = clockOffset;
            if (!clockPrecise) clockOffset = (lo + hi) / 2;
            else clockOffset = Math.min(hi, Math.max(lo, clockOffset));   // move only if outside the range
            const changeMs = clockPrecise ? clockOffset - before : null;
            clockPrecise = clockSynced = true;
            lastClockSync = {
                at: now(), rttMs: Math.round(best.rttMs), uncertaintyMs: Math.round((hi - lo) / 2 * 10) / 10,
                changeMs: changeMs === null ? null : Math.round(changeMs * 10) / 10, samples: got
            };
            if (clockDeviceId) db.ref(`devices/${clockDeviceId}/clock`).set(lastClockSync).catch(() => {});
            notifyClock();
            return lastClockSync;
        })().finally(() => { clockSyncing = null; });
        return clockSyncing;
    }

    // Recheck just before a timed event such as calibration, which starts at server time startMs.
    // Finishes at least reserveMs before it, taking as many round trips as fit; skips if there is
    // no time. Not limited to once a minute.
    async function clockRecheckBefore(startMs, { reserveMs = 1500, jitterMs = 500 } = {}) {
        const budget = startMs - now() - reserveMs;
        if (budget < 400) return null;
        const wait = Math.random() * Math.min(jitterMs, budget / 3);
        await new Promise(r => setTimeout(r, wait));
        if (clockSyncing) return null;   // another sync is running; don't risk overrunning the start
        return clockSync({ deadline: perfNow() + (budget - wait) - 100 });   // 100 ms safety margin
    }

    // Re-measure at a quiet moment. Does nothing if the last sync was under a minute ago.
    // Waits a random 0–1.5 s first so a full house doesn't hit the database at once.
    async function clockRecheck({ minIntervalMs = CLOCK.RECHECK_MIN_INTERVAL_MS, jitterMs = CLOCK.RECHECK_JITTER_MS } = {}) {
        if (lastClockSync && now() - lastClockSync.at < minIntervalMs) return null;
        await new Promise(r => setTimeout(r, Math.random() * jitterMs));
        return clockSync();
    }

    // ---------------------------------------------------------------- Geometry
    const isNum = v => typeof v === 'number' && isFinite(v);
    const dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
    const isPillarId = id => typeof id === 'string' && id.startsWith('PILLAR_');

    // Pillars as a polygon, ordered by angle around their centre (so the file's order doesn't matter).
    function pillarPolygon(pillars) {
        const pts = pillars.filter(p => isNum(p.x) && isNum(p.y));
        if (pts.length < 3) return null;
        const cx = pts.reduce((s, p) => s + p.x, 0) / pts.length;
        const cy = pts.reduce((s, p) => s + p.y, 0) / pts.length;
        return pts.map(p => ({ x: p.x, y: p.y, a: Math.atan2(p.y - cy, p.x - cx) }))
                  .sort((p, q) => p.a - q.a);
    }
    function pointInPolygon(pt, poly) {
        let inside = false;
        for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
            const a = poly[i], b = poly[j];
            if ((a.y > pt.y) !== (b.y > pt.y) &&
                pt.x < (b.x - a.x) * (pt.y - a.y) / (b.y - a.y) + a.x) inside = !inside;
        }
        return inside;
    }
    function distToSegment(p, a, b) {
        const dx = b.x - a.x, dy = b.y - a.y;
        const len2 = dx * dx + dy * dy;
        const t = len2 ? Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2)) : 0;
        return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
    }
    // 0 if inside the pillar outline, otherwise metres outside it.
    function distanceOutside(pt, poly) {
        if (pointInPolygon(pt, poly)) return 0;
        let d = Infinity;
        for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) d = Math.min(d, distToSegment(pt, poly[i], poly[j]));
        return d;
    }

    // ---------------------------------------------------------------- Pillar config
    function validatePillarConfig(data) {
        const errors = [], warnings = [];
        if (!data || !Array.isArray(data.pillars) || data.pillars.length === 0) {
            errors.push("No 'pillars' list found.");
            return { errors, warnings };
        }
        const seen = new Set();
        data.pillars.forEach((p, i) => {
            const name = (p && p.id) || `pillar #${i + 1}`;
            if (!p || typeof p.id !== 'string' || !p.id) { errors.push(`${name}: missing id.`); return; }
            if (!isPillarId(p.id)) errors.push(`${name}: id must start with PILLAR_.`);
            if (BAD_KEY.test(p.id)) errors.push(`${name}: id contains . $ # [ ] or /, which Firebase does not allow.`);
            if (seen.has(p.id)) errors.push(`${name}: listed twice.`);
            seen.add(p.id);
            if (!isNum(p.x) || !isNum(p.y)) errors.push(`${name}: x and y must be numbers (metres).`);
            if (typeof p.audioUrl !== 'string' || !/^https:\/\//.test(p.audioUrl))
                errors.push(`${name}: missing audioUrl (ePillar cannot activate without it).`);
            if (p.speakerDelayMs === undefined) warnings.push(`${name}: no speakerDelayMs. Pages will fall back to a guess (${FALLBACK.speakerDelayMs} ms).`);
            else if (!isNum(p.speakerDelayMs) || p.speakerDelayMs < LIMITS.SPEAKER_DELAY_MS[0] || p.speakerDelayMs > LIMITS.SPEAKER_DELAY_MS[1])
                errors.push(`${name}: speakerDelayMs ${p.speakerDelayMs} is outside ${LIMITS.SPEAKER_DELAY_MS.join('–')} ms.`);
            if (p.micDelayMs === undefined) warnings.push(`${name}: no micDelayMs. Pages will fall back to a guess (${FALLBACK.micDelayMs} ms).`);
            else if (!isNum(p.micDelayMs) || p.micDelayMs < LIMITS.MIC_DELAY_MS[0] || p.micDelayMs > LIMITS.MIC_DELAY_MS[1])
                errors.push(`${name}: micDelayMs ${p.micDelayMs} is outside ${LIMITS.MIC_DELAY_MS.join('–')} ms.`);
            if (!isNum(p.preferred_freq)) warnings.push(`${name}: no preferred_freq (needed only for the seat survey beacons).`);
        });
        if (data.pillars.length !== 4) warnings.push(`Expected 4 pillars, found ${data.pillars.length}.`);
        if (data.room_temp_c !== undefined &&
            (!isNum(data.room_temp_c) || data.room_temp_c < ROOM_TEMP_C[0] || data.room_temp_c > ROOM_TEMP_C[1]))
            errors.push(`room_temp_c ${data.room_temp_c} is outside ${ROOM_TEMP_C.join('–')} °C.`);
        if (data.speed_of_sound !== undefined &&
            (!isNum(data.speed_of_sound) || data.speed_of_sound < LIMITS.SPEED_OF_SOUND[0] || data.speed_of_sound > LIMITS.SPEED_OF_SOUND[1]))
            errors.push(`speed_of_sound ${data.speed_of_sound} is outside ${LIMITS.SPEED_OF_SOUND.join('–')} m/s.`);
        if (data.room_temp_c === undefined && data.speed_of_sound === undefined)
            warnings.push(`No room_temp_c or speed_of_sound; pages will use ${FALLBACK.speedOfSound} m/s.`);

        const pts = data.pillars.filter(p => p && isNum(p.x) && isNum(p.y));
        let span = 0;
        for (let i = 0; i < pts.length; i++) for (let j = i + 1; j < pts.length; j++) span = Math.max(span, dist(pts[i], pts[j]));
        if (pts.length >= 2 && span < LIMITS.MIN_PILLAR_SPAN_M)
            errors.push(`The pillars are at most ${span.toFixed(2)} m apart. Coordinates must be in metres.`);
        return { errors, warnings };
    }

    // Resolves the speed of sound: room temperature, then speed_of_sound, then the fallback.
    function resolveSpeedOfSound(data) {
        if (data && isNum(data.room_temp_c))
            return { value: speedFromTemp(data.room_temp_c), source: `room temperature ${data.room_temp_c} °C` };
        if (data && isNum(data.speed_of_sound))
            return { value: data.speed_of_sound, source: 'speed_of_sound in pillar_config' };
        return { value: FALLBACK.speedOfSound, source: 'fallback (not set in pillar_config)' };
    }

    // Returns { pillars: {id: {...}}, list, speedOfSound, speedOfSoundSource, warnings } or null.
    // Every pillar has numeric speakerDelayMs and micDelayMs; any filled from FALLBACK are
    // named in pillar.assumed and in warnings.
    function normalizePillarConfig(data) {
        if (!data || !Array.isArray(data.pillars)) return null;
        const warnings = [];
        const pillars = {};
        const list = data.pillars.map(p => {
            const assumed = [];
            const pick = (key) => {
                if (isNum(p[key])) return p[key];
                assumed.push(key);
                warnings.push(`${p.id}: ${key} is not set in pillar_config, so ${FALLBACK[key]} ms is being assumed.`);
                return FALLBACK[key];
            };
            const n = {
                id: p.id, label: p.label || p.id, x: p.x, y: p.y,
                audioUrl: p.audioUrl || null,
                speakerDelayMs: pick('speakerDelayMs'),
                micDelayMs: pick('micDelayMs'),
                preferred_freq: isNum(p.preferred_freq) ? p.preferred_freq : null,
                stagger: isNum(p.stagger) ? p.stagger : 0,
                assumed
            };
            if (!n.audioUrl) warnings.push(`${p.id}: no audioUrl in pillar_config.`);
            pillars[p.id] = n;
            return n;
        });
        const sos = resolveSpeedOfSound(data);
        if (sos.source.startsWith('fallback'))
            warnings.push(`Speed of sound is not set in pillar_config, so ${FALLBACK.speedOfSound} m/s is being assumed. Set the room temperature on the admin page.`);
        return {
            pillars, list,
            speedOfSound: sos.value, speedOfSoundSource: sos.source,
            roomTempC: isNum(data.room_temp_c) ? data.room_temp_c : null,
            warnings
        };
    }
    async function loadPillarConfig() {
        const snap = await db.ref('pillar_config').once('value');
        const cfg = normalizePillarConfig(snap.val());
        if (cfg) cfg.warnings.forEach(w => console.warn('[pillar_config] ' + w));
        return cfg;
    }
    // Calls fn(cfg) now and whenever pillar_config changes (cfg is null if it is missing).
    function watchPillarConfig(fn) {
        db.ref('pillar_config').on('value', snap => fn(normalizePillarConfig(snap.val())));
    }

    // ---------------------------------------------------------------- Visible warnings
    // Shows messages in an orange box. With opts.container (an element or its id) the box
    // goes at the top of that element; otherwise it is pinned to the top of the window.
    // Calling again replaces the messages for that key, so pages can refresh them.
    const warningSets = {};
    function showWarning(key, messages, opts = {}) {
        warningSets[key] = (Array.isArray(messages) ? messages : [messages]).filter(Boolean);
        const all = [].concat(...Object.values(warningSets));
        let box = document.getElementById('so-warnings');
        if (!all.length) { if (box) box.remove(); return; }
        if (!box) {
            box = document.createElement('div');
            box.id = 'so-warnings';
            const host = typeof opts.container === 'string' ? document.getElementById(opts.container) : opts.container;
            box.style.cssText = 'background:#3a2600;color:#ffcc66;border:1px solid #cc8800;border-radius:6px;' +
                'padding:8px 12px;font:12px/1.4 monospace;text-align:left;z-index:99998;max-height:40vh;overflow:auto;' +
                (host ? 'margin:10px 0;' : 'position:fixed;top:8px;left:8px;right:8px;');
            if (host) host.insertBefore(box, host.firstChild); else document.body.appendChild(box);
        }
        box.innerHTML = '<b>⚠️ Check pillar_config</b> ' +
            '<span style="float:right;cursor:pointer" onclick="this.parentNode.remove()">✕</span>' +
            '<ul style="margin:4px 0 0 18px;padding:0">' +
            all.map(m => '<li>' + String(m).replace(/</g, '&lt;') + '</li>').join('') + '</ul>';
    }

    // ---------------------------------------------------------------- Seat config
    // Parses seat_config.tsv. Needs columns seat_id (or id), x, y; section is optional.
    function parseSeatTsv(text) {
        const errors = [];
        const lines = String(text).split(/\r?\n/).filter(l => l.trim().length > 0);
        if (lines.length < 2) return { seats: [], errors: ['The file has no data rows.'] };
        const headers = lines[0].split('\t').map(h => h.trim().toLowerCase());
        let idIdx = headers.indexOf('seat_id');
        if (idIdx === -1) idIdx = headers.indexOf('id');
        const xIdx = headers.indexOf('x'), yIdx = headers.indexOf('y');
        const sectionIdx = headers.indexOf('section');
        if (idIdx === -1 || xIdx === -1 || yIdx === -1) {
            return { seats: [], errors: [`Header must include seat_id, x and y (tab-separated). Found: ${headers.join(', ')}`] };
        }
        const seats = [];
        for (let i = 1; i < lines.length; i++) {
            const cols = lines[i].split('\t').map(c => c.trim());
            const id = cols[idIdx];
            const x = parseFloat(cols[xIdx]), y = parseFloat(cols[yIdx]);
            if (!id || !isFinite(x) || !isFinite(y)) {
                errors.push(`Line ${i + 1}: could not read seat id, x and y (${lines[i].trim()}).`);
                continue;
            }
            seats.push({ id, x, y, section: (sectionIdx !== -1 && cols[sectionIdx]) || 'Orchestra' });
        }
        return { seats, errors };
    }

    // Neighbours: up to 6 closest seats in the same section within 2 m. Pillar rows get none.
    function computeNeighborMap(seats) {
        const map = {};
        const patrons = seats.filter(s => !isPillarId(s.id));
        seats.forEach(s => { map[s.id] = []; });
        patrons.forEach(s => {
            map[s.id] = patrons
                .filter(o => o.id !== s.id && o.section === s.section)
                .map(o => ({ id: o.id, d: dist(s, o) }))
                .filter(n => n.d <= LIMITS.NEIGHBOR_RADIUS_M)
                .sort((a, b) => a.d - b.d)
                .slice(0, LIMITS.MAX_NEIGHBORS)
                .map(n => n.id);
        });
        return map;
    }

    // pillarCfg may be raw pillar_config data or the result of normalizePillarConfig; null skips pillar checks.
    function validateSeatConfig(seats, pillarCfg, neighborMap) {
        const errors = [], warnings = [];
        if (!Array.isArray(seats) || seats.length === 0) return { errors: ['No seats found.'], warnings };

        // IDs
        const seen = new Set();
        seats.forEach(s => {
            if (BAD_KEY.test(s.id)) errors.push(`Seat ${s.id}: id contains . $ # [ ] or /, which Firebase does not allow.`);
            if (seen.has(s.id)) errors.push(`Seat ${s.id} is listed twice.`);
            seen.add(s.id);
        });

        const patrons = seats.filter(s => !isPillarId(s.id));
        const pillarRows = seats.filter(s => isPillarId(s.id));

        // Spacing: catches the 10x units error
        const tooClose = [];
        for (let i = 0; i < patrons.length; i++) {
            for (let j = i + 1; j < patrons.length; j++) {
                const d = dist(patrons[i], patrons[j]);
                if (d < LIMITS.MIN_SEAT_SPACING_M) tooClose.push(`${patrons[i].id}–${patrons[j].id} ${d.toFixed(2)} m`);
            }
        }
        if (tooClose.length) {
            errors.push(`${tooClose.length} pair(s) of seats are closer than ${LIMITS.MIN_SEAT_SPACING_M} m ` +
                `(${tooClose.slice(0, 5).join('; ')}${tooClose.length > 5 ? '; …' : ''}). ` +
                `Coordinates must be in metres; a 10× scale error looks like this.`);
        }

        // Against the pillars
        const list = pillarCfg ? (pillarCfg.list || pillarCfg.pillars) : null;
        if (!Array.isArray(list) || list.length < 3) {
            warnings.push('No pillar config loaded, so seats were not checked against the pillar positions.');
        } else {
            const poly = pillarPolygon(list);
            const far = [], near = [];
            patrons.forEach(s => {
                const d = distanceOutside(s, poly);
                if (d > LIMITS.OUTSIDE_PILLARS_ERROR_M) far.push(`${s.id} (${d.toFixed(1)} m)`);
                else if (d > LIMITS.OUTSIDE_PILLARS_WARN_M) near.push(`${s.id} (${d.toFixed(2)} m)`);
            });
            if (far.length) errors.push(`${far.length} seat(s) are more than ${LIMITS.OUTSIDE_PILLARS_ERROR_M} m outside the area enclosed by the pillars: ` +
                `${far.slice(0, 8).join(', ')}${far.length > 8 ? ', …' : ''}. Check units, and that pillar_config is for this room.`);
            if (near.length) warnings.push(`${near.length} seat(s) are outside the area enclosed by the pillars: ` +
                `${near.slice(0, 8).join(', ')}${near.length > 8 ? ', …' : ''}. Spatial effects there will be less accurate.`);

            // PILLAR_ rows in the TSV should match pillar_config
            const byId = {};
            list.forEach(p => { byId[p.id] = p; });
            pillarRows.forEach(r => {
                const p = byId[r.id];
                if (!p) warnings.push(`Seat file has a row for ${r.id}, which is not in pillar_config.`);
                else if (dist(r, p) > LIMITS.PILLAR_ROW_MISMATCH_M)
                    errors.push(`${r.id} is at (${r.x}, ${r.y}) in the seat file but (${p.x}, ${p.y}) in pillar_config.`);
            });
        }

        // Neighbours
        const nmap = neighborMap || computeNeighborMap(seats);
        const lonely = patrons.filter(s => !(nmap[s.id] && nmap[s.id].length));
        if (lonely.length) warnings.push(`${lonely.length} seat(s) have no neighbour within ${LIMITS.NEIGHBOR_RADIUS_M} m and will calibrate from the pillars only: ` +
            `${lonely.slice(0, 10).map(s => s.id).join(', ')}${lonely.length > 10 ? ', …' : ''}.`);

        return { errors, warnings };
    }

    // Accepts seats stored as an array or as an object keyed by id, and neighbour
    // entries stored as plain lists or as { neighbors: [...] }.
    function normalizeSeatConfig(data) {
        if (!data || !data.seats) return null;
        const raw = Array.isArray(data.seats) ? data.seats : Object.keys(data.seats).map(k => ({ id: k, ...data.seats[k] }));
        const seats = raw.filter(s => s && s.id).map(s => ({ id: s.id, x: s.x, y: s.y, section: s.section || 'Orchestra' }));
        const byId = {};
        seats.forEach(s => { byId[s.id] = s; });
        const neighbors = {};
        const nm = data.neighborMap || {};
        Object.keys(nm).forEach(k => {
            const e = nm[k];
            neighbors[k] = Array.isArray(e) ? e : (e && Array.isArray(e.neighbors) ? e.neighbors : []);
        });
        return { seats, byId, neighbors };
    }
    let seatCache = null;
    async function loadSeatConfig() {
        const snap = await db.ref('seat_config').once('value');
        seatCache = normalizeSeatConfig(snap.val());
        return seatCache;
    }
    async function getSeat(id) {
        if (!seatCache) await loadSeatConfig();
        return seatCache ? seatCache.byId[id] || null : null;
    }

    // ---------------------------------------------------------------- Audio playback (shared)
    // The one way every page plays a sound file at a server time:
    //   decode once  →  convert server time to the audio clock  →  source.start(when, offset)
    // No setTimeout and no <audio> element in the timing path.
    const audioBuffers = new Map();   // url -> Promise<AudioBuffer>

    // Downloads and decodes a file once per page; later calls reuse the same AudioBuffer.
    function getBuffer(ctx, url) {
        if (!audioBuffers.has(url)) {
            const p = fetch(url)
                .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status} for ${url.split('/').pop()}`); return r.arrayBuffer(); })
                .then(ab => ctx.decodeAudioData(ab));
            p.catch(() => audioBuffers.delete(url));   // a later call can retry
            audioBuffers.set(url, p);
        }
        return audioBuffers.get(url);
    }

    // Server-clock time (ms) → audio-clock time (s), sampled at this moment.
    function serverToAudioTime(ctx, serverMs) {
        return ctx.currentTime + (serverMs - now()) / 1000;
    }

    /*
     * playAt(ctx, buffer, serverMs, opts)
     * Starts the buffer at server time (serverMs − leadMs), at position offsetSec in the file.
     *   opts.leadMs       how much earlier than serverMs to start the source: the device's speaker
     *                     delay, plus its mic delay on the paths that use it (default 0)
     *   opts.offsetSec    position in the file to start from (default 0)
     *   opts.joinLate     if the start time has passed: true (default) starts now, further into the
     *                     file, so it stays in sync; false starts now at offsetSec
     *   opts.lateLimitMs  later than this, skip it (default 5000)
     *   opts.gain         gain value (default 1); opts.destination (default ctx.destination)
     *   opts.duration     seconds to play (default: to the end); opts.loop
     * Returns { source, gain, when, offset, lateMs }, or { skipped: true, reason, lateMs }.
     */
    function playAt(ctx, buffer, serverMs, opts = {}) {
        const startServer = serverMs - (opts.leadMs || 0);
        const lateMs = now() - startServer;
        const lateLimit = opts.lateLimitMs ?? 5000;
        if (lateMs > lateLimit) return { skipped: true, lateMs, reason: `${Math.round(lateMs)} ms late (limit ${lateLimit} ms)` };

        let offset = opts.offsetSec || 0, when;
        if (lateMs > 0) {
            when = ctx.currentTime;
            if (opts.joinLate !== false) offset += lateMs / 1000;
        } else {
            when = serverToAudioTime(ctx, startServer);
        }
        if (!opts.loop && offset >= buffer.duration) return { skipped: true, lateMs, reason: 'start point is past the end of the file' };

        // Mandatory: a buffer is never played before it has been primed (0.1 s at gain 0.001).
        // Priming done earlier with primeBuffer() counts, so it is not repeated.
        const primedNow = !primedSet.has(buffer);
        if (primedNow) primeBuffer(ctx, buffer, { destination: opts.destination });

        const source = ctx.createBufferSource();
        source.buffer = buffer;
        if (opts.loop) source.loop = true;
        const gain = ctx.createGain();
        gain.gain.value = opts.gain ?? 1;
        source.connect(gain);
        gain.connect(opts.destination || ctx.destination);
        if (opts.duration) source.start(when, offset, opts.duration);
        else source.start(when, offset);
        return { source, gain, when, offset, lateMs, primedNow };
    }

    // Plays the first 0.1 s of the buffer almost silently, right now, to wake the speaker path.
    // playAt() calls this automatically for any buffer not yet primed; pages may also call it
    // earlier (e.g. at activation) so the hardware is awake well before the first cue.
    const PRIME_DEFAULTS = Object.freeze({ gain: 0.001, durationSec: 0.1 });
    const primedSet = new WeakSet();
    function primeBuffer(ctx, buffer, { gain = PRIME_DEFAULTS.gain, durationSec = PRIME_DEFAULTS.durationSec, destination } = {}) {
        const s = ctx.createBufferSource();
        s.buffer = buffer;
        const g = ctx.createGain();
        g.gain.value = gain;
        s.connect(g);
        g.connect(destination || ctx.destination);
        s.start(0, 0, Math.min(durationSec, buffer.duration));
        primedSet.add(buffer);
    }
    const isPrimed = buffer => primedSet.has(buffer);

    // ---------------------------------------------------------------- Tuning mode
    async function loadTuningMode() {
        const snap = await db.ref('tuning_mode').once('value');
        return snap.val();
    }

    // ---------------------------------------------------------------- Crew sign-in
    // Only pages that change configs, cues or tuning need this (admin, conductor).
    // Needs firebase-auth-compat.js on the page.
    async function requireCrew() {
        if (!firebase.auth) throw new Error('requireCrew needs firebase-auth-compat.js on this page.');
        const auth = firebase.auth();
        await new Promise(r => { const off = auth.onAuthStateChanged(() => { off(); r(); }); });
        if (!auth.currentUser) {
            await new Promise(resolve => {
                const btn = document.createElement('button');
                btn.textContent = 'Sign in with Google';
                btn.style.cssText = 'position:fixed;top:40%;left:50%;transform:translateX(-50%);z-index:99999;' +
                    'padding:18px 30px;font-size:1.1rem;background:#00ffcc;color:#000;border:none;border-radius:8px;font-weight:bold;cursor:pointer;';
                btn.onclick = async () => {
                    try {
                        await auth.signInWithPopup(new firebase.auth.GoogleAuthProvider());
                        btn.remove();
                        resolve();
                    } catch (e) { btn.textContent = 'Sign-in failed (' + e.code + '). Click to retry.'; }
                };
                document.body.appendChild(btn);
            });
        }
        const user = auth.currentUser;
        const role = (await db.ref('roles/' + user.uid).once('value')).val();
        if (role !== 'crew') {
            const box = document.createElement('div');
            box.style.cssText = 'position:fixed;inset:0;z-index:99999;background:#000;color:#f66;padding:30px;font-family:monospace;';
            box.innerHTML = '<h2>Not authorized to change the show</h2>' +
                '<p>Signed in as ' + (user.email || 'unknown') + '</p>' +
                '<p>In the Firebase console, open Realtime Database &rarr; Data, and add this under <b>roles</b> with the value "crew":</p>' +
                '<p style="color:#0fc;font-size:1.2rem;user-select:all">' + user.uid + '</p>' +
                '<button onclick="firebase.auth().signOut().then(()=>location.reload())" style="padding:10px 20px;margin-top:10px">Sign out</button>';
            document.body.appendChild(box);
            throw new Error('Not authorized');
        }
        return user;
    }

    // ---------------------------------------------------------------- Export
    window.SO = {
        FIREBASE_CONFIG, LIMITS, FALLBACK, db,
        now, onClockSync,
        get serverOffset() { return now() - Date.now(); },   // legacy: server − Date.now()
        clock: {
            sync: clockSync, recheck: clockRecheck, recheckBefore: clockRecheckBefore,
            setDeviceId: id => { clockDeviceId = id; },
            get status() { return lastClockSync; },
            get precise() { return clockPrecise; },
            SETTINGS: CLOCK
        },
        requireCrew, showWarning,
        speedFromTemp, resolveSpeedOfSound,
        validatePillarConfig, normalizePillarConfig, loadPillarConfig, watchPillarConfig,
        parseSeatTsv, computeNeighborMap, validateSeatConfig, normalizeSeatConfig, loadSeatConfig, getSeat,
        loadTuningMode,
        audio: { getBuffer, serverToAudioTime, playAt, primeBuffer, isPrimed, PRIME_DEFAULTS },
        geometry: { dist, pillarPolygon, distanceOutside }
    };
})();
