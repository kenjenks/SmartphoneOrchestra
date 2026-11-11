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
 *   SO.spatial.reachesSeat(asset, seat), SO.spatial.gainAt(asset, seat, tSec, pathSec)
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
    let wallMinusPerf = Date.now() - perfNow();   // see checkForSleep()
    const SLEEP_JUMP_MS = 1000;
    let clockSleepJumps = 0;
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
    // performance.now() stops while an Android phone is asleep, but the wall clock keeps going.
    // So a sudden change in (wall − performance) means the phone slept: shift the offset by that
    // amount at once (accurate to a few ms) and re-measure right away.
    function checkForSleep() {
        const d = (Date.now() - perfNow()) - wallMinusPerf;
        if (Math.abs(d) < SLEEP_JUMP_MS) return;
        wallMinusPerf += d;
        clockOffset += d;   // the wall clock kept running while performance.now() was paused
        clockSleepJumps++;
        console.warn(`[clock] jump of ${Math.round(d)} ms (device slept or clock changed); re-measuring`);
        if (clockPrecise && !clockSyncing) setTimeout(() => clockSync().catch(() => {}), 0);
    }
    function now() { checkForSleep(); return perfNow() + clockOffset; }
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
        const gen = clockSyncGen;
        const run = (async () => {
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
            if (gen !== clockSyncGen) return null;   // cancelled (e.g. tuning started): don't touch the clock
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
        })();
        const p = run.finally(() => { if (clockSyncing === p) clockSyncing = null; wallMinusPerf = Date.now() - perfNow(); });
        clockSyncing = p;
        return p;
    }

    // Discards the result of a sync that is still running, so it can't move the clock later
    // (call when calibration or a movement starts).
    let clockSyncGen = 0;
    function clockCancelPending() {
        clockSyncGen++;
        clockSyncing = null;
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
        box.innerHTML = '<b>⚠️ Check the setup</b> ' +
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

    // ---------------------------------------------------------------- Spatial sound (show stems)
    // How loud one instrument stem is at one seat. These are the rules the older render loop
    // used, unchanged:
    //   - the source sits at (originX, originY), or glides to (destX, destY) over the path time
    //     with a sine ease-in-out, then stays there
    //   - inside `radius`: rolloff "linear" gives 1 − d/r; anything else ("none") gives 1
    //   - at or beyond `radius`: silent
    // The path time is asset.duration (ms) if the timeline gives one, otherwise the stem's length.
    function stemPath(asset) {
        const num = (v, d) => (v === null || v === undefined || v === '' || !Number.isFinite(+v)) ? d : +v;
        const ox = num(asset.originX, NaN), oy = num(asset.originY, NaN);
        return { ox, oy, dx: num(asset.destX, ox), dy: num(asset.destY, oy), r: num(asset.radius, NaN),
                 linear: asset.rolloff === 'linear' };
    }
    function stemPathOk(asset) {
        const s = stemPath(asset);
        return Number.isFinite(s.ox) && Number.isFinite(s.oy) && Number.isFinite(s.r) && s.r > 0;
    }
    // True if the source comes inside its radius of the seat at any point on its path.
    function reachesSeat(asset, seat) {
        if (!stemPathOk(asset)) return false;
        const s = stemPath(asset);
        return distToSegment({ x: seat.x, y: seat.y }, { x: s.ox, y: s.oy }, { x: s.dx, y: s.dy }) < s.r;
    }
    // Gain (0..1) at the seat, tSec seconds after the stem starts.
    function gainAt(asset, seat, tSec, pathSec) {
        const s = stemPath(asset);
        let x = s.ox, y = s.oy;
        if ((s.dx !== s.ox || s.dy !== s.oy) && pathSec > 0) {
            const t = Math.max(0, Math.min(1, tSec / pathSec));
            const e = -(Math.cos(Math.PI * t) - 1) / 2;
            x = s.ox + (s.dx - s.ox) * e;
            y = s.oy + (s.dy - s.oy) * e;
        }
        const d = Math.hypot(seat.x - x, seat.y - y);
        if (!(d < s.r)) return 0;
        return s.linear ? 1 - d / s.r : 1;
    }
    function isMoving(asset) {
        const s = stemPath(asset);
        return s.dx !== s.ox || s.dy !== s.oy;
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

    /*
     * record(ctx, stream, { startServerMs, durationMs })
     *   → Promise<{ samples: Float32Array, sampleRate, startServerMs, method }>
     * Records the microphone and says, on the server clock, when its first sample was captured.
     * Start it a few hundred ms early: it keeps only the audio from startServerMs on (or from
     * the first sample it gets, if that is later), and reports the true time of sample 0.
     * Callers place what they find with  result.startServerMs + offset  and never assume the
     * recording began when they asked. (Assuming that made every phone's mic delay come out
     * about −85 ms: one ScriptProcessor block of 4096 samples at 48 kHz.)
     * Timing: each block carries the audio-clock frame it was captured at (AudioWorklet
     * currentFrame), converted with the same audio-clock ↔ server-clock mapping playAt uses, so
     * playback and recording share one timeline. Microphone input latency stays in the result;
     * that is what "mic delay" measures.
     */
    const RECORDER_SRC = `class SoRecorder extends AudioWorkletProcessor {
        constructor() { super(); this.buf = new Float32Array(2048); this.n = 0; this.f0 = 0; }
        process(inputs) {
            const ch = inputs[0] && inputs[0][0];
            if (ch) {
                if (this.n === 0) this.f0 = currentFrame;
                this.buf.set(ch, this.n); this.n += ch.length;
                if (this.n + ch.length > this.buf.length) { this.port.postMessage({ f0: this.f0, data: this.buf.slice(0, this.n) }); this.n = 0; }
            }
            return true;
        }
    }
    registerProcessor('so-recorder', SoRecorder);`;
    const recorderModules = new WeakMap();
    function prepareRecorder(ctx) {
        if (!ctx.audioWorklet) return Promise.reject(new Error('no AudioWorklet'));
        if (!recorderModules.has(ctx)) {
            const url = URL.createObjectURL(new Blob([RECORDER_SRC], { type: 'application/javascript' }));
            recorderModules.set(ctx, ctx.audioWorklet.addModule(url));
        }
        return recorderModules.get(ctx);
    }
    function medianOf(a) { const s = [...a].sort((x, y) => x - y); const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; }
    async function record(ctx, stream, { startServerMs, durationMs }) {
        const sr = ctx.sampleRate;
        const src = ctx.createMediaStreamSource(stream);
        const sink = ctx.createGain(); sink.gain.value = 0; sink.connect(ctx.destination);
        let node = null, method = 'worklet';
        try {
            await prepareRecorder(ctx);
            node = new AudioWorkletNode(ctx, 'so-recorder', { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1] });
        } catch (e) {
            method = 'scriptprocessor';
        }
        const SP_SIZE = 4096;
        if (!node) node = ctx.createScriptProcessor(SP_SIZE, 1, 1);
        if (startServerMs === undefined) startServerMs = now();
        const endServerMs = startServerMs + durationMs;
        const blocks = [], ks = [];
        return new Promise((resolve, reject) => {
            let finished = false;
            const finish = () => {
                if (finished) return; finished = true;
                clearTimeout(guard);
                try { src.disconnect(); node.disconnect(); sink.disconnect(); } catch (e) {}
                if (node.port) node.port.onmessage = null; else node.onaudioprocess = null;
                if (!blocks.length) { reject(new Error('the microphone delivered no audio')); return; }
                const K = medianOf(ks);                           // server ms at audio frame 0
                const want0 = Math.ceil((startServerMs - K) / 1000 * sr);
                const want1 = Math.floor((endServerMs - K) / 1000 * sr);
                const first = Math.max(want0, blocks[0].f0);
                const last = Math.min(want1, blocks[blocks.length - 1].f0 + blocks[blocks.length - 1].data.length);
                const samples = new Float32Array(Math.max(0, last - first));
                for (const b of blocks) {
                    const a = Math.max(first, b.f0), z = Math.min(last, b.f0 + b.data.length);
                    if (z > a) samples.set(b.data.subarray(a - b.f0, z - b.f0), a - first);
                }
                resolve({ samples, sampleRate: sr, startServerMs: K + first / sr * 1000, method });
            };
            const onBlock = (f0, data) => {
                ks.push(now() - ctx.currentTime * 1000);           // the same mapping playAt schedules with
                blocks.push({ f0, data });
                if (medianOf(ks) + (f0 + data.length) / sr * 1000 >= endServerMs) finish();
            };
            if (node.port) node.port.onmessage = e => onBlock(e.data.f0, e.data.data);
            else node.onaudioprocess = e => {   // fallback: the input block was captured one block before playbackTime
                const d = new Float32Array(e.inputBuffer.getChannelData(0));
                onBlock(Math.round(e.playbackTime * sr) - SP_SIZE, d);
            };
            src.connect(node); node.connect(sink);
            const guard = setTimeout(finish, Math.max(0, endServerMs - now()) + 3000);
        });
    }

    // Plays the first 0.1 s of the buffer almost silently, right now, to wake the speaker path.
    // playAt() calls this automatically for any buffer not yet primed; pages may also call it
    // earlier (e.g. at activation) so the hardware is awake well before the first cue.
    /*
     * findTemplate(template, signal, fromIdx, toIdx, sampleRate = 48000)
     *   → { index, score }   where in signal (start index between fromIdx and toIdx) the template
     * lines up best, and how well (normalized correlation at that spot, 0…1).
     * Uses GCC-PHAT: the cross-spectrum is whitened (every frequency weighted equally) before
     * transforming back, so the answer is a single sharp peak at the true delay instead of the
     * broad, many-humped correlation that sustained tonal notes give, and room echoes, which
     * arrive later, don't pull it off. Limited to 300 Hz – 8 kHz, where the calibration sounds are.
     */
    function fftInPlace(re, im, inverse) {
        const n = re.length;
        for (let i = 1, j = 0; i < n; i++) {
            let bit = n >> 1;
            for (; j & bit; bit >>= 1) j ^= bit;
            j ^= bit;
            if (i < j) { let t = re[i]; re[i] = re[j]; re[j] = t; t = im[i]; im[i] = im[j]; im[j] = t; }
        }
        for (let len = 2; len <= n; len <<= 1) {
            const ang = (inverse ? 2 : -2) * Math.PI / len, wr = Math.cos(ang), wi = Math.sin(ang);
            for (let i = 0; i < n; i += len) {
                let cr = 1, ci = 0;
                for (let k = 0; k < len / 2; k++) {
                    const a = i + k, b = a + len / 2;
                    const xr = re[b] * cr - im[b] * ci, xi = re[b] * ci + im[b] * cr;
                    re[b] = re[a] - xr; im[b] = im[a] - xi; re[a] += xr; im[a] += xi;
                    const t = cr * wr - ci * wi; ci = cr * wi + ci * wr; cr = t;
                }
            }
        }
    }
    function findTemplate(template, signal, fromIdx = 0, toIdx = signal.length - template.length, sampleRate = 48000) {
        const m = template.length;
        fromIdx = Math.max(0, Math.round(fromIdx)); toIdx = Math.min(signal.length - m, Math.round(toIdx));
        if (toIdx < fromIdx || m < 64) return { index: -1, score: 0 };
        const segLen = toIdx - fromIdx + m;
        let N = 1; while (N < segLen + m) N <<= 1;
        const ar = new Float64Array(N), ai = new Float64Array(N), br = new Float64Array(N), bi = new Float64Array(N);
        for (let i = 0; i < segLen; i++) ar[i] = signal[fromIdx + i];
        for (let i = 0; i < m; i++) br[i] = template[i];
        fftInPlace(ar, ai, false); fftInPlace(br, bi, false);
        const lo = Math.floor(300 / sampleRate * N), hi = Math.min(N / 2, Math.ceil(8000 / sampleRate * N));
        let maxMag = 0;
        for (let k = 0; k < N; k++) {           // G = A · conj(B)
            const gr = ar[k] * br[k] + ai[k] * bi[k], gi = ai[k] * br[k] - ar[k] * bi[k];
            ar[k] = gr; ai[k] = gi; const mag = Math.hypot(gr, gi); if (mag > maxMag) maxMag = mag;
        }
        const eps = maxMag * 1e-4;
        for (let k = 0; k < N; k++) {
            const f = k <= N / 2 ? k : N - k;
            if (f < lo || f > hi) { ar[k] = 0; ai[k] = 0; continue; }
            const w = 1 / (Math.hypot(ar[k], ai[k]) + eps); ar[k] *= w; ai[k] *= w;
        }
        fftInPlace(ar, ai, true);
        let best = 0, max = -Infinity;
        for (let lag = 0; lag <= toIdx - fromIdx; lag++) if (ar[lag] > max) { max = ar[lag]; best = lag; }
        const index = fromIdx + best;
        let dot = 0, et = 0, es = 0;
        for (let i = 0; i < m; i++) { const a = template[i], b = signal[index + i]; dot += a * b; et += a * a; es += b * b; }
        return { index, score: et && es ? Math.max(0, dot / Math.sqrt(et * es)) : 0 };
    }
    function decimateBy(x, f) {
        const out = new Float32Array(Math.floor(x.length / f));
        for (let i = 0, j = 0; i < out.length; i++) { let s = 0; for (let k = 0; k < f; k++) s += x[j++]; out[i] = s / f; }
        return out;
    }
    /*
     * findTemplateMasked(template, signal, mask, fromIdx, toIdx)  → { index, score }
     * Like findTemplate, for a signal with stretches that must be ignored (mask[i] = 0), such as
     * a phone's own notes in its own recording. Normalized correlation computed only over the
     * samples that are kept, so a blanked stretch counts as "unknown", not as silence. Both
     * signals are first-differenced so note attacks dominate (sharper peaks on tonal notes).
     */
    function xcorrFFT(a, b, N) {     // c[L] = sum_i a[i] * b[L+i]
        const ar = new Float64Array(N), ai = new Float64Array(N), br = new Float64Array(N), bi = new Float64Array(N);
        ar.set(a); br.set(b);
        fftInPlace(ar, ai, false); fftInPlace(br, bi, false);
        for (let k = 0; k < N; k++) {   // conj(A) * B
            const r = ar[k] * br[k] + ai[k] * bi[k], im = ar[k] * bi[k] - ai[k] * br[k];
            ar[k] = r; ai[k] = im;
        }
        fftInPlace(ar, ai, true);
        for (let k = 0; k < N; k++) ar[k] /= N;
        return ar;
    }
    function findTemplateMasked(template, signal, mask, fromIdx = 0, toIdx = signal.length - template.length) {
        const m = template.length;
        fromIdx = Math.max(0, Math.round(fromIdx)); toIdx = Math.min(signal.length - m, Math.round(toIdx));
        if (toIdx < fromIdx || m < 64) return { index: -1, score: 0 };
        const segLen = toIdx - fromIdx + m;
        let N = 1; while (N < segLen + m) N <<= 1;
        const t = new Float64Array(m), t2 = new Float64Array(m);
        for (let i = 1; i < m; i++) { t[i] = template[i] - template[i - 1]; t2[i] = t[i] * t[i]; }
        const r = new Float64Array(segLen), r2 = new Float64Array(segLen), mk = new Float64Array(segLen);
        for (let i = 1; i < segLen; i++) {
            const j = fromIdx + i, keep = mask[j] && mask[j - 1] ? 1 : 0;
            const d = keep * (signal[j] - signal[j - 1]);
            r[i] = d; r2[i] = d * d; mk[i] = keep;
        }
        const num = xcorrFFT(t, r, N), tEnergy = xcorrFFT(t2, mk, N), rEnergy = xcorrFFT(t2.map(() => 1), r2, N);
        let tot = 0; for (let i = 0; i < m; i++) tot += t2[i];
        let best = -1, max = -Infinity;
        for (let lag = 0; lag <= toIdx - fromIdx; lag++) {
            if (tEnergy[lag] < 0.3 * tot) continue;              // too little of the template is visible here
            const sc = num[lag] / Math.sqrt(Math.max(1e-20, tEnergy[lag] * rEnergy[lag]));
            if (sc > max) { max = sc; best = lag; }
        }
        if (best < 0) return { index: -1, score: 0 };
        return { index: fromIdx + best, score: Math.max(0, max) };
    }
    // Mono copy of part of a decoded buffer (channels averaged)
    function monoSlice(buffer, startSec, durSec) {
        const sr = buffer.sampleRate, a = Math.max(0, Math.round(startSec * sr));
        const z = Math.min(buffer.length, Math.round((startSec + durSec) * sr));
        const out = new Float32Array(Math.max(0, z - a));
        for (let c = 0; c < buffer.numberOfChannels; c++) {
            const d = buffer.getChannelData(c);
            for (let i = a; i < z; i++) out[i - a] += d[i] / buffer.numberOfChannels;
        }
        return out;
    }

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
        BUILD: '2026-11-11h',
        FIREBASE_CONFIG, LIMITS, FALLBACK, db,
        now, onClockSync,
        get serverOffset() { return now() - Date.now(); },   // legacy: server − Date.now()
        clock: {
            sync: clockSync, recheck: clockRecheck, recheckBefore: clockRecheckBefore, cancelPending: clockCancelPending,
            setDeviceId: id => { clockDeviceId = id; },
            get status() { return lastClockSync; },
            get precise() { return clockPrecise; },
            get sleepJumps() { return clockSleepJumps; },
            SETTINGS: CLOCK
        },
        requireCrew, showWarning,
        speedFromTemp, resolveSpeedOfSound,
        validatePillarConfig, normalizePillarConfig, loadPillarConfig, watchPillarConfig,
        parseSeatTsv, computeNeighborMap, validateSeatConfig, normalizeSeatConfig, loadSeatConfig, getSeat,
        loadTuningMode,
        audio: { getBuffer, serverToAudioTime, playAt, primeBuffer, isPrimed, PRIME_DEFAULTS, record, prepareRecorder },
        dsp: { findTemplate, findTemplateMasked, monoSlice, decimate: decimateBy },
        geometry: { dist, pillarPolygon, distanceOutside },
        spatial: { reachesSeat, gainAt, isMoving, pathOk: stemPathOk }
    };
})();
