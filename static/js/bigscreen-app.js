// =========================================================================
// 4. HUD RENDERING & WEBSOCKET TELEMETRY SYNC
// =========================================================================
function updateStageHeader() {
  if (!currentScene) return;
  document.getElementById('stageTitle').textContent = currentScene.stage_title || "STAGE 01: 8-BIT ARENA";
  const msgText = `*** ${currentScene.stage_title} *** ${currentScene.flavor_text} *** SCAN QR CODE TO VOTE & DROP SONGS! ***`;
  const copyA = document.getElementById('flavorTickerCopyA');
  const copyB = document.getElementById('flavorTickerCopyB');
  if (copyA && copyB) {
    copyA.textContent = msgText;
    copyB.textContent = msgText;
  }
  const isAi = String(currentScene.source || '').startsWith('gemini');
  const srcTag = isAi ? '[GEMINI AI]' : '[8-BIT]';
  document.getElementById('sceneSourceTag').textContent = srcTag;
}

function renderHUD(state) {
  appState = state;

  // Telemetry
  if (state.telemetry) {
    const djs = String(state.telemetry.active_djs || 0).padStart(2, '0');
    document.getElementById('telemetryDjs').textContent = djs;
  }

  // Public URL & QR (or Wi-Fi Setup Hotspot QR if Pi is in Captive Portal mode)
  const wifiSt = state.wifi_status || {};
  const qrHeader = document.getElementById('qrBoxHeaderTitle');
  const lbl = document.getElementById('publicUrlLabel');
  if (wifiSt.hotspot_active) {
    if (qrHeader) qrHeader.textContent = 'SETUP VENUE WI-FI!';
    const setupLabel = `WIFI: ${wifiSt.setup_ssid || '8BIT-JUKEBOX-SETUP'}`;
    if (lbl.textContent !== setupLabel) {
      lbl.textContent = setupLabel;
      const qrPayload = wifiSt.wifi_qr_payload || 'WIFI:S:8BIT-JUKEBOX-SETUP;T:nopass;;';
      document.getElementById('qrCodeImg').src = `/api/qrcode?url=${encodeURIComponent(qrPayload)}&t=${Date.now()}`;
    }
  } else if (state.public_url) {
    if (qrHeader) qrHeader.textContent = 'JOIN THE PARTY!';
    const cleanLabel = state.public_url.replace(/^https?:\/\//, '');
    if (lbl.textContent !== cleanLabel) {
      lbl.textContent = cleanLabel;
      document.getElementById('qrCodeImg').src = `/api/qrcode?url=${encodeURIComponent(state.public_url)}&t=${Date.now()}`;
    }
  }

  if (typeof state.system_volume === 'number') {
    setMasterVolumePct(state.system_volume);
  }

  // Now Playing
  const np = state.now_playing;
  if (np) {
    document.getElementById('nowPlayingTitle').textContent = np.title;
    document.getElementById('nowPlayingArtist').textContent = np.artist;
    document.getElementById('nowPlayingSeedBadge').textContent =
      np.is_seed ? "AUTO-DJ" : `DJ: ${np.requested_by_name}`;
    document.getElementById('bufferStatusBadge').textContent =
      np.audio_ready ? "[R128 READY]" : "[BUFFERING]";

    if (np.scene) {
      currentScene = np.scene;
      applyPalette(currentScene.palette);
      updateStageHeader();
    }

    const { currentEl } = getDeckElements();
    if (activeVideoId !== np.video_id && !isCrossfading) {
      loadAndPlayOnActiveDeck(np);
    } else if (
      !isCrossfading &&
      np.audio_ready &&
      (!activeTrackAudioReady || currentEl.error || (currentEl.paused && currentEl.readyState < 2))
    ) {
      // Track finished normalizing in the background while deck was waiting -> reload & start playback immediately!
      loadAndPlayOnActiveDeck(np, true);
    }
  }

  // Preload Next Up Track onto the inactive deck
  if (state.next_up_track) {
    preloadNextDeck(state.next_up_track);
  }

  // Pending Host Approvals Indicator on Main Screen
  const pendingCount = Number(state.pending_approvals_count || (state.pending_approvals ? state.pending_approvals.length : 0));
  const pendingBadge = document.getElementById('pendingApprovalBadge');
  const pendingBanner = document.getElementById('pendingApprovalBanner');
  const pendingBannerText = document.getElementById('pendingApprovalBannerText');
  if (pendingCount > 0) {
    if (pendingBadge) {
      pendingBadge.textContent = `${pendingCount} PENDING`;
      pendingBadge.classList.remove('hidden');
    }
    if (pendingBanner && pendingBannerText) {
      pendingBannerText.textContent = `! ${pendingCount} REQUEST${pendingCount === 1 ? '' : 'S'} AWAITING HOST APPROVAL !`;
      pendingBanner.classList.remove('hidden');
    }
  } else {
    if (pendingBadge) pendingBadge.classList.add('hidden');
    if (pendingBanner) pendingBanner.classList.add('hidden');
  }

  // Up Next Queue (Top 5)
  const q = state.queue || [];
  document.getElementById('queueCountBadge').textContent = `${q.length} IN QUEUE`;
  const upNextEl = document.getElementById('upNextList');
  if (q.length === 0) {
    upNextEl.innerHTML = `
      <div class="p-4 border-2 border-dashed border-gray-700 text-center">
        <div class="text-xs xl:text-sm text-amber-300 mb-2">USER QUEUE EMPTY // AUTO-DJ ACTIVE</div>
        <div class="text-[11px] xl:text-xs text-gray-300 leading-relaxed">
          NEXT SEED: ${state.next_up_track ? state.next_up_track.title + ' - ' + state.next_up_track.artist : 'ROTATING SEED POOL'}
        </div>
      </div>
    `;
  } else {
    upNextEl.innerHTML = q.slice(0, 7).map((item, idx) => `
      <div class="p-3 bg-black/75 border-2 ${idx === 0 ? 'border-emerald-400' : 'border-gray-700'} flex items-center justify-between gap-3">
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-2.5 mb-1.5">
            <span class="text-[11px] px-2 py-0.5 ${idx === 0 ? 'bg-emerald-400 text-black' : 'bg-gray-800 text-gray-200'} shrink-0">
              #${idx + 1}
            </span>
            <span class="text-xs xl:text-sm text-white truncate">${item.title}</span>
          </div>
          <div class="text-[10px] xl:text-xs text-gray-300 truncate">
            ${item.artist} // DJ: <span class="text-amber-300">${item.requested_by_name}</span>
            ${idx === 0 && item.audio_ready ? '<span class="text-emerald-400 ml-1">[READY]</span>' : ''}
          </div>
        </div>
        <div class="px-3 py-1.5 bg-pink-600/30 border-2 border-pink-500 text-center shrink-0">
          <div class="text-sm xl:text-base highlight-text">${item.votes}</div>
          <div class="text-[8px] xl:text-[9px] text-gray-200">VOTES</div>
        </div>
      </div>
    `).join('');
  }

  // Leaderboard
  const lb = state.leaderboard || [];
  const lbEl = document.getElementById('leaderboardList');
  if (lb.length === 0) {
    lbEl.innerHTML = `<div class="text-[11px] text-gray-400 py-2">NO DJ WINS RECORDED YET. REQUEST A TRACK!</div>`;
  } else {
    lbEl.innerHTML = lb.slice(0, 4).map((u, idx) => `
      <div class="flex items-center py-1.5 border-b border-gray-800 min-w-0">
        <div class="truncate w-full">
          <span class="text-amber-400 mr-1.5">${idx + 1}.</span>
          <span class="text-white">${u.username}</span>
        </div>
      </div>
    `).join('');
  }
}

function handleHostTransportControl(msg) {
  if (msg.control === 'reload_bigscreen') {
    location.reload();
    return;
  }
  if (msg.control === 'exit_fullscreen') {
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => {});
    }
    return;
  }
  if (msg.control === 'set_viz_mode' && msg.viz_mode) {
    setVizModeById(msg.viz_mode);
    return;
  }
  if (msg.control === 'set_volume' && typeof msg.volume_pct === 'number') {
    setMasterVolumePct(msg.volume_pct);
    return;
  }
  ensureAudioUnlocked();
  const { currentEl } = getDeckElements();
  if (!currentEl) return;

  const control = msg.control;
  const delta = Number(msg.seconds || 15);

  if (control === 'toggle_pause') {
    if (currentEl.paused) {
      currentEl.play().catch(() => {});
      document.getElementById('activeDeckBadge').textContent = `DECK ${activeDeck} [LIVE]`;
    } else {
      currentEl.pause();
      document.getElementById('activeDeckBadge').textContent = `DECK ${activeDeck} [PAUSED]`;
    }
  } else if (control === 'seek_ff') {
    if (currentEl.duration && !isNaN(currentEl.duration)) {
      currentEl.currentTime = Math.min( Math.max(0, currentEl.duration - 1), currentEl.currentTime + delta );
    }
  } else if (control === 'seek_rw') {
    currentEl.currentTime = Math.max(0, currentEl.currentTime - delta);
  } else if (control === 'restart_track') {
    currentEl.currentTime = 0;
    if (currentEl.paused) currentEl.play().catch(() => {});
  }
}

async function minimizeKioskFromBigscreen() {
  if (document.fullscreenElement) {
    document.exitFullscreen().catch(() => {});
  }
  try {
    await fetch('/api/host/action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pin: '', action: 'minimize_kiosk' })
    });
  } catch (err) {}
}

function toggleHostModal(forceState = null) {
  const modal = document.getElementById('hostOverlayModal');
  const iframe = document.getElementById('hostModalIframe');
  const isHidden = modal.classList.contains('hidden');
  const shouldOpen = forceState !== null ? forceState : isHidden;

  if (shouldOpen) {
    if (iframe.src === 'about:blank' || !iframe.src.includes('/host')) {
      iframe.src = '/host';
    }
    modal.classList.remove('hidden');
  } else {
    modal.classList.add('hidden');
  }
}

window.addEventListener('message', (e) => {
  if (e.data && e.data.type === 'set_viz_mode' && e.data.viz_mode) {
    setVizModeById(e.data.viz_mode);
  } else if (e.data && e.data.type === 'set_volume' && typeof e.data.volume_pct === 'number') {
    setMasterVolumePct(e.data.volume_pct);
  }
});

document.addEventListener('keydown', (e) => {
  ensureAudioUnlocked();
  if (e.target && (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA')) return;
  if (e.key === 'Escape') {
    toggleHostModal(false);
  } else if (e.key === 'h' || e.key === 'H') {
    toggleHostModal();
  } else if (e.key === 'v' || e.key === 'V') {
    cycleVizMode();
  }
});

// WebSocket auto-reconnect loop
let ws = null;
function connectBigScreenWS() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${proto}//${location.host}/ws?role=bigscreen&client_id=bigscreen_tv`);

  ws.onopen = () => {
    document.getElementById('wsDot').className = 'inline-block w-3 h-3 bg-emerald-400 shrink-0';
    document.getElementById('wsStatusText').textContent = 'ONLINE';
  };

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === 'state_sync') {
        renderHUD(msg);
      } else if (msg.type === 'host_veto_crossfade') {
        triggerSixSecondCrossfade(msg.now_playing, false);
      } else if (msg.type === 'host_transport_control') {
        handleHostTransportControl(msg);
      }
    } catch (e) {
      console.error("WS parse error:", e);
    }
  };

  ws.onclose = () => {
    document.getElementById('wsDot').className = 'inline-block w-3 h-3 bg-red-500';
    document.getElementById('wsStatusText').textContent = 'RECONNECTING...';
    setTimeout(connectBigScreenWS, 2000);
  };
}

connectBigScreenWS();
