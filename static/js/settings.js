/* 설정 페이지 로직 */

function showError(msg) {
    const el = document.getElementById('auth-error');
    el.textContent = msg;
    el.classList.remove('hidden');
    document.getElementById('auth-success').classList.add('hidden');
}

function showSuccess(msg) {
    const el = document.getElementById('auth-success');
    el.textContent = msg;
    el.classList.remove('hidden');
    document.getElementById('auth-error').classList.add('hidden');
}

function toggleKeyVisibility(inputId) {
    const input = document.getElementById(inputId);
    input.type = input.type === 'password' ? 'text' : 'password';
}

function setStatus(id, isSet) {
    const el = document.getElementById(id);
    if (isSet) {
        el.textContent = '(설정됨)';
        el.className = 'key-status set';
    } else {
        el.textContent = '(미설정)';
        el.className = 'key-status unset';
    }
}

// 페이지 로드 시 현재 키 상태 표시
async function loadKeyStatus() {
    try {
        const resp = await authFetch('/api/auth/api-keys');
        if (!resp || !resp.ok) return;
        const data = await resp.json();

        setStatus('gemini-status', data.gemini);
        setStatus('typecast-status', data.typecast);
        setStatus('fal-status', data.fal);

        // 마스킹된 키를 placeholder에 표시
        if (data.gemini) document.getElementById('gemini-key').placeholder = data.gemini;
        if (data.typecast) document.getElementById('typecast-key').placeholder = data.typecast;
        if (data.fal) document.getElementById('fal-key').placeholder = data.fal;
    } catch { /* ignore */ }
}

async function handleSaveKeys(e) {
    e.preventDefault();
    document.getElementById('auth-error').classList.add('hidden');
    document.getElementById('auth-success').classList.add('hidden');

    const btn = document.getElementById('btn-save');
    btn.disabled = true;
    btn.textContent = '검증 중...';

    const payload = {};
    const gemini = document.getElementById('gemini-key').value;
    const typecast = document.getElementById('typecast-key').value;
    const fal = document.getElementById('fal-key').value;

    // 값이 입력된 필드만 전송 (빈 칸 = 삭제)
    if (gemini !== '') payload.gemini_api_key = gemini;
    if (typecast !== '') payload.typecast_api_key = typecast;
    if (fal !== '') payload.fal_key = fal;

    // 아무 입력도 없으면 안내
    if (Object.keys(payload).length === 0) {
        showError('변경할 키를 입력하세요. 키를 삭제하려면 빈 칸으로 두고 저장하세요.');
        btn.disabled = false;
        btn.textContent = '저장';
        return;
    }

    try {
        const resp = await authFetch('/api/auth/api-keys', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        if (!resp || !resp.ok) {
            const data = await resp.json();
            throw new Error(data.detail || '저장에 실패했습니다');
        }

        showSuccess('API 키가 저장되었습니다');
        // 입력 필드 초기화 + 상태 갱신
        document.getElementById('gemini-key').value = '';
        document.getElementById('typecast-key').value = '';
        document.getElementById('fal-key').value = '';
        await loadKeyStatus();
    } catch (err) {
        showError(err.message);
    } finally {
        btn.disabled = false;
        btn.textContent = '저장';
    }
}

document.addEventListener('DOMContentLoaded', loadKeyStatus);
