/**
 * 메인 페이지 - 대본 생성 & 작업 생성
 */

// 슬라이더 값 표시
document.getElementById('tts-speed').addEventListener('input', function() {
    document.getElementById('speed-val').textContent = this.value;
});
document.getElementById('bgm-volume').addEventListener('input', function() {
    document.getElementById('bgm-val').textContent = this.value;
});

// 생성된 대본 데이터
let scriptData = null;

async function generateScript() {
    const topic = document.getElementById('topic').value.trim();
    if (!topic) {
        alert('주제를 입력해주세요');
        return;
    }

    const style = document.getElementById('style').value;

    // 로딩 표시
    showLoading('대본 생성 중...');

    try {
        const resp = await fetch('/api/generate/script', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ topic, style, num_lines: 6 }),
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '대본 생성 실패');
        }

        scriptData = await resp.json();
        displayScript(scriptData);
    } catch (e) {
        alert('에러: ' + e.message);
        hideLoading();
    }
}

function displayScript(data) {
    hideLoading();
    document.getElementById('step-script').classList.remove('hidden');
    document.getElementById('title-text').value = data.title;

    const container = document.getElementById('script-lines');
    container.innerHTML = data.lines.map((line, i) => `
        <div class="script-line">
            <div class="line-header">
                <span class="line-num">${i + 1}</span>
                <span class="line-motion">${motionLabel(line.motion)}</span>
            </div>
            <input type="text" class="line-text" value="${escapeHtml(line.text)}" data-index="${i}">
            <p class="line-prompt">${escapeHtml(line.image_prompt)}</p>
        </div>
    `).join('');
}

async function createJob() {
    if (!scriptData) return;

    // 수정된 값 반영
    const title = document.getElementById('title-text').value;
    const textInputs = document.querySelectorAll('.line-text');
    textInputs.forEach((input, i) => {
        scriptData.lines[i].text = input.value;
    });

    const payload = {
        topic: document.getElementById('topic').value,
        style: document.getElementById('style').value,
        tts_engine: document.getElementById('tts-engine').value,
        tts_speed: parseFloat(document.getElementById('tts-speed').value),
        title: title,
        lines: scriptData.lines,
        bgm_volume: parseInt(document.getElementById('bgm-volume').value) / 100,
    };

    showLoading('이미지 생성 작업 등록 중...');

    try {
        const resp = await fetch('/api/jobs/', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '작업 생성 실패');
        }

        const job = await resp.json();
        // 상태 페이지로 이동 (이미지 생성 → 미리보기)
        window.location.href = `/static/status.html?job=${job.job_id}&phase=images`;
    } catch (e) {
        alert('에러: ' + e.message);
        hideLoading();
    }
}

function showLoading(text) {
    document.getElementById('step-loading').classList.remove('hidden');
    document.getElementById('loading-text').textContent = text;
    document.getElementById('btn-generate').disabled = true;
}

function hideLoading() {
    document.getElementById('step-loading').classList.add('hidden');
    document.getElementById('btn-generate').disabled = false;
}

function motionLabel(motion) {
    const labels = {
        zoom_in: 'Zoom In',
        zoom_out: 'Zoom Out',
        pan_left: 'Pan Left',
        pan_right: 'Pan Right',
        pan_up: 'Pan Up',
        pan_down: 'Pan Down',
    };
    return labels[motion] || motion;
}

function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}
