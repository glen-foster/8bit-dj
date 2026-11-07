// =========================================================================
// 3. AUDIO-REACTIVE 8-BIT PIXEL CANVAS ENGINE (MULTI-STYLE + WINAMP + SIDE-SCROLLER)
// =========================================================================
const canvas = document.getElementById('arcadeCanvas');
const ctx = canvas.getContext('2d');
const W = canvas.width;   // 320
const H = canvas.height;  // 180

const VIZ_MODES = [
  { id: 'auto', label: 'AUTO' },
  { id: 'side_scroller', label: 'SIDE-SCROLLER' },
  { id: 'beat_em_up', label: 'BEAT-EM-UP' },
  { id: 'shmup', label: 'SPACE SHMUP' },
  { id: 'jrpg_battle', label: 'JRPG BATTLE' },
  { id: 'dungeon_castle', label: 'DUNGEON KEEP' },
  { id: 'winamp_analyzer', label: 'WINAMP LED' },
  { id: 'winamp_fire', label: 'WINAMP FIRE' },
  { id: 'city_cruiser', label: 'CITY SKYLINE' },
  { id: 'synth_grid', label: 'SYNTH GRID' }
];
let vizModeIdx = 0; // 0 = 'auto'

function cycleVizMode() {
  vizModeIdx = (vizModeIdx + 1) % VIZ_MODES.length;
}

function setVizModeById(modeId) {
  if (modeId === 'cycle') {
    cycleVizMode();
    return;
  }
  const idx = VIZ_MODES.findIndex(m => m.id === modeId);
  if (idx !== -1) {
    vizModeIdx = idx;
  }
}

function resolveActiveVizStyle(sprites) {
  const selected = VIZ_MODES[vizModeIdx].id;
  if (selected !== 'auto') return selected;

  // If scene explicitly specifies a valid bg_style from Gemini or fallback
  if (currentScene && currentScene.bg_style && VIZ_MODES.some(m => m.id === currentScene.bg_style)) {
    return currentScene.bg_style;
  }

  // Automatically favor character-centric arcade stages when character/walker sprites are present!
  const hasCharacters = sprites.some(s => s.behavior === 'dancers' || s.behavior === 'scroll');
  if (hasCharacters) {
    const titleStr = (currentScene && currentScene.stage_title) ? currentScene.stage_title : '';
    let hash = 0;
    for (let i = 0; i < titleStr.length; i++) hash = (hash * 31 + titleStr.charCodeAt(i)) >>> 0;
    const charModes = [
      'side_scroller', 'beat_em_up', 'jrpg_battle', 'dungeon_castle',
      'shmup', 'city_cruiser', 'winamp_analyzer'
    ];
    return charModes[hash % charModes.length];
  }

  const titleStr = (currentScene && currentScene.stage_title) ? currentScene.stage_title : 'STAGE';
  let hash = 0;
  for (let i = 0; i < titleStr.length; i++) hash = (hash * 31 + titleStr.charCodeAt(i)) >>> 0;
  const allAutoModes = [
    'winamp_analyzer', 'side_scroller', 'beat_em_up', 'shmup',
    'jrpg_battle', 'dungeon_castle', 'winamp_fire', 'city_cruiser', 'synth_grid'
  ];
  return allAutoModes[hash % allAutoModes.length];
}

// Pre-initialize scrolling starfield, clouds, buildings, fire particles, and Winamp peak-hold caps
const stars = Array.from({ length: 55 }, () => ({
  x: Math.random() * W,
  y: Math.random() * (H * 0.65),
  speed: 0.2 + Math.random() * 1.1,
  size: Math.random() > 0.75 ? 2 : 1
}));

const clouds = Array.from({ length: 6 }, (_, i) => ({
  x: (i * 62) % W,
  y: 16 + (i % 3) * 18,
  w: 24 + (i % 2) * 12,
  speed: 0.18 + (i % 3) * 0.08
}));

const buildings = Array.from({ length: 18 }, (_, i) => ({
  w: 16 + (i * 7) % 14,
  h: 28 + (i * 19) % 52,
  windowsSeed: (i * 97) + 13
}));

const fireEmbers = Array.from({ length: 45 }, () => ({
  x: Math.random() * W,
  y: H * 0.5 + Math.random() * (H * 0.5),
  vx: (Math.random() - 0.5) * 0.5,
  vy: 0.5 + Math.random() * 1.4,
  size: Math.random() > 0.6 ? 2 : 1
}));

const NUM_SPECTRUM_BARS = 32;
const peakCaps = new Float32Array(NUM_SPECTRUM_BARS);
const peakHoldFrames = new Int16Array(NUM_SPECTRUM_BARS);

const spriteActors = Array.from({ length: 8 }, (_, idx) => ({
  x: 25 + (idx * 38) % (W - 40),
  y: 25 + (idx * 23) % (H - 70),
  vx: (idx % 2 === 0 ? 1 : -1) * (0.45 + (idx % 3) * 0.25),
  vy: 0.35 + (idx % 2) * 0.25,
  phase: idx * 0.9
}));

let frameTick = 0;
let stageScrollX = 0;
let smoothBass = 0.15;
let smoothMid = 0.12;
let smoothTreble = 0.10;

function getAudioEnergy() {
  if (!analyser || !audioCtx || audioCtx.state !== 'running') {
    const idlePulse = (Math.sin(frameTick * 0.06) + 1) * 0.25;
    for (let i = 0; i < timeData.length; i++) {
      timeData[i] = 128 + Math.floor(Math.sin(frameTick * 0.08 + i * 0.25) * 18);
    }
    return { bass: idlePulse, mid: idlePulse * 0.8, treble: idlePulse * 0.6 };
  }
  analyser.getByteFrequencyData(freqData);
  analyser.getByteTimeDomainData(timeData);
  let bassSum = 0;
  let midSum = 0;
  let trebleSum = 0;
  for (let i = 0; i < 8; i++) bassSum += freqData[i];
  for (let i = 8; i < 24; i++) midSum += freqData[i];
  for (let i = 24; i < 48; i++) trebleSum += freqData[i];

  return {
    bass: (bassSum / (8 * 255)),
    mid: (midSum / (16 * 255)),
    treble: (trebleSum / (24 * 255))
  };
}

function getBarValue(b, bass) {
  if (analyser && audioCtx && audioCtx.state === 'running' && freqData) {
    return freqData[b] / 255;
  }
  return Math.max(0.08, (Math.sin(frameTick * 0.07 + b * 0.35) * 0.35 + 0.4) * (0.5 + bass));
}

// Classic Winamp Segmented LED Spectrum Bars + Gravity Peak-Hold Caps
function drawWinampSegmentedBars(baseY, maxBarH, bass, pal, mirrored = false) {
  const barWidth = 7;
  const gap = 3;
  const segH = 2;
  const segGap = 1;
  const step = segH + segGap;

  for (let b = 0; b < NUM_SPECTRUM_BARS; b++) {
    const val = getBarValue(b, bass);
    const rawH = Math.max(step, Math.floor(val * maxBarH));
    const bx = b * (barWidth + gap) + 2;

    // Update gravity peak-hold cap
    if (rawH >= peakCaps[b]) {
      peakCaps[b] = rawH;
      peakHoldFrames[b] = 18; // Hold at the peak for ~18 frames like classic Winamp
    } else {
      if (peakHoldFrames[b] > 0) {
        peakHoldFrames[b]--;
      } else {
        peakCaps[b] = Math.max(step, peakCaps[b] - 0.85);
      }
    }

    // Draw segmented LED blocks from bottom to top
    const numSegments = Math.floor(rawH / step);
    const maxSegments = Math.floor(maxBarH / step);
    for (let s = 0; s < numSegments; s++) {
      const ratio = s / Math.max(1, maxSegments);
      // Classic Winamp gradient: green base -> yellow/neon middle -> hot pink/red top
      if (ratio < 0.5) {
        ctx.fillStyle = "#22c55e"; // Classic LED Green
      } else if (ratio < 0.78) {
        ctx.fillStyle = pal.accent_neon || "#eab308"; // Mid Neon / Amber
      } else {
        ctx.fillStyle = pal.pixel_highlight || "#ff2a85"; // Peak Red / Hot Pink
      }

      const sy = baseY - (s + 1) * step;
      ctx.fillRect(bx, sy, barWidth, segH);

      if (mirrored) {
        ctx.globalAlpha = 0.45;
        ctx.fillRect(bx, baseY + 2 + s * step, barWidth, segH);
        ctx.globalAlpha = 1.0;
      }
    }

    // Draw Peak-Hold Cap (bright white 2px bar)
    const capY = Math.floor(baseY - peakCaps[b] - 2);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(bx, capY, barWidth, 2);
  }
}

// Classic Winamp Oscilloscope Waveform Ribbon
function drawOscilloscopeWaveform(centerY, amplitude, pal) {
  const len = timeData.length;
  const sliceW = W / len;
  for (let i = 0; i < len; i++) {
    const v = (timeData[i] - 128) / 128.0;
    const y = Math.floor(centerY + v * amplitude);
    const x = Math.floor(i * sliceW);
    ctx.fillStyle = i % 2 === 0 ? pal.accent_neon : "#ffffff";
    ctx.fillRect(x, y, Math.ceil(sliceW), 2);
    // Subtle glow shadow
    ctx.fillStyle = pal.pixel_highlight;
    ctx.fillRect(x, y + 2, Math.ceil(sliceW), 1);
  }
}

function drawPixelMatrix(matrix, startX, startY, scale, paletteColors, flipX = false) {
  if (!Array.isArray(matrix)) return;
  const rows = matrix.length;
  const baseX = Math.round(startX);
  const baseY = Math.round(startY);

  // Pass 1: High-contrast outer black border + subtle white rim so sprites pop off any background!
  for (let r = 0; r < rows; r++) {
    const row = matrix[r];
    if (!Array.isArray(row)) continue;
    const cols = row.length;
    for (let c = 0; c < cols; c++) {
      const colorIdx = row[flipX ? (cols - 1 - c) : c];
      if (!colorIdx || colorIdx <= 0) continue;
      const px = baseX + c * scale;
      const py = baseY + r * scale;
      // Outer white/neon halo + inner black cut-out outline
      ctx.fillStyle = "rgba(255, 255, 255, 0.35)";
      ctx.fillRect(px - 2, py - 2, scale + 4, scale + 4);
      ctx.fillStyle = "#000000";
      ctx.fillRect(px - 1, py - 1, scale + 2, scale + 2);
    }
  }

  // Pass 2: Crisp foreground sprite pixels at 100% brightness
  for (let r = 0; r < rows; r++) {
    const row = matrix[r];
    if (!Array.isArray(row)) continue;
    const cols = row.length;
    for (let c = 0; c < cols; c++) {
      const colorIdx = row[flipX ? (cols - 1 - c) : c];
      if (!colorIdx || colorIdx <= 0) continue;
      ctx.fillStyle = paletteColors[colorIdx] || '#ffffff';
      ctx.fillRect(
        baseX + c * scale,
        baseY + r * scale,
        scale,
        scale
      );
    }
  }
}

// -------------------------------------------------------------------------
// STAGE STYLE 1: SIDE-SCROLLER PLATFORMER (Great for character/dancer sets!)
// -------------------------------------------------------------------------
function renderSideScrollerStage(pal, bass, mid, treble) {
  const groundY = Math.floor(H * 0.66); // Y=118

  // 1. Parallax Pixel Clouds
  for (const cl of clouds) {
    cl.x -= cl.speed * (1 + bass * 0.8);
    if (cl.x + cl.w < 0) cl.x = W + 12;
    const cx = Math.floor(cl.x);
    const cy = Math.floor(cl.y);
    ctx.fillStyle = pal.bg_secondary;
    ctx.fillRect(cx + 2, cy + 4, cl.w, 6);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(cx, cy + 2, cl.w, 5);
    ctx.fillRect(cx + 4, cy, cl.w - 8, 3);
  }

  // 2. Compact Winamp-style Mini LED Equalizer across the upper sky
  for (let b = 0; b < NUM_SPECTRUM_BARS; b++) {
    const val = getBarValue(b, bass);
    const h = Math.max(2, Math.floor(val * 18));
    ctx.fillStyle = b % 2 === 0 ? pal.accent_neon : pal.pixel_highlight;
    ctx.fillRect(b * 10 + 2, groundY - h, 6, h);
  }

  // 3. Parallax Rounded Retro Platformer Hills & Bushes
  const hillScroll = Math.floor(stageScrollX * 0.65);
  ctx.fillStyle = pal.bg_secondary;
  for (let x = 0; x < W; x += 4) {
    const wx = x + hillScroll;
    const hillH = Math.floor(12 + Math.max(0, Math.sin(wx * 0.035) * 18) + smoothBass * 4);
    ctx.fillRect(x, groundY - hillH, 4, hillH);
  }

  // 4. Floating Retro Brick / Note Platforms that bounce gently on bass hits
  const platScroll = (stageScrollX * 1.1) % 110;
  const bassBump = -Math.round(smoothBass * 3);
  for (let p = 0; p < 4; p++) {
    const px = Math.floor(W - ((p * 110 + platScroll) % (W + 60)) - 20);
    const py = groundY - 38 - (p % 2) * 14 + bassBump;
    // Draw 3 connected 10x10 retro blocks
    for (let blk = 0; blk < 3; blk++) {
      const bx = px + blk * 10;
      ctx.fillStyle = blk === 1 ? pal.pixel_highlight : pal.bg_secondary;
      ctx.fillRect(bx, py, 9, 9);
      ctx.fillStyle = pal.accent_neon;
      ctx.fillRect(bx, py, 9, 1);
      ctx.fillRect(bx, py, 1, 9);
      if (blk === 1) {
        // Question / Note dot inside center block
        ctx.fillStyle = "#ffffff";
        ctx.fillRect(bx + 3, py + 3, 3, 3);
      }
    }
  }

  // 5. Scrolling Side-Scroller Ground (Top Turf + Checkered Brick Tiles)
  ctx.fillStyle = pal.accent_neon;
  ctx.fillRect(0, groundY, W, 3);
  ctx.fillStyle = pal.pixel_highlight;
  ctx.fillRect(0, groundY + 3, W, 2);

  ctx.fillStyle = pal.bg_secondary;
  ctx.fillRect(0, groundY + 5, W, H - (groundY + 5));

  const tileOffset = Math.floor(stageScrollX * 1.5) % 16;
  ctx.fillStyle = "#000000";
  for (let y = groundY + 5; y < H; y += 8) {
    ctx.fillRect(0, y, W, 1);
    const rowShift = ((y - groundY) % 16 === 0) ? 0 : 8;
    for (let x = -tileOffset + rowShift; x < W; x += 16) {
      ctx.fillRect(x, y, 1, 8);
    }
  }

  return groundY;
}

// -------------------------------------------------------------------------
// STAGE STYLE 1B: 90s ARCADE BEAT-EM-UP STREET BRAWLER (Streets of Rage / Final Fight)
// -------------------------------------------------------------------------
function renderBeatEmUpStage(pal, bass, mid, treble) {
  const wallTopY = 34;
  const curbY = 74;
  const streetTopY = 82;

  // 1. Scrolling Brick Alley Wall & Storefronts
  ctx.fillStyle = pal.bg_secondary;
  ctx.fillRect(0, wallTopY, W, curbY - wallTopY);
  ctx.fillStyle = pal.accent_neon;
  ctx.fillRect(0, wallTopY, W, 2);

  const alleyScroll = Math.floor(stageScrollX * 0.85);
  ctx.fillStyle = "rgba(0, 0, 0, 0.45)";
  for (let y = wallTopY + 6; y < curbY; y += 6) {
    ctx.fillRect(0, y, W, 1);
    const shift = ((y - wallTopY) % 12 === 0) ? 0 : 6;
    for (let x = -(alleyScroll % 12) + shift; x < W; x += 12) {
      ctx.fillRect(x, y, 1, 6);
    }
  }

  // 2. Repeating Alleyway Props: Neon Signs, Barred Doors, Streetlamps & Burning Trash Barrels
  const propSpan = 88;
  let propX = -(alleyScroll % propSpan);
  let propIdx = Math.floor(alleyScroll / propSpan);
  while (propX < W + 32) {
    // Dark Alley Doorway + Grate
    ctx.fillStyle = "#05030a";
    ctx.fillRect(propX + 8, curbY - 28, 16, 28);
    ctx.fillStyle = pal.pixel_highlight;
    ctx.fillRect(propX + 7, curbY - 29, 18, 2);

    // Glowing Storefront Neon Sign (pulsing on mid/treble)
    const signX = propX + 32;
    const signY = wallTopY + 8;
    ctx.fillStyle = "#05030a";
    ctx.fillRect(signX, signY, 28, 13);
    ctx.fillStyle = (propIdx % 2 === 0) ? pal.pixel_highlight : pal.accent_neon;
    ctx.fillRect(signX, signY, 28, 1);
    ctx.fillRect(signX, signY + 12, 28, 1);
    ctx.fillRect(signX, signY, 1, 13);
    ctx.fillRect(signX + 27, signY, 1, 13);
    // Inner neon glyph bars
    ctx.fillStyle = (smoothTreble > 0.35 && Math.floor(frameTick / 8) % 2 === 0) ? "#ffffff" : pal.accent_neon;
    ctx.fillRect(signX + 4, signY + 4, 6, 5);
    ctx.fillRect(signX + 12, signY + 4, 4, 5);
    ctx.fillRect(signX + 18, signY + 4, 6, 5);

    // Streetlamp Post & Burning Barrel on Sidewalk
    const barrelX = propX + 68;
    const barrelY = curbY - 12;
    ctx.fillStyle = "#111827";
    ctx.fillRect(barrelX, barrelY, 10, 14);
    ctx.fillStyle = pal.accent_neon;
    ctx.fillRect(barrelX, barrelY + 3, 10, 1);
    ctx.fillRect(barrelX, barrelY + 9, 10, 1);
    // Audio-reactive pixel flames leaping out of the barrel
    const flameH = 4 + Math.floor(smoothBass * 7) + (Math.floor(frameTick / 6 + propIdx * 3) % 3);
    ctx.fillStyle = pal.pixel_highlight;
    ctx.fillRect(barrelX + 1, barrelY - flameH, 8, flameH);
    ctx.fillStyle = "#facc15";
    ctx.fillRect(barrelX + 2, barrelY - Math.max(2, flameH - 2), 6, Math.max(2, flameH - 2));
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(barrelX + 4, barrelY - 2, 2, 2);

    propX += propSpan;
    propIdx++;
  }

  // 3. 2.5D Belt-Scroller Sidewalk & Curb
  ctx.fillStyle = pal.bg_secondary;
  ctx.fillRect(0, curbY, W, streetTopY - curbY);
  ctx.fillStyle = pal.accent_neon;
  ctx.fillRect(0, curbY, W, 1);
  ctx.fillStyle = pal.pixel_highlight;
  ctx.fillRect(0, streetTopY - 2, W, 2);

  // Angled perspective sidewalk slab joints
  ctx.fillStyle = "#000000";
  for (let x = -(alleyScroll % 20); x < W + 20; x += 20) {
    ctx.beginPath();
    ctx.moveTo(x, curbY);
    ctx.lineTo(x - 6, streetTopY);
    ctx.stroke();
  }

  // 4. 2.5D Asphalt Street Plane with Scrolling Center Dashes
  ctx.fillStyle = "#090614";
  ctx.fillRect(0, streetTopY, W, H - streetTopY);

  const dashScroll = Math.floor(stageScrollX * 1.2) % 32;
  ctx.fillStyle = "#facc15";
  for (let x = -dashScroll; x < W; x += 32) {
    ctx.fillRect(x, streetTopY + 20, 16, 3);
  }

  // 5. Arcade Beat-Em-Up 1P VS BOSS Health Bars & Flashing "GO! >>" Arrow
  const hudX = 12;
  const hudY = 22;
  const barW = 60;
  // 1P Life Bar (Left)
  ctx.fillStyle = "#000000";
  ctx.fillRect(hudX - 1, hudY - 1, barW + 2, 6);
  ctx.fillStyle = "#dc2626";
  ctx.fillRect(hudX, hudY, barW, 4);
  const p1Fill = Math.max(16, Math.floor(barW * (0.90 - smoothBass * 0.14)));
  ctx.fillStyle = "#facc15";
  ctx.fillRect(hudX, hudY, p1Fill, 4);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(hudX, hudY, p1Fill, 1);

  // Center KO Badge
  ctx.fillStyle = pal.pixel_highlight;
  ctx.fillRect(hudX + barW + 4, hudY - 2, 10, 8);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(hudX + barW + 6, hudY, 6, 4);

  // BOSS Life Bar (Right)
  const bossX = hudX + barW + 18;
  ctx.fillStyle = "#000000";
  ctx.fillRect(bossX - 1, hudY - 1, barW + 2, 6);
  ctx.fillStyle = "#dc2626";
  ctx.fillRect(bossX, hudY, barW, 4);
  const bossFill = Math.max(12, Math.floor(barW * (0.82 - smoothBass * 0.30)));
  ctx.fillStyle = pal.accent_neon;
  ctx.fillRect(bossX + (barW - bossFill), hudY, bossFill, 4);

  // Flashing Arcade "GO! >>" Arrow on Beat
  if (Math.floor(frameTick / 24) % 2 === 0) {
    const goX = bossX + barW + 8;
    const goY = hudY - 2;
    ctx.fillStyle = "#facc15";
    ctx.fillRect(goX, goY + 2, 8, 4);
    ctx.fillRect(goX + 8, goY, 2, 8);
    ctx.fillRect(goX + 10, goY + 1, 2, 6);
    ctx.fillRect(goX + 12, goY + 2, 2, 4);
    ctx.fillRect(goX + 14, goY + 3, 2, 2);
  }

  return streetTopY + 18;
}

// -------------------------------------------------------------------------
// STAGE STYLE 2: CLASSIC WINAMP LED SPECTRUM + PEAK HOLD + OSCILLOSCOPE
// -------------------------------------------------------------------------
function renderWinampAnalyzerStage(pal, bass, mid, treble) {
  const baseY = Math.floor(H * 0.66);

  // 1. Classic Dark Winamp Analyzer Micro-Grid Backdrop
  ctx.fillStyle = pal.bg_secondary;
  for (let y = 12; y < baseY; y += 8) {
    ctx.fillRect(0, y, W, 1);
  }
  for (let x = 8; x < W; x += 16) {
    ctx.fillRect(x, 0, 1, baseY);
  }

  // 2. Upper Live Oscilloscope Waveform Ribbon
  drawOscilloscopeWaveform(36, 18 + bass * 14, pal);

  // 3. Full-Stage Segmented LED Bars + Gravity Peak-Hold Caps
  drawWinampSegmentedBars(baseY, 64, bass, pal, true);

  // 4. Base Divider Line
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, baseY, W, 1);

  return baseY;
}

// -------------------------------------------------------------------------
// STAGE STYLE 3: WINAMP FIRE / DEMOSCENE PLASMA EQUALIZER
// -------------------------------------------------------------------------
function renderWinampFireStage(pal, bass, mid, treble) {
  const centerY = Math.floor(H * 0.56);

  // 1. Rising Audio-Reactive Demoscene Ember Particles
  for (const em of fireEmbers) {
    em.y -= em.vy * (0.8 + bass * 2.6);
    em.x += Math.sin(frameTick * 0.08 + em.y * 0.05) * 0.5;
    if (em.y < 8) {
      em.y = H - 10;
      em.x = Math.random() * W;
    }
    ctx.fillStyle = em.y < centerY ? pal.pixel_highlight : pal.accent_neon;
    ctx.fillRect(Math.floor(em.x), Math.floor(em.y), em.size, em.size);
  }

  // 2. Dual Sine Plasma Wave Ribbon
  for (let x = 0; x < W; x += 3) {
    const py1 = Math.floor(28 + Math.sin(frameTick * 0.07 + x * 0.03) * (10 + mid * 16));
    const py2 = Math.floor(28 + Math.cos(frameTick * 0.05 - x * 0.04) * (10 + treble * 16));
    ctx.fillStyle = pal.accent_neon;
    ctx.fillRect(x, py1, 2, 2);
    ctx.fillStyle = pal.pixel_highlight;
    ctx.fillRect(x, py2, 2, 2);
  }

  // 3. Mirrored Segmented Spectrum Bars with Peak Caps
  drawWinampSegmentedBars(centerY, 52, bass, pal, true);

  return centerY + 12;
}

// -------------------------------------------------------------------------
// STAGE STYLE 4: MIDNIGHT CITY SKYLINE & HIGHWAY CRUISER
// -------------------------------------------------------------------------
function renderCityCruiserStage(pal, bass, mid, treble) {
  const horizonY = Math.floor(H * 0.62);

  // 1. Twinkling Night Stars
  for (const st of stars) {
    st.x -= st.speed * 0.5;
    if (st.x < 0) st.x = W;
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(Math.floor(st.x), Math.floor(st.y * 0.6), 1, 1);
  }

  // 2. Parallax City Skyscrapers with Audio-Reactive Lit Windows
  const cityScroll = Math.floor(frameTick * 0.35);
  let curX = -(cityScroll % 22);
  let bIdx = 0;
  while (curX < W) {
    const b = buildings[(bIdx + Math.floor(cityScroll / 22)) % buildings.length];
    const by = horizonY - b.h;
    ctx.fillStyle = pal.bg_secondary;
    ctx.fillRect(curX, by, b.w - 1, b.h);
    ctx.fillStyle = pal.accent_neon;
    ctx.fillRect(curX, by, b.w - 1, 1);

    // Lit Pixel Windows
    for (let wy = by + 4; wy < horizonY - 4; wy += 5) {
      for (let wx = curX + 3; wx < curX + b.w - 4; wx += 4) {
        const winHash = ((wx * 13 + wy * 7 + b.windowsSeed) % 11);
        if (winHash < 5) {
          ctx.fillStyle = (winHash === 0 && treble > 0.3) ? "#ffffff" : (winHash % 2 === 0 ? pal.accent_neon : pal.pixel_highlight);
          ctx.fillRect(wx, wy, 2, 3);
        }
      }
    }
    curX += 22;
    bIdx++;
  }

  // 3. Segmented Horizon Equalizer Overlay
  drawWinampSegmentedBars(horizonY, 32, bass, pal, false);

  // 4. Midnight Highway with Scrolling Lane Dashes
  ctx.fillStyle = pal.bg_secondary;
  ctx.fillRect(0, horizonY, W, H - horizonY);
  ctx.fillStyle = pal.pixel_highlight;
  ctx.fillRect(0, horizonY, W, 2);

  const laneScroll = Math.floor(stageScrollX * 1.8) % 24;
  ctx.fillStyle = "#ffffff";
  for (let x = -laneScroll; x < W; x += 24) {
    ctx.fillRect(x, horizonY + 14, 12, 2);
  }

  return horizonY;
}

// -------------------------------------------------------------------------
// STAGE STYLE 5: 80s OUTRUN SYNTH SUN & PERSPECTIVE CYBER GRID
// -------------------------------------------------------------------------
function renderSynthGridStage(pal, bass, mid, treble) {
  const horizonY = Math.floor(H * 0.62);

  // 1. Parallax Starfield
  for (const st of stars) {
    st.x -= st.speed * (0.7 + smoothBass * 0.8);
    if (st.x < 0) {
      st.x = W;
      st.y = Math.random() * (H * 0.62);
    }
    ctx.fillStyle = (treble > 0.38 && Math.random() > 0.5) ? pal.accent_neon : "#ffffff";
    ctx.fillRect(Math.floor(st.x), Math.floor(st.y), st.size, st.size);
  }

  // 2. Retro Pulsing Segmented Synth Sun
  const sunRadius = Math.floor(22 + smoothBass * 8);
  const sunCenterX = Math.floor(W * 0.42);
  const sunCenterY = horizonY - 6;

  for (let y = -sunRadius; y <= 6; y++) {
    if ((y + Math.floor(frameTick * 0.15)) % 4 === 0 && y > 0) continue;
    const halfWidth = Math.floor(Math.sqrt(Math.max(0, sunRadius * sunRadius - y * y)));
    ctx.fillStyle = y < -4 ? pal.pixel_highlight : pal.accent_neon;
    ctx.fillRect(sunCenterX - halfWidth, sunCenterY + y, halfWidth * 2, 1);
  }

  // 3. Pixelated Mountain Silhouette
  ctx.fillStyle = pal.bg_secondary;
  for (let x = 0; x < W; x += 4) {
    const mHeight = Math.floor(
      10 +
      Math.sin((x + stageScrollX * 0.5) * 0.045) * 8 +
      Math.cos((x - stageScrollX * 0.25) * 0.09) * 5 +
      smoothBass * 5
    );
    ctx.fillRect(x, horizonY - mHeight, 4, mHeight);
  }

  // 4. Perspective Cyber Grid Floor
  ctx.fillStyle = pal.bg_secondary;
  ctx.fillRect(0, horizonY, W, H - horizonY);
  ctx.fillStyle = pal.accent_neon;
  ctx.fillRect(0, horizonY, W, 2);

  const gridOffset = (stageScrollX * 0.8) % 10;
  for (let i = 1; i <= 7; i++) {
    const yPos = horizonY + Math.floor(Math.pow(i + gridOffset / 10, 1.75) * 2.4);
    if (yPos < H) {
      ctx.fillStyle = i % 2 === 0 ? pal.accent_neon : pal.pixel_highlight;
      ctx.fillRect(0, yPos, W, 1);
    }
  }

  for (let x = -W; x <= W * 2; x += 28) {
    const topX = sunCenterX + (x - sunCenterX) * 0.18;
    ctx.strokeStyle = pal.accent_neon;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(Math.floor(topX), horizonY);
    ctx.lineTo(Math.floor(x), H);
    ctx.stroke();
  }

  // 5. Segmented Horizon Equalizer with Peak Caps
  drawWinampSegmentedBars(horizonY, 36, bass, pal, false);

  return horizonY;
}

// -------------------------------------------------------------------------
// STAGE STYLE 6: ARCADE SPACE SHOOTER / SHMUP (Galaga / Gradius / R-Type)
// -------------------------------------------------------------------------
function renderShmupStage(pal, bass, mid, treble) {
  const topWallY = 24;
  const bottomWallY = 108;

  // 1. Multi-Speed Warp Starfield across Deep Space
  for (let idx = 0; idx < stars.length; idx++) {
    const st = stars[idx];
    st.x -= st.speed * (1.8 + smoothBass * 2.4);
    if (st.x < 0) {
      st.x = W;
      st.y = topWallY + ((idx * 17) % (bottomWallY - topWallY - 4));
    }
    const sy = topWallY + 2 + (Math.floor(st.y) % (bottomWallY - topWallY - 4));
    ctx.fillStyle = (idx % 3 === 0) ? pal.accent_neon : (idx % 5 === 0 ? pal.pixel_highlight : "#ffffff");
    const streakW = st.size > 1 ? Math.min(5, Math.floor(2 + smoothBass * 4)) : 1;
    ctx.fillRect(Math.floor(st.x), sy, streakW, st.size);
  }

  // 2. Scrolling Ringed Pixel Planet in the Distant Nebula
  const planetX = W - 60 - (Math.floor(stageScrollX * 0.25) % (W + 80)) + 40;
  const planetY = 54;
  const pRad = 13;
  for (let py = -pRad; py <= pRad; py++) {
    const hw = Math.floor(Math.sqrt(Math.max(0, pRad * pRad - py * py)));
    ctx.fillStyle = (py + pRad) % 4 === 0 ? pal.pixel_highlight : pal.bg_secondary;
    ctx.fillRect(planetX - hw, planetY + py, hw * 2, 1);
  }
  // Angled planetary ring
  ctx.fillStyle = pal.accent_neon;
  ctx.fillRect(planetX - 20, planetY + 1, 40, 2);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(planetX - 16, planetY, 32, 1);

  // 3. Gradius / R-Type Biomechanical Tech Trench Walls (Top & Bottom)
  const trenchScroll = Math.floor(stageScrollX * 1.4);
  ctx.fillStyle = pal.bg_secondary;
  ctx.fillRect(0, 16, W, topWallY - 16);
  ctx.fillRect(0, bottomWallY, W, H - bottomWallY);

  ctx.fillStyle = pal.accent_neon;
  ctx.fillRect(0, topWallY, W, 2);
  ctx.fillRect(0, bottomWallY, W, 2);

  for (let x = -(trenchScroll % 24); x < W + 24; x += 24) {
    // Upper & lower armored turret ribs and glowing power cores
    ctx.fillStyle = "#05030a";
    ctx.fillRect(x + 2, 17, 10, 6);
    ctx.fillRect(x + 2, bottomWallY + 3, 14, 10);

    const coreGlow = (Math.floor(frameTick / 10 + x) % 2 === 0) ? pal.pixel_highlight : pal.accent_neon;
    ctx.fillStyle = coreGlow;
    ctx.fillRect(x + 15, topWallY - 4, 4, 3);
    ctx.fillRect(x + 18, bottomWallY + 4, 4, 4);
  }

  // 4. Gradius-Style Power-Up Capsule Meter Bar at Top-Left ([SPEED][MISSILE][LASER][OPTION][SHIELD])
  const hudX = 12;
  const hudY = 19;
  const activeSlot = Math.floor(frameTick / 20) % 5;
  for (let slot = 0; slot < 5; slot++) {
    const sx = hudX + slot * 26;
    ctx.fillStyle = "#000000";
    ctx.fillRect(sx - 1, hudY - 1, 24, 5);
    ctx.fillStyle = (slot === activeSlot) ? "#facc15" : pal.bg_secondary;
    ctx.fillRect(sx, hudY, 22, 3);
    ctx.fillStyle = (slot === activeSlot) ? "#000000" : pal.accent_neon;
    ctx.fillRect(sx + 2, hudY + 1, 18, 1);
  }

  return bottomWallY;
}

// -------------------------------------------------------------------------
// STAGE STYLE 7: 16-BIT JRPG BOSS BATTLE (Final Fantasy / Chrono Trigger / EarthBound)
// -------------------------------------------------------------------------
function renderJrpgBattleStage(pal, bass, mid, treble) {
  const arenaTopY = 74;
  const arenaBottomY = 118;

  // 1. Psychedelic EarthBound-Style Oscillating Sine Wave Battle Backdrop
  for (let y = 18; y < arenaTopY; y += 2) {
    const waveShift = Math.floor(Math.sin(frameTick * 0.05 + y * 0.14) * (10 + smoothMid * 14));
    const band = Math.floor((y + waveShift + frameTick * 0.4) / 6) % 3;
    ctx.fillStyle = band === 0 ? pal.bg_secondary : (band === 1 ? pal.accent_neon : pal.pixel_highlight);
    ctx.globalAlpha = band === 0 ? 0.55 : 0.28;
    ctx.fillRect(0, y, W, 2);
  }
  ctx.globalAlpha = 0.62;

  // 2. Perspective Battleground Floor & Glowing Summoning Circle
  ctx.fillStyle = pal.bg_secondary;
  ctx.fillRect(0, arenaTopY, W, H - arenaTopY);
  ctx.fillStyle = pal.accent_neon;
  ctx.fillRect(0, arenaTopY, W, 2);

  // Perspective tile grid on battle arena floor
  ctx.fillStyle = "rgba(0, 0, 0, 0.45)";
  for (let y = arenaTopY + 6; y < arenaBottomY; y += 8) {
    ctx.fillRect(0, y, W, 1);
  }
  for (let x = 0; x < W; x += 22) {
    ctx.beginPath();
    ctx.moveTo(x, arenaTopY);
    ctx.lineTo(x - 14, arenaBottomY);
    ctx.strokeStyle = "rgba(0, 0, 0, 0.45)";
    ctx.stroke();
  }

  // Pulsing arcane boss ring under the left side of the arena
  const ringW = Math.floor(46 + smoothBass * 12);
  ctx.fillStyle = pal.pixel_highlight;
  ctx.fillRect(42 - Math.floor(ringW / 2), arenaTopY + 14, ringW, 4);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(42 - Math.floor(ringW / 3), arenaTopY + 15, Math.floor(ringW * 0.66), 2);

  // 3. Classic Bevelled Blue 16-Bit JRPG Command & Party Status Window (Top of Viewport)
  const winX = 10;
  const winY = 20;
  const winW = 166;
  const winH = 14;
  // Classic FF4/FF6 royal blue window with crisp white border
  ctx.fillStyle = "#000000";
  ctx.fillRect(winX - 1, winY - 1, winW + 2, winH + 2);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(winX, winY, winW, winH);
  ctx.fillStyle = "#1e3a8a";
  ctx.fillRect(winX + 1, winY + 1, winW - 2, winH - 2);
  // Divider between Command Menu (left) and Party HP/MP (right)
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(winX + 54, winY + 1, 1, winH - 2);

  // Animated Command Cursor + 3 Command Rows (FIGHT / MAGIC / ITEM)
  const activeCmd = Math.floor(frameTick / 28) % 3;
  for (let c = 0; c < 3; c++) {
    const cy = winY + 3 + c * 3;
    if (c === activeCmd) {
      ctx.fillStyle = "#facc15";
      ctx.fillRect(winX + 4, cy, 3, 2);
    }
    ctx.fillStyle = c === activeCmd ? "#ffffff" : "#93c5fd";
    ctx.fillRect(winX + 9, cy, 38 - (c * 5), 2);
  }

  // 3 Party Member HP / MP Bars on the right side of the RPG window
  for (let p = 0; p < 3; p++) {
    const py = winY + 3 + p * 3;
    const hpFill = Math.max(18, Math.floor(48 * (0.92 - ((p * 0.12) + smoothBass * 0.15))));
    const mpFill = Math.max(12, Math.floor(28 * (0.85 - smoothTreble * 0.2)));
    // Name glyph
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(winX + 59, py, 14, 2);
    // HP Bar
    ctx.fillStyle = "#0f172a";
    ctx.fillRect(winX + 77, py, 48, 2);
    ctx.fillStyle = "#4ade80";
    ctx.fillRect(winX + 77, py, hpFill, 2);
    // MP Bar
    ctx.fillStyle = "#0f172a";
    ctx.fillRect(winX + 130, py, 30, 2);
    ctx.fillStyle = "#38bdf8";
    ctx.fillRect(winX + 130, py, mpFill, 2);
  }

  return arenaTopY + 16;
}

// -------------------------------------------------------------------------
// STAGE STYLE 8: GOTHIC CASTLE KEEP (Castlevania / Ghosts 'n Goblins / Bowser's Keep)
// -------------------------------------------------------------------------
function renderDungeonCastleStage(pal, bass, mid, treble) {
  const wallTopY = 20;
  const bridgeY = 84;
  const lavaTopY = 102;

  // 1. Giant Blood / Full Moon & Drifting Night Clouds visible through Gothic Arches
  const moonX = 52;
  const moonY = 44;
  const moonR = Math.floor(14 + smoothBass * 2);
  for (let my = -moonR; my <= moonR; my++) {
    const hw = Math.floor(Math.sqrt(Math.max(0, moonR * moonR - my * my)));
    ctx.fillStyle = my < -3 ? "#fef08a" : "#facc15";
    ctx.fillRect(moonX - hw, moonY + my, hw * 2, 1);
  }

  // 2. Scrolling Stonebrick Castle Wall, Gothic Windows & Flickering Wall Torches
  const castleScroll = Math.floor(stageScrollX * 0.75);
  const archSpan = 64;
  let archX = -(castleScroll % archSpan);
  let archIdx = Math.floor(castleScroll / archSpan);

  while (archX < W + archSpan) {
    // Solid stone pillar between gothic windows
    ctx.fillStyle = pal.bg_secondary;
    ctx.fillRect(archX, wallTopY, 38, bridgeY - wallTopY);
    // Arch header lintel across window opening
    ctx.fillRect(archX + 38, wallTopY, 26, 12);
    ctx.fillRect(archX + 38, wallTopY + 12, 4, 6);
    ctx.fillRect(archX + 60, wallTopY + 12, 4, 6);

    // Gothic window iron mullions
    ctx.fillStyle = pal.accent_neon;
    ctx.fillRect(archX + 50, wallTopY + 12, 2, bridgeY - (wallTopY + 12));
    ctx.fillRect(archX + 38, wallTopY + 32, 26, 2);

    // Stone mortar lines on the pillar
    ctx.fillStyle = "rgba(0, 0, 0, 0.5)";
    for (let y = wallTopY + 8; y < bridgeY; y += 8) {
      ctx.fillRect(archX, y, 38, 1);
      const brickShift = ((y - wallTopY) % 16 === 0) ? 8 : 18;
      ctx.fillRect(archX + brickShift, y, 1, 8);
    }

    // Flickering Iron Wall Torch Sconce on each pillar
    const torchX = archX + 16;
    const torchY = wallTopY + 28;
    ctx.fillStyle = "#1f2937";
    ctx.fillRect(torchX + 1, torchY + 4, 4, 8);
    ctx.fillStyle = "#78350f";
    ctx.fillRect(torchX, torchY + 2, 6, 3);

    const tFlameH = 5 + Math.floor(smoothBass * 6) + (Math.floor(frameTick / 5 + archIdx) % 3);
    ctx.fillStyle = pal.pixel_highlight;
    ctx.fillRect(torchX, torchY - tFlameH + 2, 6, tFlameH);
    ctx.fillStyle = "#facc15";
    ctx.fillRect(torchX + 1, torchY - Math.max(2, tFlameH - 2) + 2, 4, Math.max(2, tFlameH - 2));
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(torchX + 2, torchY, 2, 2);

    archX += archSpan;
    archIdx++;
  }

  // 3. Castle Battlement Bridge Deck
  ctx.fillStyle = pal.accent_neon;
  ctx.fillRect(0, bridgeY, W, 2);
  ctx.fillStyle = pal.bg_secondary;
  ctx.fillRect(0, bridgeY + 2, W, lavaTopY - (bridgeY + 2));

  // Crenellations & bridge arches along the stone walkway
  ctx.fillStyle = "#05030a";
  for (let x = -(castleScroll % 16); x < W + 16; x += 16) {
    ctx.fillRect(x + 4, bridgeY + 6, 8, lavaTopY - (bridgeY + 6));
  }

  // 4. Bubbling Audio-Reactive Lava / Hellfire Moat below the Bridge
  ctx.fillStyle = "#450a0a";
  ctx.fillRect(0, lavaTopY, W, H - lavaTopY);
  for (let x = 0; x < W; x += 4) {
    const wave = Math.floor(Math.sin(frameTick * 0.09 + x * 0.08) * (2 + smoothBass * 4));
    ctx.fillStyle = pal.pixel_highlight;
    ctx.fillRect(x, lavaTopY + wave, 4, 6);
    ctx.fillStyle = "#facc15";
    ctx.fillRect(x, lavaTopY + wave, 4, 2);
  }

  return bridgeY;
}

function renderArcadeCanvas() {
  frameTick++;
  const { bass, mid, treble } = getAudioEnergy();
  // Smooth audio envelopes so sprites and camera scroll glide smoothly without frame-to-frame jitter
  smoothBass += (bass - smoothBass) * 0.07;
  smoothMid += (mid - smoothMid) * 0.07;
  smoothTreble += (treble - smoothTreble) * 0.07;
  stageScrollX += 0.35 + smoothBass * 0.28;
  const pal = (currentScene && currentScene.palette) ? currentScene.palette : {
    bg_primary: "#090518",
    bg_secondary: "#1d1138",
    accent_neon: "#00f5d4",
    pixel_highlight: "#ff2a85"
  };

  const colorLookup = {
    1: "#070510",
    2: pal.accent_neon,
    3: pal.pixel_highlight,
    4: "#ffffff"
  };

  // Clear primary background
  ctx.fillStyle = pal.bg_primary;
  ctx.fillRect(0, 0, W, H);

  const sprites = (currentScene && Array.isArray(currentScene.sprites) && currentScene.sprites.length > 0)
    ? currentScene.sprites
    : [];

  const activeStyle = resolveActiveVizStyle(sprites);
  let horizonY = Math.floor(H * 0.62);

  // Render background stage slightly muted so foreground pixel art sprites pop with high contrast
  ctx.save();
  ctx.globalAlpha = 0.62;
  if (activeStyle === 'side_scroller') {
    horizonY = renderSideScrollerStage(pal, bass, mid, treble);
  } else if (activeStyle === 'beat_em_up') {
    horizonY = renderBeatEmUpStage(pal, bass, mid, treble);
  } else if (activeStyle === 'shmup') {
    horizonY = renderShmupStage(pal, bass, mid, treble);
  } else if (activeStyle === 'jrpg_battle') {
    horizonY = renderJrpgBattleStage(pal, bass, mid, treble);
  } else if (activeStyle === 'dungeon_castle') {
    horizonY = renderDungeonCastleStage(pal, bass, mid, treble);
  } else if (activeStyle === 'winamp_analyzer') {
    horizonY = renderWinampAnalyzerStage(pal, bass, mid, treble);
  } else if (activeStyle === 'winamp_fire') {
    horizonY = renderWinampFireStage(pal, bass, mid, treble);
  } else if (activeStyle === 'city_cruiser') {
    horizonY = renderCityCruiserStage(pal, bass, mid, treble);
  } else {
    horizonY = renderSynthGridStage(pal, bass, mid, treble);
  }
  ctx.restore();

  // Subtle atmospheric depth veil separating background visualizer from foreground sprites
  ctx.fillStyle = "rgba(4, 2, 12, 0.28)";
  ctx.fillRect(0, 0, W, H);

  // Render AI-Generated Custom 8-Bit Sprites adapted to the active stage!
  for (let i = 0; i < spriteActors.length; i++) {
    if (sprites.length === 0) break;
    // In JRPG Boss Battle mode, keep a clean 1 Giant Boss + 3 Party Heroes formation
    if (activeStyle === 'jrpg_battle' && i >= 4) break;
    // In Space Shmup mode, keep 1 Hero Ship + 6 Galaga Wave Enemies
    if (activeStyle === 'shmup' && i >= 7) break;

    const actor = spriteActors[i];
    const spriteDef = sprites[i % sprites.length];
    const behavior = spriteDef.behavior || 'bounce';
    let scale = 2;
    let drawX = actor.x;
    let drawY = actor.y;
    let flipX = false;

    if (activeStyle === 'beat_em_up' && behavior !== 'rain') {
      // 2.5D Belt-Scroller Brawler Planes: smooth, deliberate footwork & rhythmic sparring
      const pairIdx = Math.floor(i / 2);
      const isLeftFighter = (i % 2 === 0);
      const laneBaseY = 58 + (pairIdx % 4) * 9; // Depth-sorted street lanes kept well above the Now Playing HUD
      const centerX = 24 + (pairIdx % 4) * 34 + Math.sin(frameTick * 0.015 + pairIdx) * 6;
      // Smooth continuous footwork wave: fighters step in and out smoothly without snapping
      const brawlWave = Math.sin(frameTick * 0.038 + pairIdx * 1.7);
      const spacing = 11 - brawlWave * 7; // Ranges smoothly from 4px (close clash) to 18px (footsies)
      drawX = isLeftFighter ? (centerX - spacing) : (centerX + spacing);

      // Gentle fighter stance bob + occasional smooth jump-kick arc
      const jumpWave = Math.max(0, Math.sin(frameTick * 0.032 + i * 1.9) - 0.55) / 0.45;
      const stanceBob = Math.abs(Math.sin(frameTick * 0.06 + i)) * 2;
      const jumpKick = Math.floor(stanceBob + jumpWave * (10 + smoothBass * 6));
      drawY = laneBaseY - jumpKick;
      flipX = !isLeftFighter;

      // Draw 2.5D Belt-Scroller Ground Drop Shadow on the asphalt
      ctx.fillStyle = "rgba(0, 0, 0, 0.65)";
      ctx.fillRect(Math.round(drawX) + 4, laneBaseY + 29, 24, 4);

      // Draw Arcade Hit Spark / Impact Burst when fighters step into close range
      if (!isLeftFighter && brawlWave > 0.78) {
        const sparkX = Math.round(centerX + 14);
        const sparkY = Math.round(laneBaseY + 12 - jumpKick * 0.4);
        ctx.fillStyle = pal.pixel_highlight;
        ctx.fillRect(sparkX - 5, sparkY - 1, 11, 3);
        ctx.fillRect(sparkX - 1, sparkY - 5, 3, 11);
        ctx.fillStyle = "#ffffff";
        ctx.fillRect(sparkX - 2, sparkY - 2, 5, 5);
      }
    } else if (activeStyle === 'shmup') {
      if (i === 0) {
        // Hero Ship piloting on the left with Gradius 'Option' drones & audio-reactive Twin Lasers
        drawX = 16 + Math.sin(frameTick * 0.04) * 6;
        drawY = 52 + Math.sin(frameTick * 0.055) * 18;
        flipX = false;

        // Exhaust thruster flame behind hero ship
        const thrustW = 5 + Math.floor(smoothBass * 8) + (frameTick % 3);
        ctx.fillStyle = pal.pixel_highlight;
        ctx.fillRect(Math.round(drawX) - thrustW, Math.round(drawY) + 13, thrustW, 6);
        ctx.fillStyle = "#facc15";
        ctx.fillRect(Math.round(drawX) - Math.max(2, thrustW - 3), Math.round(drawY) + 14, Math.max(2, thrustW - 3), 4);

        // Glowing Gradius "Option" satellite pods trailing above & below
        const optUpperY = Math.round(52 + Math.sin((frameTick - 12) * 0.055) * 18) - 8;
        const optLowerY = Math.round(52 + Math.sin((frameTick - 12) * 0.055) * 18) + 34;
        ctx.fillStyle = "#facc15";
        ctx.fillRect(Math.round(drawX) + 6, optUpperY, 6, 4);
        ctx.fillRect(Math.round(drawX) + 6, optLowerY, 6, 4);

        // Rapid-fire twin pixel laser bolts streaking across the space lane
        const laserOffset = (frameTick * 6) % 36;
        for (let lx = Math.round(drawX) + 32 + laserOffset; lx < 168; lx += 36) {
          ctx.fillStyle = "#ffffff";
          ctx.fillRect(lx, Math.round(drawY) + 9, 14, 2);
          ctx.fillRect(lx, Math.round(drawY) + 21, 14, 2);
          ctx.fillStyle = pal.accent_neon;
          ctx.fillRect(lx - 2, Math.round(drawY) + 8, 18, 1);
          ctx.fillRect(lx - 2, Math.round(drawY) + 23, 18, 1);
        }
      } else {
        // Galaga / R-Type Enemy Squadron on the right side of the open viewport
        const eIdx = i - 1;
        const col = eIdx % 3;
        const row = Math.floor(eIdx / 3);
        const swayX = Math.sin(frameTick * 0.04 + row * 1.2) * 8;
        const swayY = Math.cos(frameTick * 0.05 + col * 1.1) * 5;
        drawX = 78 + col * 26 + swayX;
        drawY = 34 + row * 32 + swayY;
        flipX = true;

        // Impact explosion burst on enemy squadron when bass peaks
        if (smoothBass > 0.28 && (Math.floor(frameTick / 10) % 6 === eIdx)) {
          const exX = Math.round(drawX) + 8;
          const exY = Math.round(drawY) + 14;
          ctx.fillStyle = "#facc15";
          ctx.fillRect(exX - 6, exY - 2, 13, 5);
          ctx.fillRect(exX - 2, exY - 6, 5, 13);
          ctx.fillStyle = "#ffffff";
          ctx.fillRect(exX - 3, exY - 3, 7, 7);
        }
      }
    } else if (activeStyle === 'jrpg_battle') {
      if (i === 0) {
        // Giant 3x-Scaled Boss Monster on the left side of the battle arena!
        scale = 3;
        drawX = 16 + Math.sin(frameTick * 0.035) * 4;
        drawY = 42 + Math.cos(frameTick * 0.045) * 3;
        flipX = false;

        // Boss ground shadow
        ctx.fillStyle = "rgba(0, 0, 0, 0.65)";
        ctx.fillRect(Math.round(drawX) + 4, 88, 40, 5);
      } else {
        // 3 Party Heroes lined up in classic Final Fantasy right-hand formation
        const heroIdx = i - 1; // 0, 1, 2
        const activeAttacker = Math.floor(frameTick / 36) % 3;
        const attackPhase = (frameTick % 36) / 36;
        const isSteppingForward = (heroIdx === activeAttacker && attackPhase < 0.55);
        const stepOffset = isSteppingForward ? Math.sin((attackPhase / 0.55) * Math.PI) * 16 : 0;

        const baseHeroX = 122 + heroIdx * 10;
        const baseHeroY = 38 + heroIdx * 20;
        drawX = baseHeroX - stepOffset;
        drawY = baseHeroY - (isSteppingForward ? Math.floor(smoothBass * 4) : 0);
        flipX = true;

        // Hero shadow
        ctx.fillStyle = "rgba(0, 0, 0, 0.6)";
        ctx.fillRect(Math.round(drawX) + 4, baseHeroY + 29, 24, 3);

        // Slash effect & floating "999" pixel damage popup over the Boss at peak of attack!
        if (isSteppingForward && attackPhase > 0.2 && attackPhase < 0.48) {
          const hitX = 38;
          const hitY = 56;
          // Diagonal slash beam across the Boss
          ctx.fillStyle = "#ffffff";
          for (let s = -10; s <= 10; s += 2) {
            ctx.fillRect(hitX + s, hitY + s, 3, 3);
          }
          // Crisp floating pixel "999" damage indicator above the Boss
          const popY = 36 - Math.floor((attackPhase - 0.2) * 18);
          for (let d = 0; d < 3; d++) {
            const dx = 28 + d * 7;
            ctx.fillStyle = "#000000";
            ctx.fillRect(dx - 1, popY - 1, 7, 9);
            ctx.fillStyle = "#facc15";
            ctx.fillRect(dx, popY, 5, 7);
            ctx.fillStyle = "#000000";
            ctx.fillRect(dx + 1, popY + 1, 3, 2);
            ctx.fillRect(dx + 1, popY + 4, 2, 2);
          }
        }
      }
    } else if (activeStyle === 'dungeon_castle' && behavior !== 'rain') {
      if (behavior === 'float' || behavior === 'starfield') {
        // Castlevania Medusa-head / bat sine-wave flight across upper gothic arches
        actor.x -= (0.65 + smoothBass * 0.4);
        if (actor.x < -32) actor.x = 180;
        drawX = actor.x;
        drawY = 34 + Math.sin(frameTick * 0.06 + i * 1.5) * 12;
        flipX = true;
      } else {
        // Heroes & monsters patrolling and leaping across the stone castle bridge (kept inside open viewport)
        const spanW = 142;
        const patrolWave = ((frameTick * 0.65 + i * 38) % (spanW * 2));
        const goingLeft = patrolWave > spanW;
        drawX = 12 + (goingLeft ? (spanW * 2 - patrolWave) : patrolWave);
        const jumpArc = Math.floor(Math.abs(Math.sin(frameTick * 0.048 + i * 1.4)) * (8 + smoothBass * 10));
        drawY = (horizonY - 32) - jumpArc;
        flipX = goingLeft;
      }
    } else if (activeStyle === 'side_scroller' && (behavior === 'dancers' || behavior === 'scroll' || behavior === 'bounce')) {
      // In Side-Scroller mode, characters run and jump in a cleanly spaced formation without overlapping
      drawX = ((frameTick * (0.48 + smoothBass * 0.15) + i * 72) % 216) - 24;
      const jumpArc = Math.floor(Math.abs(Math.sin(frameTick * 0.045 + i * 1.6)) * (8 + smoothBass * 10));
      drawY = (horizonY - 32) - jumpArc;
      flipX = false;
    } else if (behavior === 'bounce') {
      actor.x += actor.vx * (0.55 + smoothBass * 0.35);
      if (actor.x < 12 || actor.x > W - 44) actor.vx *= -1;
      drawX = actor.x;
      drawY = (horizonY - 38) - Math.abs(Math.sin(frameTick * 0.045 + actor.phase)) * (12 + smoothBass * 10);
      flipX = actor.vx < 0;
    } else if (behavior === 'scroll') {
      actor.x -= (0.6 + smoothBass * 0.45);
      if (actor.x < -36) actor.x = W + 20;
      drawX = actor.x;
      drawY = horizonY - 26 + Math.sin(frameTick * 0.05 + actor.phase) * 2.5;
    } else if (behavior === 'rain') {
      actor.y += (0.55 + smoothTreble * 0.65);
      if (actor.y > H - 20) {
        actor.y = -24;
        actor.x = (actor.x + 53) % (W - 32);
      }
      drawX = actor.x;
      drawY = actor.y;
    } else if (behavior === 'dancers') {
      drawX = 28 + i * 36;
      const jump = Math.floor(Math.abs(Math.sin(frameTick * 0.055 + i)) * (6 + smoothBass * 8));
      drawY = horizonY - 28 - jump;
      flipX = Math.floor(frameTick / 32 + i) % 2 === 0;
    } else {
      // 'float' or 'starfield'
      drawX = actor.x + Math.sin(frameTick * 0.025 + actor.phase) * 14;
      drawY = 20 + (i * 16) % Math.max(24, horizonY - 45) + Math.cos(frameTick * 0.03 + actor.phase) * (6 + smoothMid * 6);
    }

    drawPixelMatrix(spriteDef.matrix, drawX, drawY, scale, colorLookup, flipX);
  }

  requestAnimationFrame(renderArcadeCanvas);
}

requestAnimationFrame(renderArcadeCanvas);
