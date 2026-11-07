// =========================================================================
// 1. PERSISTENT CLIENT UUID & USER PROFILE IN LOCALSTORAGE
// =========================================================================
function getOrCreateClientId() {
  let cid = localStorage.getItem('jukebox_client_id');
  if (!cid) {
    if (window.crypto && crypto.randomUUID) {
      cid = crypto.randomUUID();
    } else {
      cid = 'dj_' + Math.random().toString(36).substring(2, 11) + '_' + Date.now();
    }
    localStorage.setItem('jukebox_client_id', cid);
  }
  return cid;
}

const CLIENT_ID = getOrCreateClientId();
let currentTab = 'vote';
let latestState = null;
let votedQueueIds = new Set();
let ws = null;
let reconnectTimer = null;

function showToast(msg, isError = false) {
  const el = document.getElementById('toastBanner');
  el.textContent = msg;
  el.className = `fixed bottom-6 left-3 right-3 max-w-md mx-auto z-50 p-3 border-4 border-white text-[10px] text-center shadow-lg ${
    isError ? 'bg-red-600 text-white' : 'bg-amber-400 text-black'
  }`;
  el.classList.remove('hidden');
  setTimeout(() => el.classList.add('hidden'), 2800);
}

function switchTab(tab) {
  currentTab = tab;
  document.getElementById('panelVote').classList.toggle('hidden', tab !== 'vote');
  document.getElementById('panelFast').classList.toggle('hidden', tab !== 'fast');
  document.getElementById('panelSearch').classList.toggle('hidden', tab !== 'search');

  document.getElementById('tabBtnVote').className = `retro-btn py-2.5 text-[9px] text-center ${tab === 'vote' ? 'tab-active' : 'tab-inactive'}`;
  document.getElementById('tabBtnFast').className = `retro-btn py-2.5 text-[9px] text-center ${tab === 'fast' ? 'tab-active' : 'tab-inactive'}`;
  document.getElementById('tabBtnSearch').className = `retro-btn py-2.5 text-[9px] text-center ${tab === 'search' ? 'tab-active' : 'tab-inactive'}`;
}

// =========================================================================
// 2. RENDERING MOBILE UI
// =========================================================================
function renderMobileState(state) {
  latestState = state;

  if (state.user) {
    localStorage.setItem('jukebox_username', state.user.username);
    document.getElementById('djUsername').textContent = state.user.username;
    document.getElementById('djWins').textContent = `${state.user.wins || 0} WINS`;
    document.getElementById('djVotes').textContent = `${state.user.votes_cast || 0} VOTES`;
  }

  if (Array.isArray(state.voted_queue_ids)) {
    votedQueueIds = new Set(state.voted_queue_ids);
  }

  const np = state.now_playing;
  if (np) {
    document.getElementById('nowPlayingTitle').textContent = np.title;
    document.getElementById('nowPlayingArtist').textContent = np.artist;
    document.getElementById('nowPlayingDj').textContent = np.is_seed ? 'AUTO-DJ' : `DJ: ${np.requested_by_name}`;
    document.getElementById('stageTitleBanner').textContent =
      (np.scene && np.scene.stage_title) ? np.scene.stage_title : 'STAGE 01: 8-BIT ARENA';
  }

  // Render Active Queue
  const queue = state.queue || [];
  document.getElementById('tabQueueCount').textContent = queue.length;
  const qContainer = document.getElementById('mobileQueueList');

  if (queue.length === 0) {
    qContainer.innerHTML = `
      <div class="retro-card p-5 text-center">
        <div class="text-xs text-amber-300 mb-2">THE QUEUE IS EMPTY!</div>
        <p class="text-[9px] text-gray-400 leading-relaxed mb-4">
          AUTO-DJ IS SPINNING SEED TRACKS. TAP FAST TRACK OR OPEN SEARCH TO DROP THE NEXT SONG!
        </p>
        <button onclick="switchTab('fast')" class="retro-btn px-4 py-2.5 bg-[#00f5d4] text-black text-[9px]">
          BROWSE FAST TRACKS
        </button>
      </div>
    `;
  } else {
    qContainer.innerHTML = queue.map((item, idx) => {
      const hasVoted = votedQueueIds.has(item.id);
      return `
        <div class="retro-card p-3 flex items-center justify-between gap-3">
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2 mb-1">
              <span class="px-1.5 py-0.5 text-[8px] ${idx === 0 ? 'bg-emerald-400 text-black' : 'bg-gray-800 text-gray-300'}">
                #${idx + 1}
              </span>
              <span class="text-[10px] text-white truncate">${escapeHtml(item.title)}</span>
            </div>
            <div class="text-[8px] text-gray-400 truncate mb-1">${escapeHtml(item.artist)}</div>
            <div class="text-[7px] text-amber-300">REQ BY: ${escapeHtml(item.requested_by_name)}</div>
          </div>
          <button
            onclick="voteTrack(${item.id})"
            ${hasVoted ? 'disabled' : ''}
            class="retro-btn px-3 py-2.5 text-center shrink-0 ${
              hasVoted
                ? 'bg-gray-800 text-emerald-400 border-emerald-400 opacity-80'
                : 'bg-pink-600 text-white hover:bg-pink-500'
            }"
          >
            <div class="text-xs">${item.votes}</div>
            <div class="text-[7px] mt-0.5">${hasVoted ? 'VOTED' : '+1 VOTE'}</div>
          </button>
        </div>
      `;
    }).join('');
  }

  // Render Fast Track Seed Pool
  const seedPool = state.seed_pool || [];
  const activeIds = new Set(queue.map(q => q.video_id));
  if (np) activeIds.add(np.video_id);

  const ftContainer = document.getElementById('fastTrackList');
  ftContainer.innerHTML = seedPool.map(track => {
    const inQueue = activeIds.has(track.video_id);
    return `
      <div class="retro-card p-3 flex items-center justify-between gap-3">
        <div class="min-w-0 flex-1">
          <div class="text-[10px] text-white truncate mb-1">${escapeHtml(track.title)}</div>
          <div class="text-[8px] text-gray-400 truncate">${escapeHtml(track.artist)}</div>
        </div>
        <button
          onclick='requestSong(${JSON.stringify(track).replace(/'/g, "&#39;")}, "fast_track")'
          ${inQueue ? 'disabled' : ''}
          class="retro-btn px-3 py-2 text-[8px] shrink-0 ${
            inQueue ? 'bg-gray-800 text-gray-500 border-gray-600' : 'bg-[#00f5d4] text-black'
          }"
        >
          ${inQueue ? 'ACTIVE' : '+ QUEUE'}
        </button>
      </div>
    `;
  }).join('');
}

function escapeHtml(str) {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// =========================================================================
// 3. USER ACTIONS (VOTE, RE-ROLL, FAST TRACK, OPEN SEARCH)
// =========================================================================
async function rerollUsername() {
  try {
    const res = await fetch('/api/user', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ client_id: CLIENT_ID, reroll: true })
    });
    const data = await res.json();
    if (data.ok && data.user) {
      document.getElementById('djUsername').textContent = data.user.username;
      localStorage.setItem('jukebox_username', data.user.username);
      showToast(`NEW DJ TAG: ${data.user.username}`);
    }
  } catch (e) {
    showToast("FAILED TO RE-ROLL NAME", true);
  }
}

async function voteTrack(queueId) {
  try {
    const res = await fetch('/api/vote', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ client_id: CLIENT_ID, queue_id: queueId })
    });
    const data = await res.json();
    if (!res.ok) {
      showToast(data.detail || "ALREADY VOTED!", true);
      return;
    }
    votedQueueIds.add(queueId);
    showToast("+1UP VOTE REGISTERED!");
  } catch (e) {
    showToast("VOTE FAILED", true);
  }
}

async function requestSong(track, mode) {
  try {
    const res = await fetch('/api/request', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        client_id: CLIENT_ID,
        video_id: track.video_id,
        title: track.title,
        artist: track.artist,
        duration: track.duration || 210,
        thumbnail: track.thumbnail || '',
        mode: mode
      })
    });
    const data = await res.json();
    if (data.already_queued) {
      showToast("TRACK ALREADY IN QUEUE!", true);
      return;
    }
    if (data.status === 'queued') {
      showToast(`DROPPED INTO QUEUE: ${track.title}`);
    } else {
      showToast(`SENT TO HOST FOR APPROVAL: ${track.title}`);
    }
  } catch (e) {
    showToast("REQUEST FAILED", true);
  }
}

async function submitOpenSearch(e) {
  e.preventDefault();
  const q = document.getElementById('searchInput').value.trim();
  if (!q) return;
  const container = document.getElementById('searchResultsList');
  container.innerHTML = `<div class="text-center text-[9px] text-[#00f5d4] py-6">SEARCHING YOUTUBE MUSIC...</div>`;

  try {
    const res = await fetch(`/api/search?q=${encodeURIComponent(q)}`);
    const data = await res.json();
    const results = data.results || [];
    if (results.length === 0) {
      container.innerHTML = `<div class="text-center text-[9px] text-gray-400 py-6">NO TRACKS FOUND. TRY ANOTHER QUERY!</div>`;
      return;
    }
    container.innerHTML = results.map(track => `
      <div class="retro-card-pink p-3 flex items-center justify-between gap-3">
        <div class="min-w-0 flex-1">
          <div class="text-[10px] text-white truncate mb-1">${escapeHtml(track.title)}</div>
          <div class="text-[8px] text-gray-300 truncate">${escapeHtml(track.artist)}</div>
        </div>
        <button
          onclick='requestSong(${JSON.stringify(track).replace(/'/g, "&#39;")}, "open_search")'
          class="retro-btn px-3 py-2 bg-pink-600 text-white text-[8px] shrink-0"
        >
          ASK HOST
        </button>
      </div>
    `).join('');
  } catch (err) {
    container.innerHTML = `<div class="text-center text-[9px] text-red-400 py-6">SEARCH ERROR. PLEASE RETRY.</div>`;
  }
}

// =========================================================================
// 4. WEBSOCKET AUTO-RECONNECT & PHONE SLEEP/WAKE (`visibilitychange`) SYNC
// =========================================================================
async function fetchStateSnapshot() {
  try {
    const res = await fetch(`/api/state?client_id=${encodeURIComponent(CLIENT_ID)}`);
    if (res.ok) {
      const state = await res.json();
      renderMobileState(state);
    }
  } catch (e) {
    console.debug("State snapshot fetch error:", e);
  }
}

function connectMobileWS() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(`${proto}//${location.host}/ws?role=mobile&client_id=${encodeURIComponent(CLIENT_ID)}`);

  ws.onopen = () => {
    document.getElementById('connDot').className = 'w-2.5 h-2.5 bg-emerald-400 inline-block';
  };

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === 'state_sync') {
        renderMobileState(msg);
      } else if (msg.type === 'user_updated' && msg.user) {
        document.getElementById('djUsername').textContent = msg.user.username;
      } else if (msg.type === 'error') {
        showToast(msg.message, true);
      }
    } catch (err) {
      console.error("Mobile WS parse error:", err);
    }
  };

  ws.onclose = () => {
    document.getElementById('connDot').className = 'w-2.5 h-2.5 bg-red-500 inline-block';
    if (reconnectTimer) clearTimeout(reconnectTimer);
    reconnectTimer = setTimeout(connectMobileWS, 2000);
  };
}

// Phone sleep/wake cycle handler: immediately re-sync state & reconnect WS on wake
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') {
    fetchStateSnapshot();
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      connectMobileWS();
    } else {
      ws.send(JSON.stringify({ type: 'sync' }));
    }
  }
});

window.addEventListener('online', () => {
  fetchStateSnapshot();
  connectMobileWS();
});

// Initial boot
fetchStateSnapshot();
connectMobileWS();
