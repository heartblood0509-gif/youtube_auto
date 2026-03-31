/* 로그인 페이지 로직 */

// ── 탭 전환 ──
document.querySelectorAll('.auth-tab').forEach(tab => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('.auth-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        hideAllPanels();
        clearMessages();
        const panelId = `tab-${tab.dataset.tab}`;
        document.getElementById(panelId).classList.remove('hidden');
    });
});

function hideAllPanels() {
    document.querySelectorAll('.auth-panel').forEach(p => p.classList.add('hidden'));
}

function clearMessages() {
    document.getElementById('auth-error').classList.add('hidden');
    document.getElementById('auth-success').classList.add('hidden');
}

function showError(msg) {
    clearMessages();
    const el = document.getElementById('auth-error');
    el.textContent = msg;
    el.classList.remove('hidden');
}

function showSuccess(msg) {
    clearMessages();
    const el = document.getElementById('auth-success');
    el.textContent = msg;
    el.classList.remove('hidden');
}

// ── 소셜 로그인 ──
function loginGoogle() {
    window.location.href = '/api/auth/google/login';
}

// ── 이메일 로그인 ──
async function handleLogin(e) {
    e.preventDefault();
    clearMessages();
    const btn = document.getElementById('btn-login');
    btn.disabled = true;

    try {
        const resp = await fetch('/api/auth/login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                email: document.getElementById('login-email').value,
                password: document.getElementById('login-password').value,
            }),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || '로그인에 실패했습니다');

        window.location.href = '/';
    } catch (err) {
        showError(err.message);
    } finally {
        btn.disabled = false;
    }
}

// ── 회원가입 ──
async function handleRegister(e) {
    e.preventDefault();
    clearMessages();

    const pw = document.getElementById('reg-password').value;
    const pwConfirm = document.getElementById('reg-password-confirm').value;
    if (pw !== pwConfirm) {
        showError('비밀번호가 일치하지 않습니다');
        return;
    }

    const btn = document.getElementById('btn-register');
    btn.disabled = true;

    try {
        const payload = {
            email: document.getElementById('reg-email').value,
            nickname: document.getElementById('reg-nickname').value,
            password: pw,
        };
        const inviteInput = document.getElementById('reg-invite-code');
        if (inviteInput && inviteInput.value) {
            payload.invite_code = inviteInput.value;
        }
        const resp = await fetch('/api/auth/register', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || '회원가입에 실패했습니다');

        window.location.href = '/';
    } catch (err) {
        showError(err.message);
    } finally {
        btn.disabled = false;
    }
}

// ── 아이디 찾기 ──
function showFindEmail() {
    hideAllPanels();
    clearMessages();
    document.getElementById('modal-find-email').classList.remove('hidden');
}

async function handleFindEmail(e) {
    e.preventDefault();
    clearMessages();
    const resultEl = document.getElementById('find-email-result');
    resultEl.classList.add('hidden');

    try {
        const resp = await fetch('/api/auth/find-email', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                nickname: document.getElementById('find-nickname').value,
            }),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || '검색에 실패했습니다');

        resultEl.classList.remove('hidden');
        if (data.masked_emails.length === 0) {
            resultEl.innerHTML = '<p style="color:var(--text-dim)">일치하는 계정을 찾을 수 없습니다</p>';
        } else {
            resultEl.innerHTML = `<p>${data.message}</p>` +
                data.masked_emails.map(e => `<span class="masked-email">${e}</span>`).join('');
        }
    } catch (err) {
        showError(err.message);
    }
}

// ── 비밀번호 찾기 ──
function showResetPassword() {
    hideAllPanels();
    clearMessages();
    document.getElementById('modal-reset-password').classList.remove('hidden');
}

async function handleResetRequest(e) {
    e.preventDefault();
    clearMessages();

    try {
        const resp = await fetch('/api/auth/password-reset/request', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                email: document.getElementById('reset-email').value,
            }),
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || '오류가 발생했습니다');

        showSuccess(data.message);
    } catch (err) {
        showError(err.message);
    }
}

// ── 돌아가기 ──
function backToLogin() {
    hideAllPanels();
    clearMessages();
    document.getElementById('tab-login').classList.remove('hidden');
    document.querySelectorAll('.auth-tab').forEach(t => t.classList.remove('active'));
    document.querySelector('[data-tab="login"]').classList.add('active');
}

// ── 초기화 ──
document.addEventListener('DOMContentLoaded', async () => {
    // URL에서 에러 파라미터 확인
    const params = new URLSearchParams(window.location.search);
    const error = params.get('error');
    if (error === 'google_failed') showError('Google 로그인에 실패했습니다');
    if (error === 'no_email') showError('이메일 정보를 가져올 수 없습니다. 이메일 제공에 동의해주세요.');

    // 초대 코드 필요 여부 확인
    try {
        const resp = await fetch('/api/auth/settings');
        if (resp.ok) {
            const data = await resp.json();
            if (data.invite_code_required) {
                document.getElementById('invite-code-group').classList.remove('hidden');
                document.getElementById('reg-invite-code').required = true;
            }
        }
    } catch { /* ignore */ }
});
