const isLocalDevice = ['localhost', '127.0.0.1', '::1'].includes(location.hostname);
let hostPin = sessionStorage.getItem('jukebox_host_pin') || '';
let hostWs = null;
let lastHostState = null;
let hostSortMode = 'votes';
let isDraggingVolume = false;
let volumeThrottleTimer = null;

function changeHostQueueSort(mode) {
  hostSortMode = mode;
  if (lastHostState) renderHostState(lastHostState);
}

function showHostToast(msg) {
  const el = document.getElementById('hostToast');
  el.textContent = msg;
  el.classList.remove('hidden');
  setTimeout(() => el.classList.add('hidden'), 2600);
}

function escapeHtml(str) {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

async function verifyHostPin(e) {
  if (e) e.preventDefault();
  const inputEl = document.getElementById('pinInput');
  const inputPin = (inputEl ? inputEl.value.trim() : '') || hostPin;
  if (!isLocalDevice && !inputPin) return;

  try {
    const res = await fetch('/api/host/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pin: inputPin })
    });
    if (!res.ok) {
      document.getElementById('pinErrorMsg').classList.remove('hidden');
      document.getElementById('pinGateModal').classList.remove('hidden');
      sessionStorage.removeItem('jukebox_host_pin');
      return;
    }
    const data = await res.json();
    if (inputPin) {
      hostPin = inputPin;
      sessionStorage.setItem('jukebox_host_pin', hostPin);
    }
    document.getElementById('pinErrorMsg').classList.add('hidden');
    document.getElementById('pinGateModal').classList.add('hidden');
    document.getElementById('hostDashboard').classList.remove('hidden');
    if (isLocalDevice) {
      const lockBtn = document.getElementById('hostLockBtn');
      if (lockBtn) lockBtn.classList.add('hidden');
    }
    renderHostState(data.state);
    connectHostWS();
  } catch (err) {
    if (!isLocalDevice) {
      document.getElementById('pinErrorMsg').classList.remove('hidden');
      document.getElementById('pinGateModal').classList.remove('hidden');
    }
  }
}

function logoutHost() {
  if (isLocalDevice) return;
  sessionStorage.removeItem('jukebox_host_pin');
  hostPin = '';
  if (hostWs) hostWs.close();
  document.getElementById('pinInput').value = '';
  document.getElementById('hostDashboard').classList.add('hidden');
  document.getElementById('pinGateModal').classList.remove('hidden');
}

function renderHostState(state) {
  if (!state) return;
  lastHostState = state;

  // Sync System Volume Slider unless actively dragging
  if (typeof state.system_volume === 'number' && !isDraggingVolume) {
    const slider = document.getElementById('hostVolSlider');
    const label = document.getElementById('hostVolLabel');
    if (slider) slider.value = state.system_volume;
    if (label) label.textContent = `${state.system_volume}%`;
  }

  const np = state.now_playing;
  if (np) {
    document.getElementById('hostNowTitle').textContent = np.title;
    document.getElementById('hostNowArtist').textContent =
      `${np.artist} // REQ BY: ${np.requested_by_name}`;
    document.getElementById('hostNowStage').textContent =
      (np.scene && np.scene.stage_title) ? np.scene.stage_title : 'STAGE 01';
  }

  // Sync Party Mode & Karaoke Mode Buttons & Hint
  const partyMode = state.party_mode || 'free_for_all';
  const karaokeMode = !!state.karaoke_mode;
  const btnFree = document.getElementById('partyModeBtnFree');
  const btnApproval = document.getElementById('partyModeBtnApproval');
  const btnKaraoke = document.getElementById('karaokeModeBtn');
  const modeHint = document.getElementById('hostPartyModeHint');
  if (btnKaraoke) {
    if (karaokeMode) {
      btnKaraoke.textContent = 'KARAOKE VIDEO MODE: ON';
      btnKaraoke.className = 'arcade-btn px-2.5 py-1 bg-pink-600 text-white text-[7px]';
    } else {
      btnKaraoke.textContent = 'KARAOKE VIDEO MODE: OFF';
      btnKaraoke.className = 'arcade-btn px-2.5 py-1 bg-[#1b113a] text-pink-400 text-[7px]';
    }
  }
  if (btnFree && btnApproval) {
    if (partyMode === 'host_approval') {
      btnApproval.className = 'arcade-btn px-2.5 py-1 bg-amber-400 text-black text-[7px]';
      btnFree.className = 'arcade-btn px-2.5 py-1 bg-[#1b113a] text-[#00f5d4] text-[7px]';
      if (modeHint) {
        modeHint.textContent = karaokeMode
          ? 'KARAOKE + PUB MODE: YOUTUBE LYRIC VIDEOS STREAM DIRECTLY (MODERATION ON)'
          : 'PUB / MODERATED MODE: OPEN SEARCH REQUESTS REQUIRE HOST APPROVAL';
        modeHint.className = 'text-[7px] text-amber-300';
      }
    } else {
      btnFree.className = 'arcade-btn px-2.5 py-1 bg-emerald-400 text-black text-[7px]';
      btnApproval.className = 'arcade-btn px-2.5 py-1 bg-[#1b113a] text-amber-300 text-[7px]';
      if (modeHint) {
        modeHint.textContent = karaokeMode
          ? 'KARAOKE MODE ACTIVE: YOUTUBE LYRIC VIDEOS STREAM DIRECTLY (ZERO DOWNLOAD)'
          : 'GUEST SEARCHES DROP STRAIGHT INTO LIVE QUEUE (UP/DOWN VOTING ACTIVE)';
        modeHint.className = karaokeMode ? 'text-[7px] text-pink-400' : 'text-[7px] text-emerald-300';
      }
    }
  }

  // Pending Open Search Approvals
  const pending = state.pending_approvals || [];
  document.getElementById('pendingCountBadge').textContent = pending.length;
  const pList = document.getElementById('pendingApprovalList');
  if (pending.length === 0) {
    if (partyMode === 'free_for_all') {
      pList.innerHTML = `
        <div class="p-3 bg-emerald-950/30 border border-emerald-500/40 text-center space-y-1.5">
          <div class="text-[8px] text-emerald-300">FREE-FOR-ALL MODE ACTIVE</div>
          <div class="text-[7px] text-gray-400 leading-relaxed">
            ALL GUEST OPEN SEARCHES BYPASS APPROVAL &amp; GO STRAIGHT TO THE LIVE QUEUE. SWITCH TO "HOST APPROVAL" ABOVE FOR PUBLIC / PUB EVENTS.
          </div>
        </div>
      `;
    } else {
      pList.innerHTML = `<div class="text-[8px] text-gray-500 py-4 text-center">NO PENDING OPEN SEARCH REQUESTS</div>`;
    }
  } else {
    pList.innerHTML = pending.map(item => `
      <div class="p-2 bg-black/80 border-2 border-amber-400 flex items-center justify-between gap-2">
        <div class="min-w-0 flex-1">
          <div class="text-[9px] text-white truncate mb-0.5">${escapeHtml(item.title)}</div>
          <div class="text-[7px] text-gray-400 truncate">${escapeHtml(item.artist)} // <span class="text-amber-300">${escapeHtml(item.requested_by_name)}</span></div>
        </div>
        <div class="flex items-center gap-1.5 shrink-0">
          <button
            onclick="hostQueueAction('approve', ${item.id})"
            class="arcade-btn px-2 py-1 bg-emerald-400 text-black text-[7px]"
          >
            APPROVE
          </button>
          <button
            onclick="hostQueueAction('reject', ${item.id})"
            class="arcade-btn px-2 py-1 bg-red-600 text-white text-[7px]"
          >
            REJECT
          </button>
        </div>
      </div>
    `).join('');
  }

  // Active Queue
  const rawQueue = state.queue || [];
  document.getElementById('activeQueueCount').textContent = `(${rawQueue.length})`;
  const qList = document.getElementById('hostActiveQueueList');
  if (rawQueue.length === 0) {
    qList.innerHTML = `<div class="text-[8px] text-gray-500 py-4 text-center">ACTIVE QUEUE EMPTY (SEED AUTO-DJ)</div>`;
  } else {
    const sortedQueue = rawQueue.map((item, idx) => ({ ...item, playOrderRank: idx + 1 }));
    if (hostSortMode === 'title') {
      sortedQueue.sort((a, b) => (a.title || '').localeCompare(b.title || ''));
    } else if (hostSortMode === 'artist') {
      sortedQueue.sort((a, b) => (a.artist || '').localeCompare(b.artist || ''));
    } else if (hostSortMode === 'dj') {
      sortedQueue.sort((a, b) => (a.requested_by_name || '').localeCompare(b.requested_by_name || ''));
    } else if (hostSortMode === 'recent') {
      sortedQueue.sort((a, b) => (b.id || 0) - (a.id || 0));
    }

    qList.innerHTML = sortedQueue.map((item) => `
      <div class="p-2 bg-black/70 border-2 ${item.playOrderRank === 1 ? 'border-emerald-400' : 'border-gray-700'} flex items-center justify-between gap-2">
        <div class="min-w-0 flex-1">
          <div class="text-[9px] text-white truncate mb-0.5">
            <span class="text-amber-300">#${item.playOrderRank}</span> ${escapeHtml(item.title)} <span class="text-[#00f5d4]">(${item.votes}V)</span>
          </div>
          <div class="text-[7px] text-gray-400 truncate">
            ${escapeHtml(item.artist)} // DJ: ${escapeHtml(item.requested_by_name)}
          </div>
        </div>
        <div class="flex items-center gap-1 shrink-0">
          ${item.playOrderRank > 1 ? `
            <button
              onclick="hostQueueAction('promote_top', ${item.id})"
              title="Promote to #1 Up Next"
              class="arcade-btn px-1.5 py-1 bg-gray-800 hover:bg-emerald-500 hover:text-black text-[#00f5d4] text-[7px]"
            >
              ^TOP
            </button>
          ` : ''}
          <button
            onclick="hostQueueAction('reject', ${item.id})"
            title="Remove Track"
            class="arcade-btn px-2 py-1 bg-gray-800 hover:bg-red-600 text-red-400 hover:text-white text-[8px] shrink-0"
          >
            X
          </button>
        </div>
      </div>
    `).join('');
  }

  if (state.seed_pool) {
    document.getElementById('seedPoolSummary').textContent =
      `SEED POOL: ${state.seed_pool.length} TRACKS`;
  }

  if (state.wifi_status) {
    const ws = state.wifi_status;
    const wEl = document.getElementById('hostWifiStatusText');
    if (wEl) {
      if (ws.hotspot_active) {
        wEl.textContent = `SETUP HOTSPOT ACTIVE: JOIN '${ws.setup_ssid}' ON PHONE`;
      } else if (ws.connected_ssid) {
        wEl.textContent = `CONNECTED TO WI-FI: ${ws.connected_ssid} (AUTO-FALLBACK TO '${ws.setup_ssid}' ENABLED)`;
      } else {
        wEl.textContent = `AUTO-FALLBACK HOTSPOT: '${ws.setup_ssid}' READY`;
      }
    }
  }
}

async function toggleSetupHotspot(enable) {
  try {
    const res = await fetch('/api/wifi/hotspot', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pin: hostPin, enable: !!enable })
    });
    const data = await res.json();
    showHostToast(data.message || (enable ? "HOTSPOT STARTED!" : "HOTSPOT STOPPED!"));
  } catch (e) {
    showHostToast("HOTSPOT SWITCHING IN PROGRESS...");
  }
}

async function triggerVetoSkip() {
  try {
    const res = await fetch('/api/host/action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pin: hostPin, action: 'veto_skip' })
    });
    const data = await res.json();
    if (res.ok) {
      showHostToast(data.message || "6S CROSSFADE SKIP TRIGGERED!");
    }
  } catch (e) {
    showHostToast("VETO COMMAND FAILED");
  }
}

async function sendTransportControl(action, seconds = 15, silentToast = false) {
  try {
    const res = await fetch('/api/host/action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pin: hostPin, action: action, seconds: seconds })
    });
    const data = await res.json();
    if (res.ok && !silentToast) {
      showHostToast(data.message || "TRANSPORT COMMAND SENT!");
    }
  } catch (e) {
    if (!silentToast) showHostToast("TRANSPORT COMMAND FAILED");
  }
}

async function setHostVizMode(modeId, label) {
  document.querySelectorAll('#hostVizButtons button').forEach(btn => {
    if (btn.getAttribute('data-viz') === modeId) {
      btn.className = 'arcade-btn px-2 py-1 bg-cyan-500 text-black text-[7px]';
    } else {
      btn.className = 'arcade-btn px-2 py-1 bg-[#1b113a] text-[#00f5d4] text-[7px]';
    }
  });

  try {
    if (window.parent && window.parent !== window) {
      window.parent.postMessage({ type: 'set_viz_mode', viz_mode: modeId }, '*');
    }
  } catch (err) {}

  await sendTransportControl(`set_viz_mode:${modeId}`, 0);
  showHostToast(`VISUALIZER SET TO: ${label}`);
}

async function setHostPartyMode(mode) {
  const target = mode === 'host_approval' ? 'host_approval' : 'free_for_all';
  await sendTransportControl(`set_party_mode:${target}`, 0);
}

async function toggleHostKaraokeMode() {
  await sendTransportControl('toggle_karaoke_mode', 0);
}

function onVolumeSliderInput(val) {
  isDraggingVolume = true;
  const pct = Math.max(0, Math.min(100, Number(val) || 0));
  document.getElementById('hostVolLabel').textContent = `${pct}%`;

  try {
    if (window.parent && window.parent !== window) {
      window.parent.postMessage({ type: 'set_volume', volume_pct: pct }, '*');
    }
  } catch (err) {}

  if (!volumeThrottleTimer) {
    volumeThrottleTimer = setTimeout(() => {
      volumeThrottleTimer = null;
      const currentVal = Math.max(0, Math.min(100, Number(document.getElementById('hostVolSlider').value) || 0));
      sendTransportControl(`set_volume:${currentVal}`, 0, true);
    }, 120);
  }
}

async function onVolumeSliderChange(val) {
  isDraggingVolume = false;
  const pct = Math.max(0, Math.min(100, Number(val) || 0));
  document.getElementById('hostVolLabel').textContent = `${pct}%`;

  try {
    if (window.parent && window.parent !== window) {
      window.parent.postMessage({ type: 'set_volume', volume_pct: pct }, '*');
    }
  } catch (err) {}

  await sendTransportControl(`set_volume:${pct}`, 0, true);
  showHostToast(`SYSTEM VOLUME: ${pct}%`);
}

let activePlaylistProvider = 'ytmusic';

function setPlaylistProvider(provider) {
  activePlaylistProvider = provider === 'spotify' ? 'spotify' : 'ytmusic';
  const ytBtn = document.getElementById('providerBtnYtmusic');
  const spBtn = document.getElementById('providerBtnSpotify');
  const searchInput = document.getElementById('playlistSearchInput');
  const idInput = document.getElementById('playlistIdInput');

  if (activePlaylistProvider === 'spotify') {
    spBtn.className = 'arcade-btn px-2.5 py-1 bg-[#1db954] text-black text-[7px]';
    ytBtn.className = 'arcade-btn px-2.5 py-1 bg-[#1b113a] text-[#00f5d4] text-[7px]';
    searchInput.placeholder = 'SEARCH SPOTIFY PLAYLISTS (E.G. 80S, ROCK, DISCO, HIP HOP) OR PASTE SPOTIFY URL...';
    idInput.placeholder = 'OR PASTE PUBLIC SPOTIFY PLAYLIST URL (HTTPS://OPEN.SPOTIFY.COM/PLAYLIST/...)';
    // Automatically show curated Spotify playlists so the host can pick one with 1 click
    searchHostPlaylists(null, true);
  } else {
    ytBtn.className = 'arcade-btn px-2.5 py-1 bg-cyan-500 text-black text-[7px]';
    spBtn.className = 'arcade-btn px-2.5 py-1 bg-[#1b113a] text-[#1db954] text-[7px]';
    searchInput.placeholder = 'SEARCH PLAYLISTS (E.G. 80S HITS, INDIE PARTY, 90S HIP HOP)...';
    idInput.placeholder = 'OR PASTE YOUTUBE MUSIC OR SPOTIFY PLAYLIST URL / ID...';
  }
}

async function searchHostPlaylists(e, allowEmptySpotify = false) {
  if (e) e.preventDefault();
  const q = document.getElementById('playlistSearchInput').value.trim();
  if (!q && !(allowEmptySpotify && activePlaylistProvider === 'spotify')) return;
  const btn = document.getElementById('searchPlaylistBtn');
  const container = document.getElementById('playlistSearchResults');
  btn.textContent = 'SEARCHING...';
  btn.disabled = true;
  container.classList.remove('hidden');
  const label = activePlaylistProvider === 'spotify' ? 'SPOTIFY' : 'YOUTUBE MUSIC';
  container.innerHTML = `<div class="col-span-full text-[8px] text-amber-300 py-2 text-center">LOADING ${label} PLAYLISTS...</div>`;

  try {
    const res = await fetch(`/api/host/search_playlists?q=${encodeURIComponent(q)}&provider=${encodeURIComponent(activePlaylistProvider)}&pin=${encodeURIComponent(hostPin)}`);
    const data = await res.json();
    const items = data.results || [];
    if (items.length === 0) {
      container.innerHTML = `<div class="col-span-full text-[8px] text-gray-400 py-2 text-center">NO PLAYLISTS FOUND FOR "${escapeHtml(q)}".</div>`;
      return;
    }
    container.innerHTML = items.map(pl => `
      <div class="p-2 bg-black/85 border-2 ${pl.provider === 'spotify' ? 'border-[#1db954]/60 hover:border-[#1db954]' : 'border-gray-700 hover:border-amber-400'} flex items-center justify-between gap-2">
        <div class="flex items-center gap-2 min-w-0 flex-1">
          ${pl.thumbnail ? `<img src="${escapeHtml(pl.thumbnail)}" class="w-8 h-8 object-cover border border-white shrink-0" />` : `<div class="w-8 h-8 bg-[#1b113a] border border-white flex items-center justify-center text-[7px] ${pl.provider === 'spotify' ? 'text-[#1db954]' : 'text-[#00f5d4]'} shrink-0">${pl.provider === 'spotify' ? 'SP' : 'YT'}</div>`}
          <div class="min-w-0 flex-1">
            <div class="text-[8px] text-white truncate mb-0.5">${escapeHtml(pl.title)}</div>
            <div class="text-[7px] text-gray-400 truncate">
              ${escapeHtml(pl.author)}${pl.item_count ? ' // ' + escapeHtml(pl.item_count) + ' TRACKS' : ''}
            </div>
          </div>
        </div>
        <button
          type="button"
          onclick="loadPlaylistById('${escapeHtml(pl.playlist_id)}', '${escapeHtml(pl.title).replace(/'/g, "\\'")}')"
          class="arcade-btn px-2.5 py-1.5 ${pl.provider === 'spotify' ? 'bg-[#1db954]' : 'bg-[#00f5d4]'} text-black text-[7px] shrink-0"
        >
          LOAD
        </button>
      </div>
    `).join('');
  } catch (err) {
    container.innerHTML = `<div class="col-span-full text-[8px] text-red-400 py-2 text-center">PLAYLIST SEARCH FAILED.</div>`;
  } finally {
    btn.textContent = 'SEARCH PLAYLISTS';
    btn.disabled = false;
  }
}

async function loadPlaylistById(playlistId, titleLabel = '') {
  document.getElementById('playlistIdInput').value = playlistId;
  await loadSeedPlaylist(null, playlistId, titleLabel);
}

async function hostQueueAction(action, queueId) {
  try {
    const res = await fetch('/api/host/action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pin: hostPin, action: action, queue_id: queueId })
    });
    const data = await res.json();
    if (res.ok) {
      if (action === 'approve') showHostToast("TRACK APPROVED -> LIVE QUEUE!");
      else if (action === 'promote_top') showHostToast(data.message || "PROMOTED TO #1!");
      else showHostToast("TRACK REMOVED!");
    }
  } catch (e) {
    showHostToast("HOST ACTION FAILED");
  }
}

async function loadSeedPlaylist(e, explicitId = null, titleLabel = '') {
  if (e) e.preventDefault();
  const playlistId = (explicitId || document.getElementById('playlistIdInput').value).trim();
  if (!playlistId) return;
  const btn = document.getElementById('loadPlaylistBtn');
  btn.textContent = "LOADING...";
  btn.disabled = true;
  showHostToast(titleLabel ? `LOADING "${titleLabel.toUpperCase()}"...` : "LOADING SEED PLAYLIST...");

  try {
    const res = await fetch('/api/host/action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        pin: hostPin,
        action: 'load_playlist',
        playlist_id: playlistId
      })
    });
    const data = await res.json();
    if (res.ok) {
      showHostToast(data.message || "SEED PLAYLIST UPDATED!");
      document.getElementById('playlistIdInput').value = '';
    } else {
      showHostToast(data.detail || "FAILED TO LOAD PLAYLIST");
    }
  } catch (err) {
    showHostToast("ERROR LOADING PLAYLIST");
  } finally {
    btn.textContent = "LOAD BY ID/URL";
    btn.disabled = false;
  }
}

function connectHostWS() {
  if (!isLocalDevice && !hostPin) return;
  if (hostWs) hostWs.close();

  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const pinParam = hostPin ? `&pin=${encodeURIComponent(hostPin)}` : '';
  hostWs = new WebSocket(`${proto}//${location.host}/ws?role=host&client_id=host_admin${pinParam}`);

  hostWs.onopen = () => {
    document.getElementById('hostWsStatus').textContent = 'WS: CONNECTED';
  };

  hostWs.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === 'state_sync') {
        renderHostState(msg);
      }
    } catch (err) {
      console.error(err);
    }
  };

  hostWs.onclose = () => {
    document.getElementById('hostWsStatus').textContent = 'WS: RECONNECTING...';
    if (isLocalDevice || hostPin) setTimeout(connectHostWS, 2000);
  };
}

// Unlock automatically on local device, or if PIN already saved in sessionStorage; otherwise show PIN modal
if (isLocalDevice || hostPin) {
  if (hostPin) document.getElementById('pinInput').value = hostPin;
  verifyHostPin(null);
} else {
  document.getElementById('pinGateModal').classList.remove('hidden');
}
