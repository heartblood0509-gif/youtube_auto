/* 인증 유틸리티 - 모든 페이지에서 공유 */

let currentUser = null;
let _authReady;
const authReady = new Promise(resolve => { _authReady = resolve; });

/**
 * fetch 래퍼: credentials 자동 포함 + 401 시 토큰 갱신 또는 로그인 리다이렉트
 */
let _refreshing = null; // 토큰 갱신 중복 방지

async function authFetch(url, options = {}) {
    options.credentials = 'same-origin';
    let resp = await fetch(url, options);

    // 401이면 토큰 갱신 시도 (동시 요청 시 1번만 갱신)
    if (resp.status === 401) {
        if (!_refreshing) {
            _refreshing = fetch('/api/auth/refresh', {
                method: 'POST',
                credentials: 'same-origin',
            }).finally(() => { _refreshing = null; });
        }
        const refreshResp = await _refreshing;
        if (refreshResp && refreshResp.ok) {
            resp = await fetch(url, options);
        } else {
            window.location.href = '/static/login.html';
            // 리다이렉트 중 호출자 에러 방지: 빈 응답 객체 반환
            return new Response('{}', { status: 401, headers: { 'Content-Type': 'application/json' } });
        }
    }
    return resp;
}

/**
 * 페이지 로드 시 인증 확인
 */
async function checkAuth() {
    try {
        const resp = await fetch('/api/auth/me', { credentials: 'same-origin' });
        if (resp.ok) {
            const data = await resp.json();
            currentUser = data.user;
            _authReady(currentUser);
            updateUserUI(data.user);
            return true;
        }

        // 토큰 갱신 시도
        const refreshResp = await fetch('/api/auth/refresh', {
            method: 'POST',
            credentials: 'same-origin',
        });
        if (refreshResp.ok) {
            const data = await refreshResp.json();
            currentUser = data.user;
            _authReady(currentUser);
            updateUserUI(data.user);
            return true;
        }

        window.location.href = '/static/login.html';
        return false;
    } catch {
        window.location.href = '/static/login.html';
        return false;
    }
}

/**
 * 사용자 정보 UI 업데이트 (로그아웃 버튼 등)
 */
function updateUserUI(user) {
    const el = document.getElementById('user-info');
    if (el && user) {
        el.textContent = '';
        const nameSpan = document.createElement('span');
        nameSpan.className = 'user-name';
        nameSpan.textContent = user.nickname || user.email;
        const settingsBtn = document.createElement('button');
        settingsBtn.className = 'btn-logout';
        settingsBtn.textContent = '설정';
        settingsBtn.onclick = () => { window.location.href = '/static/settings.html'; };
        const logoutBtn = document.createElement('button');
        logoutBtn.className = 'btn-logout';
        logoutBtn.textContent = '로그아웃';
        logoutBtn.onclick = logout;
        el.appendChild(nameSpan);
        el.appendChild(settingsBtn);
        el.appendChild(logoutBtn);
        el.classList.remove('hidden');
    }
}

/**
 * 로그아웃
 */
async function logout() {
    await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' });
    window.location.href = '/static/login.html';
}

// 로그인/리셋 페이지가 아니면 인증 확인
document.addEventListener('DOMContentLoaded', () => {
    const path = window.location.pathname;
    if (!path.includes('login') && !path.includes('reset-password')) {
        checkAuth();
    }
});
