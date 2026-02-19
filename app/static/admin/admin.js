// ===== State =====
let adminPassword = '';

// ===== Init =====
document.addEventListener('DOMContentLoaded', checkStoredPassword);

// ===== Auth =====
function checkStoredPassword() {
    const stored = localStorage.getItem('adminPassword');
    if (stored) {
        adminPassword = stored;
        verifyAndLoad();
    }
}

function doLogin() {
    const input = document.getElementById('passwordInput');
    const rememberMe = document.getElementById('rememberMe');
    const error = document.getElementById('loginError');
    const newPassword = input.value;

    if (!newPassword) {
        error.textContent = 'Enter a password';
        return;
    }

    adminPassword = newPassword;
    verifyAndLoad(true);
}

function verifyAndLoad(fromLogin = false) {
    const error = document.getElementById('loginError');
    const rememberMe = document.getElementById('rememberMe');

    // Test with a simple request
    fetch('/api/admin/users', {
        headers: { 'X-Admin-Password': adminPassword }
    })
        .then(resp => {
            if (resp.ok) {
                if (fromLogin && rememberMe && rememberMe.checked) {
                    localStorage.setItem('adminPassword', adminPassword);
                }

                document.getElementById('loginOverlay').style.display = 'none';
                document.getElementById('dashboard').style.display = 'block';
                loadDashboard();
            } else {
                if (fromLogin) {
                    error.textContent = 'Wrong password';
                    document.getElementById('passwordInput').value = '';
                } else {
                    // Stored password invalid ONLY if 401
                    if (resp.status === 401) {
                        localStorage.removeItem('adminPassword');
                        adminPassword = '';
                    } else {
                        console.error('Server error during auto-login:', resp.status);
                        // Optional: Show toast or indicator that server is unreachable
                    }
                }
            }
        })
        .catch(() => {
            if (fromLogin) {
                error.textContent = 'Connection error';
            }
        });
}

function logout() {
    localStorage.removeItem('adminPassword');
    adminPassword = '';
    window.location.reload();
}

// ===== API Helpers =====
function apiHeaders() {
    return { 'X-Admin-Password': adminPassword };
}

async function apiGet(url) {
    const resp = await fetch(url, { headers: apiHeaders() });
    if (!resp.ok) throw new Error(`API Error: ${resp.status}`);
    return resp.json();
}

async function apiPost(url) {
    const resp = await fetch(url, { method: 'POST', headers: apiHeaders() });
    if (!resp.ok) throw new Error(`API Error: ${resp.status}`);
    return resp.json();
}

async function apiDelete(url) {
    const resp = await fetch(url, { method: 'DELETE', headers: apiHeaders() });
    if (!resp.ok) throw new Error(`API Error: ${resp.status}`);
    return resp.json();
}

async function apiPatch(url) {
    const resp = await fetch(url, { method: 'PATCH', headers: apiHeaders() });
    if (!resp.ok) throw new Error(`API Error: ${resp.status}`);
    return resp.json();
}

// ===== Dashboard Loading =====
function loadDashboard() {
    loadUsers();
    loadRuns();
}

// ===== Users =====
async function loadUsers() {
    const grid = document.getElementById('usersGrid');
    grid.innerHTML = '<div class="loading">Loading users...</div>';

    try {
        const data = await apiGet('/api/admin/users');
        const users = data.users || [];

        // Update header count
        const countEl = document.querySelector('#userCount .stat-value');
        countEl.textContent = data.total;

        if (users.length === 0) {
            grid.innerHTML = '<div class="loading">No users registered yet. Share the login page link!</div>';
            return;
        }

        grid.innerHTML = users.map(user => {
            const initial = (user.username || '?')[0].toUpperCase();
            const badgeClass = user.is_active ? 'active' : 'inactive';
            const badgeText = user.is_active ? 'Active' : 'Inactive';
            const joinDate = user.created_at ? new Date(user.created_at).toLocaleDateString('en-US') : '-';

            let runInfo = 'Not run yet';
            if (user.latest_run) {
                const statusMap = {
                    'success': '✅ Success',
                    'failed': '❌ Failed',
                    'running': '⏳ Running...'
                };
                const runDate = user.latest_run.started_at ?
                    new Date(user.latest_run.started_at).toLocaleString('en-US') : '';
                runInfo = `${statusMap[user.latest_run.status] || user.latest_run.status}`;
                if (user.latest_run.items_count) {
                    runInfo += ` | ${user.latest_run.items_count} items`;
                }
                if (runDate) runInfo += ` | ${runDate}`;
            }

            return `
                <div class="user-card">
                    <div class="user-card-header">
                        <div class="user-info">
                            <div class="user-avatar">${initial}</div>
                            <div>
                                <div class="user-name">${user.username}</div>
                                <div class="user-email">${user.email || ''}</div>
                            </div>
                        </div>
                        <span class="user-badge ${badgeClass}">${badgeText}</span>
                    </div>
                    <div class="user-card-meta">
                        <span>📅 Joined: ${joinDate}</span>
                    </div>
                    <div class="user-run-status">📊 Last run: ${runInfo}</div>
                    <div class="user-card-actions">
                        <button class="action-btn trigger" onclick="triggerUser(${user.id})" title="Run recommendations one time">
                            🚀 Run Now
                        </button>
                        <button class="action-btn ${user.enable_recommendations ? 'success' : 'warning'}" 
                                onclick="toggleRecs(${user.id}, ${!user.enable_recommendations}, '${user.username}')"
                                title="${user.enable_recommendations ? 'Click to disable automatic recommendations' : 'Click to enable automatic recommendations'}">
                            ${user.enable_recommendations ? '✅ AI On' : '⛔ AI Off'}
                        </button>
                        <button class="action-btn danger" onclick="deactivateUser(${user.id}, '${user.username}')" title="Deactivate user completely">
                            🗑️ Deactivate
                        </button>
                    </div>
                </div>
            `;
        }).join('');

    } catch (err) {
        grid.innerHTML = `<div class="loading">❌ Error: ${err.message}</div>`;
    }
}

// ===== Runs =====
async function loadRuns() {
    const tbody = document.getElementById('runsBody');
    tbody.innerHTML = '<tr><td colspan="6" class="loading">Loading runs...</td></tr>';

    try {
        const data = await apiGet('/api/admin/runs?limit=20');
        const runs = data.runs || [];

        if (runs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="loading">No runs yet</td></tr>';
            return;
        }

        tbody.innerHTML = runs.map(run => {
            const statusClass = run.status;
            const statusIcon = { 'success': '✅', 'failed': '❌', 'running': '⏳' };
            const startTime = run.started_at ? new Date(run.started_at).toLocaleString('en-US') : '-';
            const endTime = run.completed_at ? new Date(run.completed_at).toLocaleString('en-US') : '-';

            return `
                <tr>
                    <td><strong>${run.username}</strong></td>
                    <td>
                        <span class="status-badge ${statusClass}">
                            ${statusIcon[run.status] || ''} ${run.status}
                        </span>
                    </td>
                    <td>${startTime}</td>
                    <td>${endTime}</td>
                    <td>${run.items_count || 0}</td>
                    <td style="color: rgba(255,69,58,0.8); font-size: 0.8rem; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"
                        title="${run.error || ''}">
                        ${run.error || '-'}
                    </td>
                </tr>
            `;
        }).join('');

    } catch (err) {
        tbody.innerHTML = `<tr><td colspan="6" class="loading">❌ Error: ${err.message}</td></tr>`;
    }
}

// ===== Actions =====
async function triggerUser(userId) {
    showToast('⏳ Running recommendations...');
    try {
        const data = await apiPost(`/api/admin/trigger/${userId}`);
        showToast('✅ ' + data.message);
        setTimeout(loadDashboard, 2000);
    } catch (err) {
        showToast('❌ Error: ' + err.message);
    }
}

async function triggerAll() {
    if (!confirm('Run recommendations for all users?')) return;
    showToast('⏳ Running recommendations for everyone...');
    try {
        const data = await apiPost('/api/admin/trigger-all');
        showToast('✅ ' + data.message);
        setTimeout(loadDashboard, 5000);
    } catch (err) {
        showToast('❌ Error: ' + err.message);
    }
}

async function deactivateUser(userId, username) {
    if (!confirm(`Deactivate ${username}?`)) return;
    try {
        const data = await apiDelete(`/api/admin/users/${userId}`);
        showToast('✅ ' + data.message);
        loadUsers();
    } catch (err) {
        showToast('❌ Error: ' + err.message);
    }
}


async function toggleRecs(userId, enable, username) {
    if (!confirm(enable ? `Enable recommendations for ${username}?` : `Disable recommendations for ${username}?`)) return;

    showToast('⏳ Updating...');
    try {
        const data = await apiPatch(`/api/admin/users/${userId}/toggle-recommendations?enable=${enable}`);
        showToast('✅ ' + data.message);
        loadUsers();
    } catch (err) {
        showToast('❌ Error: ' + err.message);
    }
}

// ===== Toast =====
function showToast(message) {
    let toast = document.querySelector('.toast');
    if (!toast) {
        toast = document.createElement('div');
        toast.className = 'toast';
        document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.classList.add('show');
    setTimeout(() => toast.classList.remove('show'), 3000);
}
