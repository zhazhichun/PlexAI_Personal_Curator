// ===== State =====
let adminPassword = '';

// ===== Auth =====
function doLogin() {
    const input = document.getElementById('passwordInput');
    const error = document.getElementById('loginError');
    adminPassword = input.value;

    if (!adminPassword) {
        error.textContent = 'הכנס סיסמה';
        return;
    }

    // Test with a simple request
    fetch('/api/admin/users', {
        headers: { 'X-Admin-Password': adminPassword }
    })
        .then(resp => {
            if (resp.ok) {
                document.getElementById('loginOverlay').style.display = 'none';
                document.getElementById('dashboard').style.display = 'block';
                loadDashboard();
            } else {
                error.textContent = 'סיסמה שגויה';
                input.value = '';
            }
        })
        .catch(() => {
            error.textContent = 'שגיאת חיבור';
        });
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

// ===== Dashboard Loading =====
function loadDashboard() {
    loadUsers();
    loadRuns();
}

// ===== Users =====
async function loadUsers() {
    const grid = document.getElementById('usersGrid');
    grid.innerHTML = '<div class="loading">טוען משתמשים...</div>';

    try {
        const data = await apiGet('/api/admin/users');
        const users = data.users || [];

        // Update header count
        const countEl = document.querySelector('#userCount .stat-value');
        countEl.textContent = data.total;

        if (users.length === 0) {
            grid.innerHTML = '<div class="loading">אין משתמשים רשומים עדיין. שתף את הלינק לדף ההתחברות!</div>';
            return;
        }

        grid.innerHTML = users.map(user => {
            const initial = (user.username || '?')[0].toUpperCase();
            const badgeClass = user.is_active ? 'active' : 'inactive';
            const badgeText = user.is_active ? 'פעיל' : 'לא פעיל';
            const joinDate = user.created_at ? new Date(user.created_at).toLocaleDateString('he-IL') : '-';

            let runInfo = 'לא הורץ עדיין';
            if (user.latest_run) {
                const statusMap = {
                    'success': '✅ הצלחה',
                    'failed': '❌ נכשל',
                    'running': '⏳ רץ...'
                };
                const runDate = user.latest_run.started_at ?
                    new Date(user.latest_run.started_at).toLocaleString('he-IL') : '';
                runInfo = `${statusMap[user.latest_run.status] || user.latest_run.status}`;
                if (user.latest_run.items_count) {
                    runInfo += ` | ${user.latest_run.items_count} פריטים`;
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
                        <span>📅 הצטרף: ${joinDate}</span>
                    </div>
                    <div class="user-run-status">📊 ריצה אחרונה: ${runInfo}</div>
                    <div class="user-card-actions">
                        <button class="action-btn trigger" onclick="triggerUser(${user.id})">
                            🚀 הפעל המלצות
                        </button>
                        <button class="action-btn danger" onclick="deactivateUser(${user.id}, '${user.username}')">
                            🗑️ השבת
                        </button>
                    </div>
                </div>
            `;
        }).join('');

    } catch (err) {
        grid.innerHTML = `<div class="loading">❌ שגיאה: ${err.message}</div>`;
    }
}

// ===== Runs =====
async function loadRuns() {
    const tbody = document.getElementById('runsBody');
    tbody.innerHTML = '<tr><td colspan="6" class="loading">טוען ריצות...</td></tr>';

    try {
        const data = await apiGet('/api/admin/runs?limit=20');
        const runs = data.runs || [];

        if (runs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="6" class="loading">אין ריצות עדיין</td></tr>';
            return;
        }

        tbody.innerHTML = runs.map(run => {
            const statusClass = run.status;
            const statusIcon = { 'success': '✅', 'failed': '❌', 'running': '⏳' };
            const startTime = run.started_at ? new Date(run.started_at).toLocaleString('he-IL') : '-';
            const endTime = run.completed_at ? new Date(run.completed_at).toLocaleString('he-IL') : '-';

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
        tbody.innerHTML = `<tr><td colspan="6" class="loading">❌ שגיאה: ${err.message}</td></tr>`;
    }
}

// ===== Actions =====
async function triggerUser(userId) {
    showToast('⏳ מפעיל המלצות...');
    try {
        const data = await apiPost(`/api/admin/trigger/${userId}`);
        showToast('✅ ' + data.message);
        setTimeout(loadDashboard, 2000);
    } catch (err) {
        showToast('❌ שגיאה: ' + err.message);
    }
}

async function triggerAll() {
    if (!confirm('להפעיל המלצות לכל המשתמשים?')) return;
    showToast('⏳ מפעיל המלצות לכולם...');
    try {
        const data = await apiPost('/api/admin/trigger-all');
        showToast('✅ ' + data.message);
        setTimeout(loadDashboard, 5000);
    } catch (err) {
        showToast('❌ שגיאה: ' + err.message);
    }
}

async function deactivateUser(userId, username) {
    if (!confirm(`להשבית את ${username}?`)) return;
    try {
        const data = await apiDelete(`/api/admin/users/${userId}`);
        showToast('✅ ' + data.message);
        loadUsers();
    } catch (err) {
        showToast('❌ שגיאה: ' + err.message);
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
