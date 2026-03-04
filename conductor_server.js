/**
 * server.js
 * 
 * Stage Manager local bridge for Smartphone Orchestra.
 * Run on the SM laptop alongside conductor_run_locally.html.
 * 
 * Accepts cue triggers from:
 *   - Chataigne  → HTTP POST /cue
 *   - LiSP / any OSC source → UDP port 57121
 *   - Manual     → SM presses GO in conductor_run_locally.html (bypasses this server entirely)
 * 
 * Streams triggers to conductor_run_locally.html via Server-Sent Events (SSE).
 * Never writes to Firebase directly — conductor_run_locally.html owns that.
 * 
 * Usage:
 *   npm install express osc
 *   node server.js
 * 
 * Then open: http://localhost:3000/conductor_run_locally.html
 */

const express = require('express');
const path    = require('path');
const osc     = require('osc');

const HTTP_PORT = 3000;
const OSC_PORT  = 57121;

const app = express();
app.use(express.json());

// Serve conductor_run_locally.html, cues.json, timeline.json from the same directory.
app.use(express.static(path.join(__dirname)));

// ─── SSE Client Registry ──────────────────────────────────────────────────────
// Each open conductor_run_locally.html tab registers itself here.
let sseClients = [];

app.get('/events', (req, res) => {
    res.set({
        'Content-Type':  'text/event-stream',
        'Cache-Control': 'no-cache',
        'Connection':    'keep-alive',
    });
    res.flushHeaders();

    // Send a heartbeat immediately so the browser confirms the connection.
    res.write(': heartbeat\n\n');

    sseClients.push(res);
    console.log(`[SSE] Client connected. Total: ${sseClients.length}`);

    req.on('close', () => {
        sseClients = sseClients.filter(c => c !== res);
        console.log(`[SSE] Client disconnected. Total: ${sseClients.length}`);
    });
});

/**
 * Broadcast a cue to all connected conductor_run_locally.html tabs.
 * @param {string} label  - Must match a cueLabel in cues.json (e.g. "2.0 Front Pulse")
 * @param {number} buffer - Sync buffer in seconds (optional, defaults to 1.0)
 * @param {string} source - For console logging only ("HTTP" or "OSC")
 */
function broadcastCue(label, buffer = 1.0, source = 'UNKNOWN') {
    if (!label) {
        console.warn(`[${source}] Received trigger with no cue label — ignored.`);
        return;
    }

    const payload = JSON.stringify({ label, buffer });
    sseClients.forEach(client => client.write(`data: ${payload}\n\n`));
    console.log(`[${source}] Fired cue: "${label}" | buffer: ${buffer}s | clients: ${sseClients.length}`);
}

// ─── HTTP endpoint (Chataigne) ────────────────────────────────────────────────
// Chataigne: HTTP Request module → POST http://localhost:3000/cue
// Body (JSON): { "label": "2.0 Front Pulse", "buffer": 1.5 }
app.post('/cue', (req, res) => {
    const { label, buffer } = req.body;
    broadcastCue(label, buffer, 'HTTP');
    res.sendStatus(200);
});

// ─── Emergency stop endpoint ──────────────────────────────────────────────────
// POST /kill — broadcasts the SILENCE cue label so conductor_run_locally.html calls emergencyStop()
app.post('/kill', (req, res) => {
    broadcastCue('0.0 SILENCE', 1.0, 'HTTP-KILL');
    res.sendStatus(200);
});

// ─── OSC listener (LiSP / any OSC source) ────────────────────────────────────
// LiSP: OSC Output module → address /smartphone/cue
// Args: [ string: cueLabel, float?: buffer ]
//
// Example OSC message:
//   address: /smartphone/cue
//   args:    [ "2.0 Front Pulse", 1.5 ]
//
// Emergency stop:
//   address: /smartphone/kill   (no args needed)

const udpPort = new osc.UDPPort({
    localAddress: '0.0.0.0',
    localPort:    OSC_PORT,
    metadata:     true,         // Required for reliable arg parsing
});

udpPort.on('message', (msg) => {
    if (msg.address === '/smartphone/cue') {
        const label  = msg.args[0]?.value;
        const buffer = msg.args[1]?.value ?? 1.0;
        broadcastCue(label, buffer, 'OSC');

    } else if (msg.address === '/smartphone/kill') {
        broadcastCue('0.0 SILENCE', 1.0, 'OSC-KILL');

    } else {
        console.log(`[OSC] Unhandled address: ${msg.address}`);
    }
});

udpPort.on('error', (err) => {
    console.error('[OSC] Error:', err.message);
});

udpPort.open();

// ─── Start HTTP server ────────────────────────────────────────────────────────
app.listen(HTTP_PORT, () => {
    console.log('');
    console.log('╔══════════════════════════════════════════╗');
    console.log('║   Smartphone Orchestra — SM Bridge       ║');
    console.log('╠══════════════════════════════════════════╣');
    console.log(`║  Serving conductor_run_locally.html                 ║`);
    console.log(`║  → http://localhost:${HTTP_PORT}/conductor_run_locally.html  ║`);
    console.log(`║                                          ║`);
    console.log(`║  HTTP cue endpoint: POST /cue            ║`);
    console.log(`║  HTTP kill endpoint: POST /kill          ║`);
    console.log(`║  OSC listener: UDP port ${OSC_PORT}           ║`);
    console.log('╚══════════════════════════════════════════╝');
    console.log('');
});