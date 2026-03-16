/**
 * 미리보기 페이지 - 이미지 확인 & 재생성 & 영상 제작 확정
 */

const params = new URLSearchParams(window.location.search);
const jobId = params.get('job');

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

        const data = await resp.json();
        document.getElementById('preview-title').textContent = data.title;

        const grid = document.getElementById('preview-grid');
        grid.innerHTML = data.lines.map((line, i) => `
            <div class="preview-card">
                <div class="preview-image-wrap">
                    <img src="${data.image_urls[i]}?t=${Date.now()}" alt="이미지 ${i + 1}" class="preview-image">
                </div>
                <div class="preview-info">
                    <span class="line-num">${i + 1}</span>
                    <p class="preview-text">${escapeHtml(line.text)}</p>
                    <span class="line-motion">${line.motion}</span>
                    <button class="btn-small btn-secondary" onclick="regenerateImage(${i})">
                        재생성
                    </button>
                </div>
            </div>
        `).join('');
    } catch (e) {
        alert('에러: ' + e.message);
    }
}

async function regenerateImage(index) {
    try {
        const resp = await fetch(`/api/jobs/${jobId}/regenerate-image/${index}`, {
            method: 'POST',
        });
        if (resp.ok) {
            // 잠시 대기 후 새로고침
            setTimeout(() => loadPreview(), 3000);
        }
    } catch (e) {
        alert('재생성 실패: ' + e.message);
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
