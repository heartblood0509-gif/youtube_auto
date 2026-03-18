/**
 * 메인 페이지 - 멀티스텝 대본 생성
 * Step 1: 주제 & 설정 → Step 2: 제목 선택 → Step 3: 나레이션 확인 → Step 4: 최종 확인 → Job 생성
 */

// 슬라이더 값 표시
document.getElementById('tts-speed').addEventListener('input', function() {
    document.getElementById('speed-val').textContent = this.value;
});
document.getElementById('bgm-volume').addEventListener('input', function() {
    document.getElementById('bgm-val').textContent = this.value;
});

// 카테고리 필드 토글
function toggleCategoryFields() {
    const category = document.getElementById('category').value;
    const cosmeticsFields = document.getElementById('cosmetics-fields');
    cosmeticsFields.style.display = category === 'cosmetics' ? 'block' : 'none';
}

// BGM 시작 지점 슬라이더/입력 동기화
document.getElementById('bgm-start').addEventListener('input', function() {
    const val = parseFloat(this.value);
    document.getElementById('bgm-start-val').textContent = formatTime(val);
    document.getElementById('bgm-start-sec').value = val.toFixed(1);
});
document.getElementById('bgm-start-sec').addEventListener('change', function() {
    const val = parseFloat(this.value) || 0;
    const clamped = Math.min(Math.max(val, 0), bgmDuration);
    this.value = clamped.toFixed(1);
    document.getElementById('bgm-start').value = clamped;
    document.getElementById('bgm-start-val').textContent = formatTime(clamped);
});

// ──────────────────────────────────
// 상태 관리
// ──────────────────────────────────
let titleOptions = null;
let selectedTitle = null;
let narrationData = null;
let scriptData = null;

// BGM 상태
let bgmList = [];
let selectedBgm = null;
let bgmAudio = null;
let bgmDuration = 0;

function getCategoryPayload() {
    const category = document.getElementById('category').value;
    const payload = { category };
    if (category === 'cosmetics') {
        const painPoint = document.getElementById('pain-point').value.trim();
        const ingredient = document.getElementById('ingredient').value.trim();
        const mentionType = document.getElementById('mention-type').value;
        if (painPoint) payload.pain_point = painPoint;
        if (ingredient) payload.ingredient = ingredient;
        payload.mention_type = mentionType;
    }
    return payload;
}

// ──────────────────────────────────
// Step 2: 제목 생성
// ──────────────────────────────────
async function generateTitles() {
    const topic = document.getElementById('topic').value.trim();
    if (!topic) {
        alert('주제를 입력해주세요');
        return;
    }

    showLoading('제목 생성 중...');

    try {
        const payload = { topic, ...getCategoryPayload() };
        const resp = await fetch('/api/generate/titles', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '제목 생성 실패');
        }

        titleOptions = await resp.json();
        displayTitles(titleOptions);
    } catch (e) {
        alert('에러: ' + e.message);
        hideLoading();
    }
}

function displayTitles(data) {
    hideLoading();
    hideAllSteps();
    document.getElementById('step-input').classList.remove('hidden');
    document.getElementById('step-titles').classList.remove('hidden');

    const container = document.getElementById('title-options');
    container.innerHTML = data.titles.map((opt, i) => `
        <div class="title-option" onclick="selectTitle(${i})">
            <div class="title-text">${escapeHtml(opt.title)}</div>
            <div class="title-hook">${escapeHtml(opt.hook)}</div>
        </div>
    `).join('');
}

function selectTitle(index) {
    selectedTitle = titleOptions.titles[index].title;

    // 선택 시각 표시
    document.querySelectorAll('.title-option').forEach((el, i) => {
        el.classList.toggle('selected', i === index);
    });

    // 자동으로 나레이션 생성
    generateNarration();
}

// ──────────────────────────────────
// Step 3: 나레이션 생성
// ──────────────────────────────────
async function generateNarration() {
    if (!selectedTitle) return;

    showLoading('나레이션 생성 중...');

    try {
        const topic = document.getElementById('topic').value.trim();
        const payload = {
            topic,
            selected_title: selectedTitle,
            num_lines: 6,
            ...getCategoryPayload(),
        };

        const resp = await fetch('/api/generate/narration', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '나레이션 생성 실패');
        }

        narrationData = await resp.json();
        displayNarration(narrationData);
    } catch (e) {
        alert('에러: ' + e.message);
        hideLoading();
    }
}

function displayNarration(data) {
    hideLoading();
    hideAllSteps();
    document.getElementById('step-input').classList.remove('hidden');
    document.getElementById('step-titles').classList.remove('hidden');
    document.getElementById('step-narration').classList.remove('hidden');

    document.getElementById('selected-title-display').value = selectedTitle;

    const roleLabels = {
        hook: 'Hook',
        problem: '문제',
        insight: '핵심',
        solution1: '해결 1',
        solution2: '해결 2',
        cta: 'CTA',
    };

    const container = document.getElementById('narration-lines');
    container.innerHTML = data.lines.map((line, i) => {
        const charCount = line.text.replace(/[?,!.~…]/g, '').length;
        const overClass = charCount > 24 ? 'over' : '';
        return `
        <div class="narration-line">
            <div class="line-header">
                <span class="line-num">${i + 1}</span>
                <span class="narration-role">${roleLabels[line.role] || line.role}</span>
                <span class="char-count ${overClass}">${charCount}/24</span>
            </div>
            <input type="text" class="line-text" value="${escapeHtml(line.text)}"
                   data-index="${i}" oninput="updateCharCount(this)">
        </div>
    `}).join('');
}

function updateCharCount(input) {
    const count = input.value.replace(/[?,!.~…]/g, '').length;
    const counter = input.parentElement.querySelector('.char-count');
    counter.textContent = `${count}/24`;
    counter.classList.toggle('over', count > 24);
}

function regenerateNarration() {
    generateNarration();
}

// ──────────────────────────────────
// Step 4: 이미지 프롬프트 생성
// ──────────────────────────────────
async function approveNarration() {
    // 편집된 나레이션 텍스트 수집
    const textInputs = document.querySelectorAll('#narration-lines .line-text');
    const narrationLines = Array.from(textInputs).map(input => input.value.trim());

    if (narrationLines.some(line => !line)) {
        alert('빈 나레이션 줄이 있습니다');
        return;
    }

    showLoading('이미지 프롬프트 생성 중...');

    try {
        const style = document.getElementById('style').value;
        const payload = {
            narration_lines: narrationLines,
            style,
        };

        const resp = await fetch('/api/generate/image-prompts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });

        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '이미지 프롬프트 생성 실패');
        }

        scriptData = await resp.json();
        displayFinal(scriptData);
    } catch (e) {
        alert('에러: ' + e.message);
        hideLoading();
    }
}

function displayFinal(data) {
    hideLoading();
    hideAllSteps();
    document.getElementById('step-input').classList.remove('hidden');
    document.getElementById('step-titles').classList.remove('hidden');
    document.getElementById('step-narration').classList.remove('hidden');
    document.getElementById('step-final').classList.remove('hidden');

    document.getElementById('title-text').value = selectedTitle;

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

// ──────────────────────────────────
// Step 5: Job 생성 (기존 로직 유지)
// ──────────────────────────────────
async function createJob() {
    if (!scriptData) return;

    // 수정된 값 반영
    const title = document.getElementById('title-text').value;
    const textInputs = document.querySelectorAll('#step-final .line-text');
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
        bgm_filename: selectedBgm || null,
        bgm_start_sec: parseFloat(document.getElementById('bgm-start-sec').value) || 0,
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
        window.location.href = `/static/status.html?job=${job.job_id}&phase=images`;
    } catch (e) {
        alert('에러: ' + e.message);
        hideLoading();
    }
}

// ──────────────────────────────────
// 유틸리티
// ──────────────────────────────────
function hideAllSteps() {
    ['step-input', 'step-titles', 'step-narration', 'step-final', 'step-loading'].forEach(id => {
        document.getElementById(id).classList.add('hidden');
    });
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

// ──────────────────────────────────
// BGM 선택 + 미리듣기
// ──────────────────────────────────
async function loadBgmList() {
    try {
        const resp = await fetch('/api/assets/bgm');
        if (!resp.ok) return;
        bgmList = await resp.json();
        renderBgmList();
    } catch (e) {
        console.error('BGM 목록 로드 실패:', e);
        document.getElementById('bgm-list').innerHTML =
            '<p class="text-dim">BGM 목록을 불러올 수 없습니다</p>';
    }
}

function renderBgmList() {
    const container = document.getElementById('bgm-list');
    if (bgmList.length === 0) {
        container.innerHTML = '<p class="text-dim">bgm/ 폴더에 MP3 파일이 없습니다</p>';
        return;
    }
    container.innerHTML = bgmList.map((bgm, i) => `
        <div class="bgm-card ${selectedBgm === bgm.filename ? 'selected' : ''}"
             onclick="selectBgm(${i})">
            <div class="bgm-name">${escapeHtml(bgm.filename.replace(/\.(mp3|wav|ogg)$/i, ''))}</div>
            <div class="bgm-duration">${formatTime(bgm.duration)}</div>
            <button class="btn-small bgm-play-btn ${bgmAudio && bgmAudio._bgmIdx === i ? 'playing' : ''}"
                    onclick="event.stopPropagation(); toggleBgmPreview(${i})"
                    title="미리듣기">
                ${bgmAudio && bgmAudio._bgmIdx === i ? '■' : '▶'}
            </button>
        </div>
    `).join('');
}

function selectBgm(index) {
    selectedBgm = bgmList[index].filename;
    bgmDuration = bgmList[index].duration;
    renderBgmList();

    // 시작 지점 섹션 표시 + 초기화
    const startSection = document.getElementById('bgm-start-section');
    startSection.classList.remove('hidden');
    const slider = document.getElementById('bgm-start');
    slider.max = bgmDuration;
    slider.value = 0;
    document.getElementById('bgm-start-val').textContent = '0:00';
    document.getElementById('bgm-start-sec').value = '0.0';
    document.getElementById('bgm-start-sec').max = bgmDuration;
}

function toggleBgmPreview(index) {
    // 이미 재생 중이면 정지
    if (bgmAudio) {
        bgmAudio.pause();
        const wasPlaying = bgmAudio._bgmIdx === index;
        bgmAudio = null;
        renderBgmList();
        if (wasPlaying) return;
    }

    const bgm = bgmList[index];
    bgmAudio = new Audio(bgm.url);
    bgmAudio._bgmIdx = index;

    // 선택된 BGM의 시작 지점부터 재생
    if (selectedBgm === bgm.filename) {
        bgmAudio.currentTime = parseFloat(document.getElementById('bgm-start-sec').value) || 0;
    }

    bgmAudio.play();
    renderBgmList();

    // 15초 후 자동 정지
    bgmAudio._timeout = setTimeout(() => {
        if (bgmAudio) {
            bgmAudio.pause();
            bgmAudio = null;
            renderBgmList();
        }
    }, 15000);

    // 재생 끝나면 정리
    bgmAudio.addEventListener('ended', () => {
        bgmAudio = null;
        renderBgmList();
    });
}

function formatTime(sec) {
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
}

// 페이지 로드 시 BGM 목록 가져오기
loadBgmList();
