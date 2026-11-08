// =========================================================================
// 1. STATE & THEME PALETTE MANAGEMENT
// =========================================================================
let appState = null;
let currentScene = {
  stage_title: "STAGE 01: NEON SYNTHWAVE ARENA",
  flavor_text: "A wild 8-BIT AUTODJ appeared! Bass cannons charged to 100%!",
  palette: {
    bg_primary: "#090518",
    bg_secondary: "#1d1138",
    accent_neon: "#00f5d4",
    pixel_highlight: "#ff2a85"
  },
  sprites: []
};

function applyPalette(palette) {
  if (!palette) return;
  const root = document.documentElement;
  if (palette.bg_primary) root.style.setProperty('--bg-primary', palette.bg_primary);
  if (palette.bg_secondary) root.style.setProperty('--bg-secondary', palette.bg_secondary);
  if (palette.accent_neon) root.style.setProperty('--accent-neon', palette.accent_neon);
  if (palette.pixel_highlight) root.style.setProperty('--pixel-highlight', palette.pixel_highlight);
}

function formatTime(sec) {
  if (!sec || isNaN(sec) || sec < 0) return "00:00";
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

// =========================================================================
// 2. DUAL-DECK WEB AUDIO API ENGINE + 6-SECOND EQUAL-POWER CROSSFADER
// =========================================================================
const deckAEl = document.getElementById('deckA');
const deckBEl = document.getElementById('deckB');

let audioCtx = null;
let analyser = null;
let gainNodeA = null;
let gainNodeB = null;
let masterGainNode = null;
let masterVolumePct = 50;
localStorage.removeItem('jukebox_master_vol_pct');
let freqData = new Uint8Array(64);
let timeData = new Uint8Array(64);

let activeDeck = 'A'; // 'A' or 'B'
let activeVideoId = null;
let activeTrackAudioReady = false;
let preloadedNextVideoId = null;
let isCrossfading = false;
let crossfadeTimer = null;
let advanceRequestedForId = null;

// Karaoke Mode (Zero-Download YouTube IFrame Lyric Video Player)
window.karaokeModeActive = false;
let activeKaraokeTrackId = null;
let activeKaraokeYtId = null;
let inflightKaraokeResolveId = null;
let currentKaraokeTrackObj = null;
let karaokeFailedMap = {}; // { [queueVideoId]: Set<ytVideoId> }
let isAdvancingKaraoke = false;
let karaokeCurTime = 0;
let karaokeDurTime = 0;
let karaokeIsPaused = false;

function sendKaraokeCommand(func, args = []) {
  const iframe = document.getElementById('karaokeYtIframe');
  if (!iframe || !iframe.contentWindow) return;
  try {
    iframe.contentWindow.postMessage(
      JSON.stringify({ event: 'command', func: func, args: args }),
      '*'
    );
  } catch (err) {}
}

function registerKaraokeIframeBridge() {
  const iframe = document.getElementById('karaokeYtIframe');
  if (!iframe || !iframe.contentWindow) return;
  try {
    iframe.contentWindow.postMessage(
      JSON.stringify({ event: 'listening', id: 'karaokeYtIframe' }),
      '*'
    );
  } catch (err) {}
}

function setMasterVolumePct(pct) {
  masterVolumePct = Math.max(0, Math.min(100, Number(pct) ?? 50));
  if (masterGainNode) {
    // OS PipeWire / system mixer handles 0-100% attenuation; keep Web Audio at clean 1.0 unity gain (0.0 when muted)
    masterGainNode.gain.value = masterVolumePct === 0 ? 0.0 : 1.0;
  }
  if (window.karaokeModeActive) {
    if (masterVolumePct === 0) {
      sendKaraokeCommand('mute', []);
    } else {
      sendKaraokeCommand('unMute', []);
      sendKaraokeCommand('setVolume', [100]);
    }
  }
}

function initAudioGraph() {
  if (audioCtx) return;
  const AudioContextClass = window.AudioContext || window.webkitAudioContext;
  audioCtx = new AudioContextClass();

  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 128;
  analyser.smoothingTimeConstant = 0.78;
  freqData = new Uint8Array(analyser.frequencyBinCount);
  timeData = new Uint8Array(analyser.frequencyBinCount);

  gainNodeA = audioCtx.createGain();
  gainNodeB = audioCtx.createGain();
  gainNodeA.gain.value = 1.0;
  gainNodeB.gain.value = 0.0;

  // Clean unity gain (no digital overdrive or heavy compressor distortion; OS mixer controls system volume)
  masterGainNode = audioCtx.createGain();
  masterGainNode.gain.value = masterVolumePct === 0 ? 0.0 : 1.0;

  const sourceA = audioCtx.createMediaElementSource(deckAEl);
  const sourceB = audioCtx.createMediaElementSource(deckBEl);

  sourceA.connect(gainNodeA);
  sourceB.connect(gainNodeB);

  gainNodeA.connect(analyser);
  gainNodeB.connect(analyser);
  analyser.connect(masterGainNode);
  masterGainNode.connect(audioCtx.destination);
}

async function ensureAudioUnlocked() {
  initAudioGraph();
  if (audioCtx && audioCtx.state === 'suspended') {
    try {
      await audioCtx.resume();
    } catch (err) {
      console.warn("AudioContext resume needs gesture:", err);
    }
  }
  if (audioCtx && audioCtx.state === 'running') {
    document.getElementById('audioUnlockBanner').classList.add('hidden');
  }
}

document.addEventListener('click', () => {
  ensureAudioUnlocked();
  const activeEl = activeDeck === 'A' ? deckAEl : deckBEl;
  if (activeEl.src && activeEl.paused) {
    activeEl.play().catch(() => {});
  }
});
document.addEventListener('keydown', () => ensureAudioUnlocked());
document.getElementById('unlockAudioBtn').addEventListener('click', () => ensureAudioUnlocked());

function getDeckElements() {
  return activeDeck === 'A'
    ? { currentEl: deckAEl, nextEl: deckBEl, currentGain: gainNodeA, nextGain: gainNodeB, nextDeckName: 'B' }
    : { currentEl: deckBEl, nextEl: deckAEl, currentGain: gainNodeB, nextGain: gainNodeA, nextDeckName: 'A' };
}

function loadAndPlayOnActiveDeck(track, forceReload = false) {
  if (!track || !track.video_id) return;
  ensureAudioUnlocked();

  activeVideoId = track.video_id;
  activeTrackAudioReady = !!track.audio_ready;
  const { currentEl, currentGain, nextGain } = getDeckElements();
  const targetUrl = `/api/audio/${track.video_id}`;

  if (forceReload || !currentEl.src.includes(targetUrl)) {
    currentEl.src = forceReload ? `${targetUrl}?t=${Date.now()}` : targetUrl;
    currentEl.load();
  }
  if (currentGain && nextGain) {
    currentGain.gain.value = 1.0;
    nextGain.gain.value = 0.0;
  }
  updateGainBars();

  const tryPlayActive = () => {
    currentEl.play().then(() => {
      document.getElementById('audioUnlockBanner').classList.add('hidden');
    }).catch(() => {
      if (audioCtx && audioCtx.state !== 'running') {
        document.getElementById('audioUnlockBanner').classList.remove('hidden');
      }
    });
  };
  currentEl.addEventListener('canplay', tryPlayActive, { once: true });
  tryPlayActive();

  if (track.scene) {
    currentScene = track.scene;
    applyPalette(currentScene.palette);
    updateStageHeader();
  }
}

function preloadNextDeck(nextTrack) {
  if (!nextTrack || !nextTrack.video_id || isCrossfading) return;
  if (!nextTrack.audio_ready) return;
  if (preloadedNextVideoId === nextTrack.video_id) return;

  const { nextEl } = getDeckElements();
  const targetUrl = `/api/audio/${nextTrack.video_id}`;
  nextEl.src = targetUrl;
  nextEl.load();
  preloadedNextVideoId = nextTrack.video_id;
}

function updateGainBars() {
  const gA = gainNodeA ? Math.round(gainNodeA.gain.value * 100) : (activeDeck === 'A' ? 100 : 0);
  const gB = gainNodeB ? Math.round(gainNodeB.gain.value * 100) : (activeDeck === 'B' ? 100 : 0);
  document.getElementById('deckAGainBar').style.width = `${gA}%`;
  document.getElementById('deckBGainBar').style.width = `${gB}%`;
  document.getElementById('activeDeckBadge').textContent =
    isCrossfading ? `X-FADE ${activeDeck} -> ${activeDeck === 'A' ? 'B' : 'A'}` : `DECK ${activeDeck} [LIVE]`;
}

async function triggerSixSecondCrossfade(forcedNextTrack = null, notifyBackendAdvance = true) {
  if (window.karaokeModeActive) {
    let target = forcedNextTrack;
    if (notifyBackendAdvance) {
      try {
        const res = await fetch('/api/advance', { method: 'POST' });
        const data = await res.json();
        if (data && data.now_playing) target = data.now_playing;
      } catch (err) {
        console.error("Failed to advance karaoke track:", err);
      }
    }
    if (target) {
      await loadAndPlayKaraokeTrack(target, false);
    }
    return;
  }
  if (isCrossfading) return;
  isCrossfading = true;

  document.getElementById('crossfadeNotice').classList.remove('hidden');
  document.getElementById('crossfadeBadge').textContent = "6S X-FADE ACTIVE";
  document.getElementById('crossfadeBadge').className = "px-2 py-1 border-2 border-pink-500 text-[9px] highlight-text";

  ensureAudioUnlocked();
  const { currentEl, nextEl, currentGain, nextGain, nextDeckName } = getDeckElements();

  let targetTrack = forcedNextTrack;
  if (notifyBackendAdvance) {
    try {
      const res = await fetch('/api/advance', { method: 'POST' });
      const data = await res.json();
      if (data && data.now_playing) {
        targetTrack = data.now_playing;
      }
    } catch (err) {
      console.error("Failed to advance track:", err);
    }
  }

  if (targetTrack && targetTrack.video_id) {
    activeVideoId = targetTrack.video_id;
    activeTrackAudioReady = !!targetTrack.audio_ready;
    const nextUrl = `/api/audio/${targetTrack.video_id}`;
    if (!nextEl.src.includes(nextUrl)) {
      nextEl.src = nextUrl;
      nextEl.load();
    }
    if (targetTrack.scene) {
      currentScene = targetTrack.scene;
      applyPalette(currentScene.palette);
      updateStageHeader();
    }
  }

  // Wait until nextEl has buffered enough audio to play (readyState >= 3)
  // so the outgoing track keeps playing with zero dead air while normalizing!
  if (nextEl.readyState < 3) {
    document.getElementById('crossfadeBadge').textContent = "BUFFERING NEXT...";
    let retries = 0;
    while (nextEl.readyState < 3 && retries < 3) {
      const ok = await new Promise((resolve) => {
        let resolved = false;
        const onReady = () => {
          if (resolved) return;
          resolved = true;
          cleanup();
          resolve(true);
        };
        const onErr = () => {
          if (resolved) return;
          resolved = true;
          cleanup();
          resolve(false);
        };
        const cleanup = () => {
          nextEl.removeEventListener('canplay', onReady);
          nextEl.removeEventListener('error', onErr);
        };
        nextEl.addEventListener('canplay', onReady, { once: true });
        nextEl.addEventListener('error', onErr, { once: true });
        setTimeout(onErr, 40000);
      });
      if (ok) break;
      retries++;
      if (targetTrack && targetTrack.video_id && retries < 3) {
        await new Promise(r => setTimeout(r, 2000));
        nextEl.src = `/api/audio/${targetTrack.video_id}?t=${Date.now()}`;
        nextEl.load();
      }
    }
    activeTrackAudioReady = true;
  }

  document.getElementById('crossfadeBadge').textContent = "6S X-FADE ACTIVE";
  nextEl.currentTime = 0;
  await nextEl.play().catch(err => console.warn("Next deck play warning:", err));

  // 6-second equal-power crossfade curve (60 steps @ 100ms)
  const durationMs = 6000;
  const steps = 60;
  const stepTime = durationMs / steps;
  let step = 0;

  if (crossfadeTimer) clearInterval(crossfadeTimer);
  crossfadeTimer = setInterval(() => {
    step++;
    const progress = Math.min(1, step / steps);
    // Equal-power crossfade: cos / sin curve
    const fadeOutGain = Math.cos(progress * 0.5 * Math.PI);
    const fadeInGain = Math.sin(progress * 0.5 * Math.PI);

    if (currentGain && nextGain) {
      currentGain.gain.value = Math.max(0, fadeOutGain);
      nextGain.gain.value = Math.min(1, fadeInGain);
    }
    updateGainBars();

    if (progress >= 1) {
      clearInterval(crossfadeTimer);
      crossfadeTimer = null;
      currentEl.pause();
      currentEl.currentTime = 0;
      activeDeck = nextDeckName;
      preloadedNextVideoId = null;
      isCrossfading = false;
      document.getElementById('crossfadeNotice').classList.add('hidden');
      document.getElementById('crossfadeBadge').textContent = "EBU R128 -14 LUFS";
      document.getElementById('crossfadeBadge').className = "px-2 py-1 border-2 border-gray-600 text-[9px] text-gray-400";
      updateGainBars();
    }
  }, stepTime);
}

// =========================================================================
// 2B. ZERO-DOWNLOAD YOUTUBE IFRAME KARAOKE PLAYER (DIRECT EMBED + POSTMESSAGE)
// =========================================================================
const karaokeIframeEl = document.getElementById('karaokeYtIframe');
if (karaokeIframeEl) {
  karaokeIframeEl.addEventListener('load', () => {
    if (!window.karaokeModeActive) return;
    const overlay = document.getElementById('karaokeLoadingOverlay');
    if (overlay) overlay.classList.add('hidden');
    document.getElementById('activeDeckBadge').textContent = 'KARAOKE [YT LIVE]';
    document.getElementById('bufferStatusBadge').textContent = '[NO-DL STREAM]';
    registerKaraokeIframeBridge();
    setTimeout(() => {
      registerKaraokeIframeBridge();
      setMasterVolumePct(masterVolumePct);
      sendKaraokeCommand('playVideo', []);
    }, 350);
  });
}

window.addEventListener('message', (event) => {
  if (!window.karaokeModeActive || typeof event.data !== 'string') return;
  try {
    const msg = JSON.parse(event.data);
    if (!msg || !msg.event) return;

    if (msg.event === 'onReady') {
      registerKaraokeIframeBridge();
      setMasterVolumePct(masterVolumePct);
      sendKaraokeCommand('playVideo', []);
    } else if (msg.event === 'infoDelivery' && msg.info) {
      if (typeof msg.info.currentTime === 'number') {
        karaokeCurTime = msg.info.currentTime;
      }
      if (typeof msg.info.duration === 'number' && msg.info.duration > 0) {
        karaokeDurTime = msg.info.duration;
      }
      if (typeof msg.info.playerState === 'number') {
        handleKaraokePlayerState(msg.info.playerState);
      }
    } else if (msg.event === 'onStateChange' && typeof msg.info === 'number') {
      handleKaraokePlayerState(msg.info);
    } else if (msg.event === 'onError' && currentKaraokeTrackObj) {
      console.warn('Karaoke YT Iframe error:', msg.info, 'for video:', activeKaraokeYtId);
      const qVid = currentKaraokeTrackObj.video_id;
      if (!karaokeFailedMap[qVid]) karaokeFailedMap[qVid] = new Set();
      if (activeKaraokeYtId) karaokeFailedMap[qVid].add(activeKaraokeYtId);
      if (karaokeFailedMap[qVid].size <= 4) {
        loadAndPlayKaraokeTrack(currentKaraokeTrackObj, true);
      } else {
        advanceKaraokeTrack();
      }
    }
  } catch (err) {}
});

function handleKaraokePlayerState(stateCode) {
  const overlay = document.getElementById('karaokeLoadingOverlay');
  if (stateCode === 1) {
    // PLAYING
    karaokeIsPaused = false;
    if (overlay) overlay.classList.add('hidden');
    document.getElementById('activeDeckBadge').textContent = 'KARAOKE [YT LIVE]';
    document.getElementById('bufferStatusBadge').textContent = '[NO-DL STREAM]';
  } else if (stateCode === 2) {
    // PAUSED
    karaokeIsPaused = true;
    document.getElementById('activeDeckBadge').textContent = 'KARAOKE [PAUSED]';
  } else if (stateCode === 0) {
    // ENDED
    advanceKaraokeTrack();
  }
}

async function loadAndPlayKaraokeTrack(track, forceReload = false) {
  if (!track || !track.video_id) return;
  currentKaraokeTrackObj = track;

  // Pause HTML5 dual-deck audio elements so only the karaoke video plays
  if (crossfadeTimer) {
    clearInterval(crossfadeTimer);
    crossfadeTimer = null;
  }
  isCrossfading = false;
  deckAEl.pause();
  deckBEl.pause();

  if (track.scene) {
    currentScene = track.scene;
    applyPalette(currentScene.palette);
    updateStageHeader();
  }

  const singerBadge = document.getElementById('karaokeSingerBadge');
  if (singerBadge) {
    singerBadge.textContent = track.is_seed ? 'MIC: AUTO-DJ' : `MIC: ${track.requested_by_name}`;
  }

  if (!forceReload && ((activeKaraokeTrackId === track.video_id && activeKaraokeYtId) || inflightKaraokeResolveId === track.video_id)) {
    return;
  }
  activeKaraokeTrackId = track.video_id;
  inflightKaraokeResolveId = track.video_id;
  karaokeCurTime = 0;
  karaokeDurTime = Number(track.duration || 210);
  karaokeIsPaused = false;

  const overlay = document.getElementById('karaokeLoadingOverlay');
  if (overlay) overlay.classList.remove('hidden');
  document.getElementById('activeDeckBadge').textContent = 'KARAOKE [LOADING]';
  document.getElementById('bufferStatusBadge').textContent = '[NO-DL STREAM]';

  const failedSet = karaokeFailedMap[track.video_id] || new Set();
  const excludeStr = Array.from(failedSet).join(',');
  let resolvedYtId = track.video_id;
  try {
    const url = `/api/karaoke/resolve?video_id=${encodeURIComponent(track.video_id)}&title=${encodeURIComponent(track.title || '')}&artist=${encodeURIComponent(track.artist || '')}&exclude=${encodeURIComponent(excludeStr)}`;
    const res = await fetch(url);
    if (res.ok) {
      const data = await res.json();
      if (data && data.karaoke_video_id) {
        resolvedYtId = data.karaoke_video_id;
      }
    }
  } catch (err) {
    console.warn('Karaoke resolve fallback:', err);
  } finally {
    if (inflightKaraokeResolveId === track.video_id) {
      inflightKaraokeResolveId = null;
    }
  }

  if (!window.karaokeModeActive || activeKaraokeTrackId !== track.video_id) return;
  activeKaraokeYtId = resolvedYtId;

  const iframe = document.getElementById('karaokeYtIframe');
  if (iframe) {
    const embedUrl = `https://www.youtube.com/embed/${encodeURIComponent(resolvedYtId)}?autoplay=1&enablejsapi=1&playsinline=1&rel=0&modestbranding=1&iv_load_policy=3&origin=${encodeURIComponent(location.origin)}`;
    if (iframe.src !== embedUrl) {
      iframe.src = embedUrl;
    } else {
      sendKaraokeCommand('seekTo', [0, true]);
      sendKaraokeCommand('playVideo', []);
      if (overlay) overlay.classList.add('hidden');
    }
  }
}

function stopKaraokePlayer() {
  activeKaraokeTrackId = null;
  activeKaraokeYtId = null;
  inflightKaraokeResolveId = null;
  karaokeCurTime = 0;
  karaokeDurTime = 0;
  const iframe = document.getElementById('karaokeYtIframe');
  if (iframe) {
    iframe.src = 'about:blank';
  }
}

async function advanceKaraokeTrack() {
  if (isAdvancingKaraoke) return;
  isAdvancingKaraoke = true;
  try {
    const res = await fetch('/api/advance', { method: 'POST' });
    const data = await res.json();
    if (data && data.now_playing && window.karaokeModeActive) {
      await loadAndPlayKaraokeTrack(data.now_playing, false);
    }
  } catch (err) {
    console.error('Failed to advance after karaoke track ended:', err);
  } finally {
    isAdvancingKaraoke = false;
  }
}

// Monitor active deck playback progress and trigger 6-second crossfade at T-6s (or Karaoke progress)
setInterval(() => {
  if (window.karaokeModeActive) {
    registerKaraokeIframeBridge();
    if (karaokeDurTime > 0) {
      const pct = Math.min(100, (karaokeCurTime / karaokeDurTime) * 100);
      document.getElementById('timeElapsed').textContent = formatTime(karaokeCurTime);
      document.getElementById('timeTotal').textContent = formatTime(karaokeDurTime);
      document.getElementById('progressBarFill').style.width = `${pct.toFixed(1)}%`;
    }
    return;
  }

  const { currentEl } = getDeckElements();
  if (!currentEl || currentEl.paused || !currentEl.duration || isNaN(currentEl.duration)) return;

  const cur = currentEl.currentTime;
  const dur = currentEl.duration;
  const pct = Math.min(100, (cur / dur) * 100);

  document.getElementById('timeElapsed').textContent = formatTime(cur);
  document.getElementById('timeTotal').textContent = formatTime(dur);
  document.getElementById('progressBarFill').style.width = `${pct.toFixed(1)}%`;

  const remaining = dur - cur;
  if (remaining <= 6.2 && remaining > 0.2 && !isCrossfading && dur > 12) {
    triggerSixSecondCrossfade(null, true);
  }
}, 200);

deckAEl.addEventListener('ended', () => {
  if (!window.karaokeModeActive && activeDeck === 'A' && !isCrossfading) triggerSixSecondCrossfade(null, true);
});
deckBEl.addEventListener('ended', () => {
  if (!window.karaokeModeActive && activeDeck === 'B' && !isCrossfading) triggerSixSecondCrossfade(null, true);
});
