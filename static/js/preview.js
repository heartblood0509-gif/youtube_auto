/**
 * 미리보기 페이지 - 이미지 확인 & 한글 요청으로 재생성 & 영상 제작 확정
 */

const params = new URLSearchParams(window.location.search);
const jobId = params.get('job');
let previewData = null;

async function loadPreview() {
    if (!jobId) {
        alert('작업 ID가 없습니다');
        return;
    }

    try {
        const resp = await fetch(`/api/jobs/${jobId}/preview`);
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '미리보기 로드 실패');
        }

        previewData = await resp.json();
        document.getElementById('preview-title').textContent = previewData.title;

        const grid = document.getElementById('preview-grid');
        grid.innerHTML = previewData.lines.map((line, i) => `
            <div class="preview-card" id="card-${i}">
                <div class="preview-image-wrap">
                    <img src="${previewData.image_urls[i]}?t=${Date.now()}" alt="이미지 ${i + 1}" class="preview-image">
                </div>
                <div class="preview-info">
                    <span class="line-num">${i + 1}</span>
                    <p class="preview-text">${escapeHtml(line.text)}</p>
                    <span class="line-motion">${line.motion}</span>
                    <button class="btn-small btn-secondary" onclick="toggleRequestInput(${i})">
                        재생성
                    </button>
                </div>
                <div class="prompt-edit hidden" id="prompt-edit-${i}">
                    <input type="text" class="korean-request" id="korean-req-${i}"
                           placeholder="예: 한국 여성이 거울 앞에서 화장하는 모습">
                    <button class="btn-small btn-primary" onclick="regenerateWithRequest(${i})">
                        이 요청으로 재생성
                    </button>
                </div>
            </div>
        `).join('');
    } catch (e) {
        alert('에러: ' + e.message);
    }
}

function toggleRequestInput(index) {
    const editDiv = document.getElementById(`prompt-edit-${index}`);
    editDiv.classList.toggle('hidden');
    if (!editDiv.classList.contains('hidden')) {
        document.getElementById(`korean-req-${index}`).focus();
    }
}

async function regenerateWithRequest(index) {
    const koreanRequest = document.getElementById(`korean-req-${index}`).value.trim();
    if (!koreanRequest) {
        alert('원하는 이미지를 한글로 설명해주세요');
        return;
    }
    await doRegenerate(index, koreanRequest);
}

async function doRegenerate(index, koreanRequest) {
    const card = document.getElementById(`card-${index}`);
    const imgWrap = card.querySelector('.preview-image-wrap');
    const img = card.querySelector('.preview-image');

    // 로딩 오버레이 표시
    img.style.opacity = '0.2';
    let overlay = card.querySelector('.regen-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.className = 'regen-overlay';
        overlay.innerHTML = '<div class="regen-spinner"></div><p class="regen-text">이미지 생성 중...</p>';
        imgWrap.style.position = 'relative';
        imgWrap.appendChild(overlay);
    }
    overlay.style.display = 'flex';

    // 재생성 버튼 비활성화
    const regenBtns = card.querySelectorAll('button');
    regenBtns.forEach(btn => btn.disabled = true);

    try {
        const resp = await fetch(`/api/jobs/${jobId}/regenerate-image/${index}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ korean_request: koreanRequest }),
        });

        if (resp.ok) {
            let elapsed = 0;
            const interval = setInterval(async () => {
                elapsed += 1000;
                // 진행 시간 표시
                const sec = Math.floor(elapsed / 1000);
                const textEl = overlay.querySelector('.regen-text');
                if (textEl) textEl.textContent = `이미지 생성 중... ${sec}초`;

                if (elapsed > 60000) {
                    clearInterval(interval);
                    overlay.style.display = 'none';
                    regenBtns.forEach(btn => btn.disabled = false);
                    loadPreview();
                    return;
                }
                try {
                    const statusResp = await fetch(`/api/jobs/${jobId}`);
                    const job = await statusResp.json();
                    if (job.status === 'failed') {
                        clearInterval(interval);
                        overlay.style.display = 'none';
                        regenBtns.forEach(btn => btn.disabled = false);
                        alert(job.error || '이미지 재생성 실패');
                        loadPreview();
                        return;
                    }
                    if (job.status === 'preview_ready' && elapsed >= 3000) {
                        clearInterval(interval);
                        img.src = `${previewData.image_urls[index]}?t=${Date.now()}`;
                        img.style.opacity = '1';
                        overlay.style.display = 'none';
                        regenBtns.forEach(btn => btn.disabled = false);
                    }
                } catch (e) { /* retry */ }
            }, 1000);
        }
    } catch (e) {
        alert('재생성 실패: ' + e.message);
        img.style.opacity = '1';
        overlay.style.display = 'none';
        regenBtns.forEach(btn => btn.disabled = false);
    }
}

async function confirmAndRender() {
    const btn = document.getElementById('btn-confirm');
    btn.disabled = true;
    btn.textContent = '영상 제작 시작 중...';

    try {
        const resp = await fetch(`/api/jobs/${jobId}/confirm`, {
            method: 'POST',
        });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '확정 실패');
        }

        window.location.href = `/static/status.html?job=${jobId}&phase=render`;
    } catch (e) {
        alert('에러: ' + e.message);
        btn.disabled = false;
        btn.textContent = '영상 제작 시작';
    }
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

loadPreview();
