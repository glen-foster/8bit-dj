let networks = [];
let selectedSsid = '';

function showFeedback(msg, type) {
  const box = document.getElementById('feedbackBox');
  box.className = 'toast ' + (type || 'info');
  box.textContent = msg;
}

function togglePwVisibility() {
  const pw = document.getElementById('passwordInput');
  pw.type = pw.type === 'password' ? 'text' : 'password';
}

function selectNetwork(ssid, security) {
  selectedSsid = ssid;
  document.getElementById('ssidInput').value = ssid;
  renderNetworkList();
  if (security && security !== 'OPEN' && security !== '--') {
    document.getElementById('passwordInput').focus();
  }
}

function renderNetworkList() {
  const container = document.getElementById('networkList');
  if (!networks || networks.length === 0) {
    container.innerHTML = '<div style="text-align:center; padding: 16px; color: #9ca3af; font-size: 12px;">NO NETWORKS FOUND YET. TAP RESCAN OR TYPE SSID BELOW.</div>';
    return;
  }

  container.innerHTML = networks.map(net => {
    const isSel = net.ssid === selectedSsid;
    const lockTag = (net.security && net.security !== 'OPEN' && net.security !== '--') ? 'LOCKED' : 'OPEN';
    const activeTag = net.in_use ? ' [CONNECTED]' : '';
    return `
      <div class="net-item ${isSel ? 'selected' : ''}" onclick="selectNetwork(${JSON.stringify(net.ssid).replace(/"/g, '&quot;')}, '${net.security || ''}')">
        <div>
          <div class="net-name">${escapeHtml(net.ssid)}${activeTag}</div>
          <div class="net-meta">${escapeHtml(net.security || 'OPEN')} // ${lockTag}</div>
        </div>
        <div class="signal-pill">${net.signal || 0}%</div>
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

async function loadNetworks(rescan = false) {
  const btn = document.getElementById('rescanBtn');
  btn.textContent = 'SCANNING...';
  btn.disabled = true;
  try {
    const res = await fetch('/api/wifi/networks?rescan=' + (rescan ? '1' : '0'));
    const data = await res.json();
    networks = data.networks || [];
    renderNetworkList();

    if (data.status) {
      const dot = document.getElementById('statusDot');
      const txt = document.getElementById('currentWifiText');
      if (data.status.hotspot_active) {
        dot.className = 'status-dot';
        txt.textContent = 'SETUP HOTSPOT MODE (' + data.status.setup_ssid + ')';
      } else if (data.status.connected_ssid) {
        dot.className = 'status-dot online';
        txt.textContent = 'CONNECTED TO: ' + data.status.connected_ssid;
      } else {
        dot.className = 'status-dot';
        txt.textContent = 'DISCONNECTED';
      }
    }
  } catch (err) {
    showFeedback('COULD NOT REACH JUKEBOX WI-FI API.', 'error');
  } finally {
    btn.textContent = 'RESCAN';
    btn.disabled = false;
  }
}

async function submitWifiConnect(e) {
  e.preventDefault();
  const ssid = document.getElementById('ssidInput').value.trim();
  const password = document.getElementById('passwordInput').value;
  const pin = document.getElementById('pinInput').value.trim() || '1234';
  if (!ssid) return;

  const btn = document.getElementById('connectBtn');
  btn.textContent = 'CONNECTING PI TO ' + ssid + '...';
  btn.disabled = true;

  showFeedback(
    "CONNECTING RASPBERRY PI TO '" + ssid + "'! " +
    "NOTE: THE SETUP HOTSPOT WILL TURN OFF NOW. LOOK AT THE TV BIG SCREEN IN ~10 SECONDS TO SCAN THE NEW PARTY QR CODE!",
    'info'
  );

  try {
    const res = await fetch('/api/wifi/connect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ssid, password, pin })
    });
    const data = await res.json();
    if (res.ok && data.ok) {
      showFeedback(
        "SUCCESS! CONNECTED TO '" + ssid + "'! CHECK THE TV BIG SCREEN FOR THE LIVE PARTY QR CODE!",
        'success'
      );
      loadNetworks(false);
    } else {
      showFeedback(data.message || data.detail || 'FAILED TO CONNECT. CHECK PASSWORD & TRY AGAIN.', 'error');
    }
  } catch (err) {
    // When switching off the setup hotspot, the phone's connection to 10.42.0.1 drops right as the Pi joins the new Wi-Fi!
    showFeedback(
      "HOTSPOT HANDOFF COMPLETE! THE PI IS SWITCHING TO '" + ssid + "'. " +
      "WATCH THE TV BIG SCREEN FOR THE NEW QR CODE (IF PASSWORD WAS WRONG, '8BIT-JUKEBOX-SETUP' WILL REAPPEAR IN 20 SECONDS).",
      'success'
    );
  } finally {
    btn.textContent = '[ CONNECT JUKEBOX TO WI-FI ]';
    btn.disabled = false;
  }
}

loadNetworks(false);
