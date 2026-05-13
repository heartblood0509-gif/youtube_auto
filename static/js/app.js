/**
 * 메인 페이지 - 멀티스텝 대본 생성
 * Step 1: 주제 설정 → Step 2: 제목 선택 → Step 3: 나레이션 확인
 * → Step 4: 음성 설정 → Step 5: BGM 설정 → "이미지 생성 시작" → Job 생성
 */

function showFriendlyError(msg) {
    const is503 = msg.includes('503') || msg.includes('UNAVAILABLE');
    const is429 = msg.includes('429') || msg.includes('RESOURCE_EXHAUSTED');
    let userMsg;
    if (is503) {
        userMsg = 'Google AI 서버가 현재 많이 바쁜 상태입니다.\n자동으로 3회 재시도했지만 실패했습니다.\n\n1~2분 후에 다시 시도해주세요.';
    } else if (is429) {
        userMsg = 'API 요청 횟수 제한에 도달했습니다.\n1분 후에 다시 시도해주세요.';
    } else {
        userMsg = '요청 처리에 실패했습니다.\n다시 시도해주세요.\n\n[상세 정보]\n' + msg;
    }
    alert(userMsg);
}

// ── TTS 음성 옵션 (엔진별) ──
const VOICE_OPTIONS = {
    typecast: [
        { value: "tc_62e8f21e979b3860fe2f6a24", label: "혜리 (여성)" },
        { value: "tc_611c3f692fac944dff493a04", label: "세희 (여성)" },
        { value: "tc_6568164fe05ddffee8b0e271", label: "시연 (여성)" },
        { value: "tc_622964d6255364be41659078", label: "세나 (여성)" },
        { value: "tc_61659c5818732016a95fe763", label: "류은 (여성)" },
        { value: "tc_632293f759d649937b97f323", label: "진우 (남성)" },
        { value: "tc_668f4f533ea5c6ce5e43fd48", label: "우성 (남성)" },
        { value: "tc_6059dad0b83880769a50502f", label: "창수 (남성)" },
        { value: "tc_61de29497924994f5abd68db", label: "세진 (남성)" },
    ],
};

// ── 스텝 구성 ──
// 모드 선택 화면(step-mode-select)은 STEPS 외부의 진입 화면이라 배열에 포함하지 않는다.
// 타임라인은 사용자가 모드를 고른 다음에야 활성화된다.
const STEPS_AI_FULL = [
    { id: 'step-input',     label: '주제',     summaryFn: () => document.getElementById('topic').value || '' },
    { id: 'step-titles',    label: '제목',     summaryFn: () => selectedTitle || '' },
    { id: 'step-narration', label: '나레이션', summaryFn: () => selectedTitle || '' },
    { id: 'step-tts',       label: '음성',     summaryFn: () => {
        const sel = document.getElementById('tts-voice');
        return sel && sel.selectedOptions[0] ? sel.selectedOptions[0].text : '';
    }},
    { id: 'step-bgm',       label: 'BGM',      summaryFn: () => selectedBgm ? selectedBgm.replace(/\.(mp3|wav|ogg)$/i, '') : '없음' },
];
const STEPS_USER_ASSETS = [
    { id: 'step-user-script', label: '대본',     summaryFn: () => ((window._userScript || '').slice(0, 12)) },
    { id: 'step-user-lines',  label: '자산',     summaryFn: () => ((window._splitLines || []).length ? `${window._splitLines.length}줄` : '') },
    { id: 'step-tts',         label: '음성',     summaryFn: () => {
        const sel = document.getElementById('tts-voice');
        return sel && sel.selectedOptions[0] ? sel.selectedOptions[0].text : '';
    }},
    { id: 'step-bgm',         label: 'BGM',      summaryFn: () => selectedBgm ? selectedBgm.replace(/\.(mp3|wav|ogg)$/i, '') : '없음' },
];
let STEPS = STEPS_AI_FULL;
let currentStepIndex = 0;
window._generationMode = null;  // 'ai_full' | 'user_assets' — 모드 선택 전엔 null
window._draftJobId = null;       // 카드 B: draft Job ID
window._splitLines = [];         // 카드 B: 쪼개진 대본 (텍스트만)
window._userScript = '';         // 카드 B: 원본 자유 대본

// ── 상태 관리 ──
let titleOptions = null;
let selectedTitle = null;
let titleLine1 = '';
let titleLine2 = '';
let narrationData = null;
let scriptData = null;
let bgmList = [];
let selectedBgm = null;
let bgmAudio = null;
let bgmDuration = 0;
let previewAudio = null;
let isCreatingJob = false;

// ── 카테고리 필드 토글 ──
function toggleCategoryFields() {
    const category = document.getElementById('category').value;
    const isCosmetics = category === 'cosmetics';

    // 영상 목적 드롭다운은 화장품일 때만 표시 (카테고리 바로 다음 위치)
    const ctWrapper = document.getElementById('content-type-wrapper');
    if (ctWrapper) ctWrapper.style.display = isCosmetics ? '' : 'none';

    // 화장품 전용 필드 컨테이너 (페인포인트 · 성분 · 제품 그리드)
    const cosmeticsFields = document.getElementById('cosmetics-fields');
    cosmeticsFields.style.display = isCosmetics ? 'block' : 'none';

    const topicHelp = document.getElementById('topic-help');
    if (topicHelp) {
        topicHelp.innerHTML = isCosmetics
            ? '내 화장품이 해결하는 피부 고민을 적어주세요.<br>예: 홍조 피부 진정 방법 / 건조 피부 보습 루틴 / 모공 축소 관리법'
            : '어떤 내용의 영상을 만들지 한 줄로 적어주세요.<br>예: 여름철 자외선 차단 꿀팁 / 초보 운동 루틴 / 다이어트 식단 추천';
    }

    toggleContentType();
}

// ── 영상 목적(정보성/홍보성/홍보성 고정댓글 유도형) 토글 — 화장품 카테고리에서만 의미 있음 ──
function toggleContentType() {
    const category = document.getElementById('category').value;
    if (category !== 'cosmetics') return;
    const ct = document.getElementById('content-type').value;
    const show = (id, cond) => {
        const el = document.getElementById(id);
        if (el) el.style.display = cond ? '' : 'none';
    };
    // 홍보성 전용 필드 (pain_point, ingredient)
    show('painpoint-field', ct === 'promo');
    show('ingredient-field', ct === 'promo');
    // 제품 이미지: 홍보성은 필수, 홍보성(고정댓글 유도형)은 선택(옵션). 정보성은 숨김.
    show('product-templates', ct === 'promo' || ct === 'promo_comment');
    // 정보성 전용 필드 (keyword)
    show('info-keyword-field', ct === 'info');
}

// ──────────────────────────────────
// 제품 이미지 템플릿 (CTA 라인용)
// ──────────────────────────────────
let userProducts = [];
window._selectedProductId = null;

async function loadUserProducts() {
    try {
        const resp = await authFetch('/api/products');
        if (!resp.ok) return;
        userProducts = await resp.json();
        renderProductGrid();
    } catch (e) {
        console.error('제품 목록 로드 실패:', e);
    }
}

function renderProductGrid() {
    const grid = document.getElementById('product-grid');
    if (!grid) return;
    const uploadCard = `
        <div class="style-card product-upload-card" onclick="triggerProductUpload()">
            <div class="style-card-preview">
                <span class="style-card-icon">+</span>
            </div>
            <div class="style-card-meta">
                <span class="style-card-name">제품 추가</span>
            </div>
        </div>
    `;
    const productCards = userProducts.map(p => {
        const selected = window._selectedProductId === p.id ? 'selected' : '';
        return `
            <div class="style-card ${selected}" data-product-id="${p.id}" onclick="selectProduct('${p.id}')">
                <button class="product-delete-btn" onclick="deleteProduct(event, '${p.id}')" title="삭제">×</button>
                <div class="style-card-preview">
                    <img class="product-thumb" src="/api/products/${p.id}/image" alt="${escapeHtml(p.name)}">
                </div>
                <div class="style-card-meta">
                    <span class="style-card-name">${escapeHtml(p.name)}</span>
                </div>
            </div>
        `;
    }).join('');
    grid.innerHTML = productCards + uploadCard;
}

function selectProduct(id) {
    window._selectedProductId = id;
    renderProductGrid();
}

function triggerProductUpload() {
    if (userProducts.length >= 20) {
        alert('제품은 최대 20개까지 등록 가능합니다.\n기존 제품을 삭제 후 다시 시도해주세요.');
        return;
    }
    const input = document.getElementById('product-file-input');
    input.value = '';  // 같은 파일 재선택 허용
    input.onchange = handleProductFileSelect;
    input.click();
}

async function handleProductFileSelect(e) {
    const file = e.target.files[0];
    if (!file) return;

    const name = prompt('제품명을 입력하세요 (최대 50자)', file.name.replace(/\.[^.]+$/, ''));
    if (!name || !name.trim()) return;

    const formData = new FormData();
    formData.append('file', file);
    formData.append('name', name.trim());

    try {
        const resp = await authFetch('/api/products', {
            method: 'POST',
            body: formData,
        });
        if (!resp.ok) {
            const err = await resp.json();
            alert(err.detail || '업로드 실패');
            return;
        }
        const newProduct = await resp.json();
        window._selectedProductId = newProduct.id;  // 업로드 직후 자동 선택
        await loadUserProducts();
    } catch (err) {
        alert('업로드 실패: ' + err.message);
    }
}

async function deleteProduct(event, id) {
    event.stopPropagation();
    const product = userProducts.find(p => p.id === id);
    if (!product) return;
    if (!confirm(`"${product.name}" 제품을 삭제하시겠어요?`)) return;

    try {
        const resp = await authFetch(`/api/products/${id}`, { method: 'DELETE' });
        if (!resp.ok) {
            const err = await resp.json();
            alert(err.detail || '삭제 실패');
            return;
        }
        if (window._selectedProductId === id) {
            window._selectedProductId = null;
        }
        await loadUserProducts();
    } catch (err) {
        alert('삭제 실패: ' + err.message);
    }
}

function autoSplitTitle(text) {
    const words = text.split(' ').filter(w => w);
    if (words.length <= 1) return [text, ''];

    let bestSplit = 1, bestDiff = Infinity;
    for (let i = 1; i < words.length; i++) {
        const l1 = words.slice(0, i).join(' ');
        const l2 = words.slice(i).join(' ');
        const diff = Math.abs(l1.length - l2.length);
        if (diff < bestDiff) {
            bestDiff = diff;
            bestSplit = i;
        }
    }
    return [words.slice(0, bestSplit).join(' '), words.slice(bestSplit).join(' ')];
}

function onTitleLineEdited() {
    titleLine1 = document.getElementById('title-line1').value;
    titleLine2 = document.getElementById('title-line2').value;
    selectedTitle = titleLine2 ? titleLine1 + ' ' + titleLine2 : titleLine1;
    updateTitlePreview();
}

function updateTitlePreview() {
    const el1 = document.getElementById('preview-line1');
    const el2 = document.getElementById('preview-line2');
    const frame = document.getElementById('title-preview-frame');

    el1.textContent = titleLine1;
    el2.textContent = titleLine2;

    requestAnimationFrame(() => {
        const frameW = frame.offsetWidth;
        const overflow = el1.scrollWidth > frameW || el2.scrollWidth > frameW;
        frame.classList.toggle('overflow', overflow);
    });
}

function getCategoryPayload() {
    const category = document.getElementById('category').value;
    const payload = { category };
    if (category === 'cosmetics') {
        const contentType = document.getElementById('content-type').value;
        payload.content_type = contentType;
        if (contentType === 'promo') {
            const painPoint = document.getElementById('pain-point').value.trim();
            const ingredient = document.getElementById('ingredient').value.trim();
            if (painPoint) payload.pain_point = painPoint;
            if (ingredient) payload.ingredient = ingredient;
        } else if (contentType === 'info') {
            const keyword = document.getElementById('info-keyword').value.trim();
            if (keyword) payload.keyword = keyword;
        }
        // promo_comment: 주제 외 추가 필드 없음
    }
    return payload;
}

// ──────────────────────────────────
// "제목 생성하기" 버튼 활성/비활성 상태 갱신
// 카테고리·영상 목적은 기본값이 있으므로 topic 트림값만 검사한다.
// ──────────────────────────────────
function updateGenerateButtonState() {
    const btn = document.getElementById('btn-generate');
    if (!btn) return;
    btn.disabled = !document.getElementById('topic').value.trim();
}
document.getElementById('topic').addEventListener('input', updateGenerateButtonState);
updateGenerateButtonState();

// ──────────────────────────────────
// Step 2: 제목 생성
// ──────────────────────────────────
async function generateTitles() {
    const topic = document.getElementById('topic').value.trim();
    if (!topic) {
        alert('주제를 입력해주세요');
        return;
    }

    advanceToStep(1);
    showLoading('제목 생성 중...');

    try {
        const payload = { topic, ...getCategoryPayload() };
        // 제목 생성 시점의 키워드를 저장해 나레이션 단계에서 불일치 감지
        window._keywordAtTitleGen = payload.keyword || '';
        const resp = await authFetch('/api/generate/titles', {
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
        showFriendlyError(e.message);
        hideLoading();
        goToStep(0);
    }
}

function displayTitles(data) {
    hideLoading();
    hideStepGuide('step-titles');
    document.getElementById('btn-next-title').disabled = true;
    document.getElementById('title-split-editor').classList.add('hidden');

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

    document.querySelectorAll('.title-option').forEach((el, i) => {
        el.classList.toggle('selected', i === index);
    });

    const [line1, line2] = autoSplitTitle(selectedTitle);
    titleLine1 = line1;
    titleLine2 = line2;
    document.getElementById('title-line1').value = line1;
    document.getElementById('title-line2').value = line2;
    updateTitlePreview();
    document.getElementById('title-split-editor').classList.remove('hidden');
    document.getElementById('btn-next-title').disabled = false;
}

function confirmTitle() {
    generateNarration();
}

// ──────────────────────────────────
// Step 3: 나레이션 생성
// ──────────────────────────────────
async function generateNarration() {
    if (!selectedTitle) return;

    // 핵심 키워드가 제목 생성 이후 바뀌었는지 확인 (정보성 전용)
    const currentKeyword = (document.getElementById('info-keyword')?.value || '').trim();
    const titleGenKeyword = window._keywordAtTitleGen || '';
    const _ct = document.getElementById('content-type')?.value;
    const _cat = document.getElementById('category')?.value;
    if (_cat === 'cosmetics' && _ct === 'info' && currentKeyword !== titleGenKeyword) {
        const ok = confirm(
            `⚠️ 핵심 키워드가 바뀌었습니다\n` +
            `(제목 생성 시: "${titleGenKeyword || '(비어있음)'}" → 현재: "${currentKeyword || '(비어있음)'}")\n\n` +
            `제목은 기존 키워드 방향으로 만들어졌는데, 나레이션은 새 키워드 방향으로 생성됩니다.\n` +
            `앞뒤가 맞지 않을 수 있어요.\n\n` +
            `그래도 진행하시겠어요?\n(취소하고 제목부터 다시 생성하는 것을 권장합니다)`
        );
        if (!ok) return;
        // 사용자가 "진행"을 선택했으므로 스냅샷 갱신 — 재생성 시 같은 경고 반복 방지
        window._keywordAtTitleGen = currentKeyword;
    }

    advanceToStep(2);
    showLoading('나레이션 생성 중...');

    try {
        const topic = document.getElementById('topic').value.trim();
        const catPayload = getCategoryPayload();
        const isPromoComment = catPayload.category === 'cosmetics' && catPayload.content_type === 'promo_comment';
        const payload = {
            topic,
            selected_title: selectedTitle,
            num_lines: isPromoComment ? 5 : 6,
            ...catPayload,
        };

        const resp = await authFetch('/api/generate/narration', {
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
        showFriendlyError(e.message);
        hideLoading();
    }
}

function displayNarration(data) {
    hideLoading();
    hideStepGuide('step-narration');

    document.getElementById('selected-title-display').textContent = selectedTitle;

    const roleLabels = {
        hook: 'Hook', problem: '문제', insight: '핵심',
        solution1: '해결 1', solution2: '해결 2', cta: 'CTA',
        line1: '1', line2: '2', line3: '3', line4: '4',
    };

    const isPromoComment = (
        document.getElementById('category')?.value === 'cosmetics' &&
        document.getElementById('content-type')?.value === 'promo_comment'
    );

    const container = document.getElementById('narration-lines');
    container.innerHTML = data.lines.map((line, i) => {
        const charCount = line.text.replace(/[?,!.~…]/g, '').length;
        const overClass = charCount > 28 ? 'over' : '';
        const charBadge = isPromoComment
            ? ''
            : `<span class="char-count ${overClass}">${charCount}/28</span>`;
        return `
        <div class="narration-line">
            <div class="line-header">
                <span class="line-num">${i + 1}</span>
                <span class="narration-role">${roleLabels[line.role] || line.role}</span>
                ${charBadge}
            </div>
            <input type="text" class="line-text" value="${escapeHtml(line.text)}"
                   data-index="${i}" oninput="updateCharCount(this)">
        </div>
    `}).join('');
}

function updateCharCount(input) {
    const count = input.value.replace(/[?,!.~…]/g, '').length;
    const counter = input.parentElement.querySelector('.char-count');
    if (!counter) return;
    counter.textContent = `${count}/28`;
    counter.classList.toggle('over', count > 28);
}

function regenerateNarration() {
    generateNarration();
}

// ──────────────────────────────────
// Step 4: 나레이션 확정 → 이미지 스타일 표시
// ──────────────────────────────────
async function approveNarration() {
    // 편집된 나레이션 텍스트 수집 & 검증
    const textInputs = document.querySelectorAll('#narration-lines .line-text');
    const narrationLines = Array.from(textInputs).map(input => input.value.trim());

    if (narrationLines.length === 0) {
        alert('먼저 나레이션을 생성해주세요');
        return;
    }

    if (narrationLines.some(line => !line)) {
        alert('빈 나레이션 줄이 있습니다');
        return;
    }

    window._approvedNarrationLines = narrationLines;

    // promo_comment는 이미지 프롬프트 생성을 BGM 단계의 "이미지 생성 시작" 시점으로 연기한다.
    // (음성 단계에서 6초 초과 줄이 분리될 수 있어, 분리 반영된 텍스트로
    //  프롬프트를 만들어야 이미지 컷 수와 영상 클립 수가 일치한다.)
    const category = document.getElementById('category').value;
    const contentType = category === 'cosmetics'
        ? document.getElementById('content-type').value
        : null;
    if (contentType === 'promo_comment') {
        scriptData = null;  // 최종확인 단계에서 expanded 기준으로 재생성
        advanceToStep(3);
        updateVoiceOptions();
        return;
    }

    advanceToStep(3); // 음성 단계로 먼저 이동
    showLoading('이미지 프롬프트 생성 중...');

    try {
        const resp = await authFetch('/api/generate/image-prompts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                narration_lines: narrationLines,
                style: 'realistic',
                topic: document.getElementById('topic').value.trim(),
                ...getCategoryPayload(),
            }),
        });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '이미지 프롬프트 생성 실패');
        }
        scriptData = await resp.json();
    } catch (e) {
        hideLoading();
        showFriendlyError(e.message);
        goToStep(2); // 실패 시 나레이션으로 복귀
        return;
    }
    hideLoading();
    updateVoiceOptions();
}

// ──────────────────────────────────
// Step 4: 음성 설정
// ──────────────────────────────────

// ── TTS 음성 옵션 ──
function updateVoiceOptions() {
    const engine = document.getElementById('tts-engine').value;
    const voiceSelect = document.getElementById('tts-voice');
    const options = VOICE_OPTIONS[engine] || [];
    voiceSelect.innerHTML = options.map(opt =>
        `<option value="${opt.value}">${opt.label}</option>`
    ).join('');

    const emotionSection = document.getElementById('emotion-section');
    if (engine === 'typecast') {
        emotionSection.classList.remove('hidden');
        loadEmotions(voiceSelect.value);
    } else {
        emotionSection.classList.add('hidden');
        document.getElementById('tts-emotion').value = 'normal';
    }
}

async function loadEmotions(voiceId) {
    const container = document.getElementById('emotion-buttons');
    const hiddenInput = document.getElementById('tts-emotion');
    hiddenInput.value = 'normal';

    if (!voiceId) {
        container.innerHTML = '<p class="text-dim">성우를 선택하세요.</p>';
        return;
    }

    container.innerHTML = '<p class="text-dim">감정 목록 로딩 중...</p>';

    try {
        const resp = await authFetch(`/api/tts/emotions?voice_id=${encodeURIComponent(voiceId)}`);
        if (!resp.ok) throw new Error('조회 실패');
        const emotions = await resp.json();

        container.innerHTML = emotions.map(e =>
            `<button type="button" class="emotion-btn${e.value === 'normal' ? ' active' : ''}" data-emotion="${e.value}">${e.label}</button>`
        ).join('');

        container.querySelectorAll('.emotion-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                container.querySelectorAll('.emotion-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                hiddenInput.value = btn.dataset.emotion;
            });
        });
    } catch (e) {
        container.innerHTML = '<p class="text-dim">감정 목록을 불러올 수 없습니다.</p>';
    }
}

// ── TTS 이벤트 리스너 ──
document.getElementById('tts-engine').addEventListener('change', updateVoiceOptions);
document.getElementById('tts-voice').addEventListener('change', function() {
    if (document.getElementById('tts-engine').value === 'typecast') {
        loadEmotions(this.value);
    }
});
document.getElementById('tts-speed').addEventListener('input', function() {
    document.getElementById('speed-val').textContent = this.value;
});

// ── 음성 미리듣기 ──
document.getElementById('voice-preview-btn').addEventListener('click', async function() {
    const btn = this;
    const engine = document.getElementById('tts-engine').value;
    const voiceId = document.getElementById('tts-voice').value;

    if (!voiceId) return;

    if (previewAudio && !previewAudio.paused) {
        previewAudio.pause();
        previewAudio = null;
        btn.textContent = '▶';
        return;
    }

    btn.textContent = '⏳';
    btn.disabled = true;

    try {
        const speed = document.getElementById('tts-speed').value;
        const emotion = engine === 'typecast' ? document.getElementById('tts-emotion').value : 'normal';
        const url = `/api/tts/preview?engine=${encodeURIComponent(engine)}&voice_id=${encodeURIComponent(voiceId)}&speed=${speed}&emotion=${encodeURIComponent(emotion)}`;
        const resp = await authFetch(url);
        if (!resp.ok) throw new Error(`미리듣기 실패: ${resp.status}`);

        const blob = await resp.blob();
        const audioUrl = URL.createObjectURL(blob);
        previewAudio = new Audio(audioUrl);
        previewAudio.onended = () => { btn.textContent = '▶'; };
        previewAudio.play();
        btn.textContent = '⏹';
    } catch (e) {
        console.error(e);
        alert('미리듣기 생성에 실패했습니다.');
        btn.textContent = '▶';
    } finally {
        btn.disabled = false;
    }
});

// ──────────────────────────────────
// Step 5: BGM 설정
// ──────────────────────────────────
async function confirmTtsSettings() {
    // 음성 설정 화면에서 "나레이션 음성 만들기" 버튼 클릭 시.
    // promo_comment(화장품 홍보·고정댓글 유도형)만 현재 단계에서 TTS를 미리 생성.
    // 그 외 타입은 기존처럼 영상 조립 시 한꺼번에 TTS 생성.
    const category = document.getElementById('category').value;
    const contentType = category === 'cosmetics'
        ? document.getElementById('content-type').value
        : null;
    const isPromoComment = contentType === 'promo_comment';

    if (isPromoComment) {
        const narrationLines = window._approvedNarrationLines;
        if (!narrationLines || narrationLines.length === 0) {
            alert('먼저 나레이션을 확정해주세요');
            return;
        }

        const voiceId = document.getElementById('tts-voice').value;
        const speed = parseFloat(document.getElementById('tts-speed').value);
        const emotion = document.getElementById('tts-emotion').value;

        // 로딩 UX: 단계별 메시지 (Typecast 병렬 처리라 대체로 5~10초)
        showLoading('음성 생성 중... (1/2)');
        const loadingTimer = setTimeout(() => {
            showLoading('나레이션 길이 확인 중... (2/2)');
        }, 3000);

        try {
            const resp = await authFetch('/api/tts/preview-build', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    sentences: narrationLines,
                    voice_id: voiceId,
                    speed: speed,
                    emotion: emotion,
                    content_type: contentType,
                    topic: document.getElementById('topic').value.trim(),
                    style: 'realistic',
                }),
            });
            clearTimeout(loadingTimer);
            if (!resp.ok) {
                const err = await resp.json();
                throw new Error(err.detail || 'TTS 생성 실패');
            }
            const data = await resp.json();
            window._ttsSessionId = data.session_id;
            // 분리 결과 보관 — "이미지 생성 시작" 시점에 이미지 프롬프트 생성 시 사용
            window._expandedSentences = data.expanded_sentences || narrationLines;
        } catch (e) {
            clearTimeout(loadingTimer);
            hideLoading();
            showFriendlyError(e.message);
            return;
        }
        hideLoading();
    }

    // 모드별 BGM 단계 인덱스가 다르므로 ID 기반으로 이동
    advanceToStepById('step-bgm');
}

// ── BGM 이벤트 리스너 ──
document.getElementById('bgm-volume').addEventListener('input', function() {
    document.getElementById('bgm-val').textContent = this.value;
});
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
// 생성 직전 가드: 타임라인 점프·콘솔 호출 등 우회 경로 방어
// ──────────────────────────────────
function validateBeforeCreate() {
    // 카드 B는 별도 검증 (대본 자산 + 음성)
    if (window._generationMode === 'user_assets') {
        if (!window._draftJobId) {
            alert('대본 쪼개기를 먼저 진행해주세요.');
            goToStep(stepIndexOf('step-user-script'));
            return false;
        }
        const lines = window._splitLines || [];
        const statuses = window._userLineStatuses || [];
        const missingIdx = lines.findIndex((_, i) => statuses[i] !== 'ready');
        if (missingIdx >= 0) {
            alert(`${missingIdx + 1}번째 줄의 자산이 준비되지 않았습니다.`);
            goToStep(stepIndexOf('step-user-lines'));
            return false;
        }
        const voiceSel = document.getElementById('tts-voice');
        if (!voiceSel || !voiceSel.value) {
            alert('음성을 선택해주세요.');
            goToStep(stepIndexOf('step-tts'));
            return false;
        }
        return true;
    }

    const categoryVal = document.getElementById('category').value;
    const contentTypeVal = categoryVal === 'cosmetics'
        ? document.getElementById('content-type').value
        : null;
    const isPromoComment = contentTypeVal === 'promo_comment';
    const narrationApproved = isPromoComment
        ? !!(window._approvedNarrationLines && window._approvedNarrationLines.length > 0)
        : !!scriptData;
    const ttsSessionReady = !isPromoComment || !!window._ttsSessionId;
    const voiceSel = document.getElementById('tts-voice');
    const voiceReady = !!(voiceSel && voiceSel.value);

    const missing = [];
    if (!document.getElementById('topic').value.trim()) missing.push({ id: 'step-input', name: '주제 입력' });
    if (!selectedTitle) missing.push({ id: 'step-titles', name: '제목 선택' });
    if (!narrationApproved) missing.push({ id: 'step-narration', name: '나레이션 확정' });
    if (!voiceReady) missing.push({ id: 'step-tts', name: '나레이션 음성 선택' });
    if (!ttsSessionReady) missing.push({ id: 'step-tts', name: '나레이션 음성 만들기' });

    if (missing.length > 0) {
        alert(`다음 단계를 먼저 진행해주세요: ${missing[0].name}`);
        const idx = stepIndexOf(missing[0].id);
        if (idx >= 0) goToStep(idx);
        return false;
    }
    return true;
}

// BGM 단계 버튼 wrapper — BGM 미선택 시 확인 후 createJob 호출
async function startCreateFromBgm() {
    if (isCreatingJob) return;
    if (!validateBeforeCreate()) return;
    if (!selectedBgm) {
        const ok = confirm('BGM을 선택하지 않았습니다.\nBGM 없이 진행하시겠어요?');
        if (!ok) return;
    }
    if (window._generationMode === 'user_assets') {
        await confirmDraftJob();
    } else {
        await createJob();
    }
}

// 카드 B: 이미 만들어둔 draft Job에 음성/BGM 정보를 채워 /confirm 호출.
async function confirmDraftJob() {
    if (isCreatingJob) return;
    isCreatingJob = true;
    try {
        const jobId = window._draftJobId;
        if (!jobId) {
            alert('대본 쪼개기를 먼저 진행해주세요.');
            return;
        }

        const payload = {
            video_mode: 'kenburns',
            tts_engine: document.getElementById('tts-engine').value,
            tts_speed: parseFloat(document.getElementById('tts-speed').value),
            voice_id: document.getElementById('tts-voice').value,
            emotion: document.getElementById('tts-engine').value === 'typecast' ? document.getElementById('tts-emotion').value : null,
            bgm_filename: selectedBgm || null,
            bgm_start_sec: parseFloat(document.getElementById('bgm-start-sec').value) || 0,
            bgm_volume: parseInt(document.getElementById('bgm-volume').value) / 100,
        };

        showLoading('작업 시작 중...');
        try {
            const resp = await authFetch(`/api/jobs/${jobId}/confirm`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });
            if (!resp.ok) {
                const err = await resp.json();
                throw new Error(err.detail || '영상 제작 시작 실패');
            }
            window.location.href = `/static/status.html?job=${jobId}&phase=render`;
        } catch (e) {
            showFriendlyError(e.message);
            hideLoading();
        }
    } finally {
        isCreatingJob = false;
    }
}

// ──────────────────────────────────
// Job 생성
// ──────────────────────────────────
async function createJob() {
    if (isCreatingJob) return;
    if (!validateBeforeCreate()) return;
    isCreatingJob = true;
    try {
        // 홍보성 영상만 제품 이미지 필수. 정보성은 product_image_id를 null로 강제해
        // 이전에 선택한 제품이 CTA에 새는 것을 방지한다.
        const category = document.getElementById('category').value;
        const contentType = category === 'cosmetics'
            ? document.getElementById('content-type').value
            : null;
        if (category === 'cosmetics' && contentType === 'promo' && !window._selectedProductId) {
            alert('홍보성 영상은 제품 이미지를 먼저 등록하고 선택해주세요.\n(주제 설정 단계에서 등록)');
            return;
        }
        const productImageId = (contentType === 'info')
            ? null
            : (window._selectedProductId || null);

        // promo_comment 분기: 이미지 프롬프트를 BGM 단계 완료 시점에 생성.
        // 음성 단계에서 분리된 expanded_sentences 기준으로 호출해야 이미지 컷 수가 일치.
        if (contentType === 'promo_comment') {
            const narrationForPrompts = window._expandedSentences || window._approvedNarrationLines;
            if (!narrationForPrompts || narrationForPrompts.length === 0) {
                alert('나레이션이 준비되지 않았습니다. 앞 단계부터 다시 진행해주세요.');
                return;
            }
            showLoading('이미지 프롬프트 생성 중...');
            try {
                const promptResp = await authFetch('/api/generate/image-prompts', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        narration_lines: narrationForPrompts,
                        style: 'realistic',
                        topic: document.getElementById('topic').value.trim(),
                        ...getCategoryPayload(),
                    }),
                });
                if (!promptResp.ok) {
                    const err = await promptResp.json();
                    throw new Error(err.detail || '이미지 프롬프트 생성 실패');
                }
                scriptData = await promptResp.json();
            } catch (e) {
                hideLoading();
                showFriendlyError(e.message);
                return;
            }
        }

        if (!scriptData) return;

        const payload = {
            topic: document.getElementById('topic').value,
            style: 'realistic',
            video_mode: "kenburns",
            tts_engine: document.getElementById('tts-engine').value,
            tts_speed: parseFloat(document.getElementById('tts-speed').value),
            voice_id: document.getElementById('tts-voice').value,
            emotion: document.getElementById('tts-engine').value === 'typecast' ? document.getElementById('tts-emotion').value : null,
            title: selectedTitle,
            title_line1: titleLine1,
            title_line2: titleLine2,
            lines: scriptData.lines,
            bgm_volume: parseInt(document.getElementById('bgm-volume').value) / 100,
            bgm_filename: selectedBgm || null,
            bgm_start_sec: parseFloat(document.getElementById('bgm-start-sec').value) || 0,
            product_image_id: productImageId,
            tts_session_id: window._ttsSessionId || null,
        };

        showLoading('작업 등록 중...');

        try {
            const resp = await authFetch('/api/jobs/', {
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
            showFriendlyError(e.message);
            hideLoading();
        }
    } finally {
        isCreatingJob = false;
    }
}

// ──────────────────────────────────
// 스텝 관리 (타임라인 + 접기/펼치기)
// ──────────────────────────────────
let maxReachedStep = 0;

function showStepGuide(stepId, message) {
    const body = document.querySelector(`#${stepId} .step-body`);
    if (!body) return;
    let guide = body.querySelector('.step-guide-msg');
    if (!guide) {
        guide = document.createElement('div');
        guide.className = 'step-guide-msg';
        const h2 = body.querySelector('h2');
        if (h2) h2.after(guide);
        else body.prepend(guide);
    }
    guide.innerHTML = message;
    guide.classList.remove('hidden');
}

function hideStepGuide(stepId) {
    const guide = document.querySelector(`#${stepId} .step-guide-msg`);
    if (guide) guide.classList.add('hidden');
}

function goToStep(stepIndex) {
    if (stepIndex < 0 || stepIndex >= STEPS.length) {
        console.warn(`goToStep: 범위 밖 인덱스 ${stepIndex} (유효: 0~${STEPS.length - 1})`);
        return;
    }
    currentStepIndex = stepIndex;
    const stepId = STEPS[stepIndex].id;

    // 모드 선택 화면은 STEPS 외부의 진입 화면이라 항상 숨김
    const modeSel = document.getElementById('step-mode-select');
    if (modeSel) modeSel.classList.add('hidden');

    // STEPS에 속하지 않는 모든 step-section은 숨김, 활성 STEP만 표시
    document.querySelectorAll('.step-section').forEach(el => {
        if (el.id === stepId) {
            el.classList.remove('hidden', 'collapsed');
        } else {
            el.classList.add('hidden');
        }
    });

    // step.id 기반 안내/초기화
    if (stepId === 'step-titles') {
        if (!titleOptions) showStepGuide('step-titles', '주제를 입력하고 "제목 생성하기"를 눌러주세요.');
        else hideStepGuide('step-titles');
    }
    if (stepId === 'step-narration') {
        if (!narrationData) showStepGuide('step-narration', '제목 단계에서 "다음: 나레이션 생성"을 눌러주세요.');
        else hideStepGuide('step-narration');
    }
    if (stepId === 'step-tts') {
        const voiceSelect = document.getElementById('tts-voice');
        if (voiceSelect && voiceSelect.options.length === 0) {
            updateVoiceOptions();
        }
    }
    if (stepId === 'step-bgm' && bgmList.length === 0) {
        loadBgmList();
    }
    if (stepId === 'step-user-lines') {
        renderUserLines();
    }

    updateTimeline();

    setTimeout(() => {
        const current = document.getElementById(stepId);
        if (current) current.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 100);
}

// 진행도(maxReachedStep)는 단조 증가만 가능. 자유 이동으로 이전 단계 재진입 시에도 진행도는 보존된다.
function advanceToStep(stepIndex) {
    maxReachedStep = Math.max(maxReachedStep, stepIndex);
    goToStep(stepIndex);
}

function stepIndexOf(id) {
    return STEPS.findIndex(s => s.id === id);
}

function advanceToStepById(id) {
    const idx = stepIndexOf(id);
    if (idx >= 0) advanceToStep(idx);
}

function clickTimelineStep(stepIndex) {
    goToStep(stepIndex);
}

function renderTimelineTrack() {
    const track = document.getElementById('timeline-track');
    if (!track) return;
    track.innerHTML = STEPS.map((step, i) => `
        <li class="timeline-item${i === 0 ? ' active' : ''}" data-step="${i}" onclick="clickTimelineStep(${i})">
            <span class="timeline-dot"></span>
            <span class="timeline-label">${step.label}</span>
        </li>
    `).join('');
}

function updateTimeline() {
    const items = document.querySelectorAll('.timeline-item');
    items.forEach((item, i) => {
        item.classList.remove('completed', 'active');
        if (i < maxReachedStep) item.classList.add('completed');
        if (i === currentStepIndex) item.classList.add('active');
    });

    const track = document.querySelector('.timeline-track');
    if (!track) return;
    const progress = maxReachedStep === 0 ? 0 : (maxReachedStep / Math.max(1, STEPS.length - 1)) * 100;
    track.style.setProperty('--timeline-progress', progress + '%');
}

// ──────────────────────────────────
// 스타일 카드 선택
// ──────────────────────────────────


// ──────────────────────────────────
// 유틸리티
// ──────────────────────────────────
function hideAllSteps() {
    STEPS.forEach(step => {
        const el = document.getElementById(step.id);
        if (el) { el.classList.add('hidden'); el.classList.remove('collapsed'); }
    });
}

function showLoading(text) {
    const stepEl = document.getElementById(STEPS[currentStepIndex].id);
    let overlay = stepEl.querySelector('.step-loading-overlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.className = 'step-loading-overlay';
        overlay.innerHTML = '<div class="spinner"></div><p class="loading-text"></p>';
        stepEl.appendChild(overlay);
    }
    overlay.querySelector('.loading-text').textContent = text;
    overlay.classList.remove('hidden');
}

function hideLoading() {
    document.querySelectorAll('.step-loading-overlay').forEach(el => el.classList.add('hidden'));
}

function motionLabel(motion) {
    const labels = {
        zoom_in: 'Zoom In', zoom_out: 'Zoom Out',
        pan_left: 'Pan Left', pan_right: 'Pan Right',
        pan_up: 'Pan Up', pan_down: 'Pan Down',
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
        const resp = await authFetch('/api/assets/bgm');
        if (!resp.ok) return;
        bgmList = await resp.json();
        renderBgmList();
    } catch (e) {
        console.error('BGM 목록 로드 실패:', e);
        document.getElementById('bgm-list').innerHTML =
            '<p class="text-dim">BGM 목록을 불러올 수 없습니다</p>';
    }
}

let _bgmPlaying = false;

function renderBgmList() {
    const container = document.getElementById('bgm-list');
    if (bgmList.length === 0) {
        container.innerHTML = '<p class="text-dim">BGM을 업로드하세요</p>';
        return;
    }
    const isSelected = (bgm) => selectedBgm === bgm.filename;
    container.innerHTML = bgmList.map((bgm, i) => `
        <div class="bgm-card ${isSelected(bgm) ? 'selected' : ''}"
             onclick="selectBgm(${i})">
            <div class="bgm-name">${escapeHtml(bgm.filename.replace(/\.(mp3|wav|ogg)$/i, ''))}</div>
            <div class="bgm-duration">${formatTime(bgm.duration)}</div>
            <button class="btn-small bgm-delete-btn" onclick="event.stopPropagation(); deleteBgm('${bgm.id || bgm.filename}', ${i})">삭제</button>
        </div>
        ${isSelected(bgm) ? renderBgmPlayer(i) : ''}
    `).join('');
}

function handleBgmUploadClick() {
    if (bgmList.length >= 3) {
        alert('BGM은 최대 3개까지 업로드 가능합니다.\n기존 BGM을 삭제 후 다시 시도해주세요.');
        return;
    }
    document.getElementById('bgm-upload-input').click();
}

function renderBgmPlayer(index) {
    const bgm = bgmList[index];
    const cur = bgmAudio ? bgmAudio.currentTime || 0 : 0;
    const dur = bgm.duration || 0;
    return `
        <div class="bgm-player" id="bgm-player">
            <button class="bgm-player-btn" onclick="event.stopPropagation(); toggleBgmPlayPause()">
                ${_bgmPlaying ? '⏸' : '▶'}
            </button>
            <span class="bgm-player-time" id="bgm-player-time">${formatTime(cur)}</span>
            <input type="range" class="bgm-player-bar" id="bgm-player-bar"
                   min="0" max="${Math.floor(dur)}" value="${Math.floor(cur)}" step="1"
                   onclick="event.stopPropagation()"
                   oninput="seekBgm(this.value)">
            <span class="bgm-player-duration">${formatTime(dur)}</span>
        </div>
    `;
}

// ── BGM 업로드 ──
document.getElementById('bgm-upload-input')?.addEventListener('change', async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    const btn = document.getElementById('bgm-upload-btn');
    btn.textContent = '업로드 중...';
    btn.disabled = true;

    try {
        const formData = new FormData();
        formData.append('file', file);
        const resp = await authFetch('/api/assets/bgm', { method: 'POST', body: formData });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '업로드 실패');
        }
        await loadBgmList();
    } catch (err) {
        alert(err.message);
    } finally {
        btn.textContent = '+ BGM 업로드';
        btn.disabled = false;
        e.target.value = '';
    }
});

async function deleteBgm(bgmId, index) {
    if (!confirm('이 BGM을 삭제하시겠습니까?')) return;
    try {
        const resp = await authFetch(`/api/assets/bgm/${bgmId}`, { method: 'DELETE' });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '삭제 실패');
        }
        if (selectedBgm === bgmList[index]?.filename) {
            selectedBgm = null;
            bgmDuration = 0;
            if (bgmAudio) { bgmAudio.pause(); bgmAudio = null; _bgmPlaying = false; }
        }
        await loadBgmList();
    } catch (err) {
        alert(err.message);
    }
}

function selectBgm(index) {
    // 기존 재생 중지
    if (bgmAudio) {
        bgmAudio.pause();
        bgmAudio = null;
        _bgmPlaying = false;
    }

    selectedBgm = bgmList[index].filename;
    bgmDuration = bgmList[index].duration;

    // Audio 객체 생성 (preload 안 함, 재생바만 표시)
    const bgm = bgmList[index];
    bgmAudio = new Audio();
    bgmAudio.preload = 'none';
    bgmAudio.src = bgm.url;
    bgmAudio._bgmIdx = index;

    bgmAudio.addEventListener('timeupdate', () => {
        const bar = document.getElementById('bgm-player-bar');
        const timeEl = document.getElementById('bgm-player-time');
        if (bar && bgmAudio) {
            bar.value = Math.floor(bgmAudio.currentTime);
            timeEl.textContent = formatTime(bgmAudio.currentTime);
        }
    });

    bgmAudio.addEventListener('ended', () => {
        _bgmPlaying = false;
        const btn = document.querySelector('.bgm-player-btn');
        if (btn) btn.textContent = '▶';
    });

    renderBgmList();

    const startSection = document.getElementById('bgm-start-section');
    startSection.classList.remove('hidden');
    const slider = document.getElementById('bgm-start');
    slider.max = bgmDuration;
    slider.value = 0;
    document.getElementById('bgm-start-val').textContent = '0:00';
    document.getElementById('bgm-start-sec').value = '0.0';
    document.getElementById('bgm-start-sec').max = bgmDuration;
}

function toggleBgmPlayPause() {
    if (!bgmAudio) return;
    if (_bgmPlaying) {
        bgmAudio.pause();
        _bgmPlaying = false;
    } else {
        bgmAudio.play();
        _bgmPlaying = true;
    }
    const btn = document.querySelector('.bgm-player-btn');
    if (btn) btn.textContent = _bgmPlaying ? '⏸' : '▶';
}

function seekBgm(val) {
    if (bgmAudio) {
        bgmAudio.currentTime = parseFloat(val);
        const timeEl = document.getElementById('bgm-player-time');
        if (timeEl) timeEl.textContent = formatTime(val);
    }
}

function formatTime(sec) {
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
}

// ──────────────────────────────────
// 카드 A / 카드 B 모드 선택
// ──────────────────────────────────
function selectGenerationMode(mode) {
    window._generationMode = mode;
    if (mode === 'ai_full') {
        STEPS = STEPS_AI_FULL;
    } else if (mode === 'user_assets') {
        STEPS = STEPS_USER_ASSETS;
    } else {
        return;
    }
    maxReachedStep = 0;
    currentStepIndex = 0;
    renderTimelineTrack();
    // 모드 선택 후 타임라인 노출
    const tl = document.getElementById('workflow-timeline');
    if (tl) tl.classList.remove('hidden');
    goToStep(0);
    // 카드 B는 카테고리/promo_comment 필드를 보호 차원에서 숨김
    if (mode === 'user_assets') {
        document.getElementById('category').value = 'general';
        try { toggleCategoryFields(); } catch (e) {}
    }
}

function backToModeSelect() {
    // 카드 B의 대본 입력 화면에서 "이전" — 모드 선택으로 돌아간다.
    if (window._draftJobId) {
        // draft job은 서버에 남지만 사용자가 다른 모드로 갈 수도 있으므로 클라 상태만 정리
        window._draftJobId = null;
        window._splitLines = [];
    }
    document.querySelectorAll('.step-section').forEach(el => el.classList.add('hidden'));
    const modeSel = document.getElementById('step-mode-select');
    if (modeSel) modeSel.classList.remove('hidden');
    const trackEl = document.getElementById('timeline-track');
    if (trackEl) trackEl.innerHTML = '';
    const tl = document.getElementById('workflow-timeline');
    if (tl) tl.classList.add('hidden');
    window._generationMode = null;
}

function backToUserScript() {
    goToStep(0);  // STEPS_USER_ASSETS의 0번 = step-user-script
}

// ──────────────────────────────────
// 카드 B: 대본 입력 → 쪼개기
// ──────────────────────────────────
function updateUserScriptCount() {
    const ta = document.getElementById('user-script');
    const count = ta.value.length;
    document.getElementById('user-script-count').textContent = count;
    const btn = document.getElementById('btn-split-script');
    if (btn) btn.disabled = count < 10;
}

async function splitUserScript() {
    const ta = document.getElementById('user-script');
    const script = ta.value.trim();
    if (script.length < 10) {
        alert('대본은 10자 이상 입력해주세요.');
        return;
    }

    window._userScript = script;
    showLoading('대본을 문장 단위로 분리하는 중...');

    try {
        const resp = await authFetch('/api/generate/split-script', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ script }),
        });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '대본 쪼개기 실패');
        }
        const data = await resp.json();
        window._splitLines = data.lines;

        // draft Job 생성 (줄별 자산 편집을 위한 job_id 확보)
        const draftResp = await authFetch('/api/jobs/draft', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ lines: data.lines }),
        });
        if (!draftResp.ok) {
            const err = await draftResp.json();
            throw new Error(err.detail || 'Draft Job 생성 실패');
        }
        const draft = await draftResp.json();
        window._draftJobId = draft.job_id;
        // 모드 B 줄별 상태 (클라 캐시) — 'pending'|'ready'|'failed'
        window._userLineStatuses = data.lines.map(() => 'pending');
        // 줄별 자산 출처 (클라 캐시) — 'ai'|'image'|'clip'
        window._userLineSources = data.lines.map(() => 'ai');

        hideLoading();
        // step-user-script 다음이 step-user-lines (인덱스 1)
        advanceToStep(1);
    } catch (e) {
        hideLoading();
        showFriendlyError(e.message);
    }
}

// ──────────────────────────────────
// 카드 B: 줄별 자산 편집
// ──────────────────────────────────
function renderUserLines() {
    const container = document.getElementById('user-lines-list');
    if (!container) return;
    const lines = window._splitLines || [];
    const sources = window._userLineSources || [];
    const statuses = window._userLineStatuses || [];
    const jobId = window._draftJobId;

    container.innerHTML = lines.map((text, i) => {
        const source = sources[i] || 'ai';
        const status = statuses[i] || 'pending';
        const failed = status === 'failed';
        const ts = Date.now();  // 캐시 버스트
        let slot = '';
        if (source === 'clip' && status === 'ready') {
            slot = `<video src="/api/jobs/${jobId}/clips/${i}?t=${ts}" autoplay muted loop playsinline></video>`;
        } else if ((source === 'image' || source === 'ai') && status === 'ready') {
            slot = `<img src="/api/jobs/${jobId}/images/${i}?t=${ts}" alt="">`;
        } else if (status === 'pending') {
            slot = `<span class="user-line-slot-empty">생성/업로드 대기</span>`;
        } else {
            slot = `<span class="user-line-slot-empty">실패</span>`;
        }
        const statusBadge = status === 'pending' ? '<div class="user-line-slot-status">대기</div>' : '';
        return `
            <div class="user-line-item ${failed ? 'failed' : ''}" data-line-index="${i}">
                <div class="user-line-num">${i + 1}</div>
                <div class="user-line-text"
                     contenteditable="true"
                     spellcheck="false"
                     title="클릭해서 직접 수정 (Enter 저장, Esc 취소)"
                     onblur="saveUserLineEdit(${i}, this)"
                     onkeydown="handleUserLineKey(event, ${i}, this)">${escapeHtml(text)}</div>
                <div class="user-line-slot">${slot}${statusBadge}</div>
                <div class="user-line-buttons">
                    <button class="btn-secondary" onclick="userLineGenerateAI(${i})">🪄 AI 이미지 생성</button>
                    <button class="btn-secondary" onclick="userLineUploadImage(${i})">🖼 이미지 업로드</button>
                    <button class="btn-secondary" onclick="userLineUploadClip(${i})">🎬 영상 업로드</button>
                </div>
                ${failed ? `<div class="fail-reason">실패: 다시 시도하거나 직접 업로드해주세요.</div>` : ''}
            </div>
        `;
    }).join('');

    // 상태 요약
    const total = lines.length;
    const ready = statuses.filter(s => s === 'ready').length;
    const summary = document.getElementById('user-lines-status');
    if (summary) summary.textContent = `${ready}/${total}줄 준비됨`;
}

// 줄 텍스트를 클릭 인라인 편집: blur 시 저장, Enter/Esc 키 처리
function saveUserLineEdit(i, el) {
    if (!window._splitLines || i < 0 || i >= window._splitLines.length) return;
    const next = (el.innerText || '').trim();
    if (!next) {
        // 빈 줄 방지 → 원본 복구
        el.innerText = window._splitLines[i];
        return;
    }
    if (next === window._splitLines[i]) return;
    window._splitLines[i] = next;
}

function handleUserLineKey(ev, i, el) {
    if (ev.key === 'Enter' && !ev.shiftKey) {
        ev.preventDefault();
        el.blur();
    } else if (ev.key === 'Escape') {
        ev.preventDefault();
        el.innerText = window._splitLines[i];
        el.blur();
    }
}

async function userLineGenerateAI(i) {
    if (!window._draftJobId) return;
    window._userLineStatuses[i] = 'pending';
    window._userLineSources[i] = 'ai';
    renderUserLines();
    try {
        const resp = await authFetch(`/api/jobs/${window._draftJobId}/regenerate-image/${i}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}),
        });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || 'AI 이미지 생성 실패');
        }
        // 서버 SSE 또는 폴링 대신, 간단히 polling으로 줄별 status 확인
        pollUserLineStatus(i);
    } catch (e) {
        window._userLineStatuses[i] = 'failed';
        renderUserLines();
        showFriendlyError(e.message);
    }
}

function userLineUploadImage(i) {
    if (!window._draftJobId) return;
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'image/png,image/jpeg,image/webp';
    input.onchange = async (e) => {
        const file = e.target.files[0];
        if (!file) return;
        const formData = new FormData();
        formData.append('file', file);
        window._userLineStatuses[i] = 'pending';
        renderUserLines();
        try {
            const resp = await authFetch(`/api/jobs/${window._draftJobId}/upload-image/${i}`, {
                method: 'POST',
                body: formData,
            });
            if (!resp.ok) {
                const err = await resp.json();
                throw new Error(err.detail || '이미지 업로드 실패');
            }
            window._userLineStatuses[i] = 'ready';
            window._userLineSources[i] = 'image';
            renderUserLines();
        } catch (err) {
            window._userLineStatuses[i] = 'failed';
            renderUserLines();
            alert('업로드 실패: ' + err.message);
        }
    };
    input.click();
}

function userLineUploadClip(i) {
    if (!window._draftJobId) return;
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'video/mp4,video/quicktime,video/webm,video/x-msvideo';
    input.onchange = async (e) => {
        const file = e.target.files[0];
        if (!file) return;
        const formData = new FormData();
        formData.append('file', file);
        window._userLineStatuses[i] = 'pending';
        renderUserLines();
        try {
            const resp = await authFetch(`/api/jobs/${window._draftJobId}/upload-clip/${i}`, {
                method: 'POST',
                body: formData,
            });
            if (!resp.ok) {
                const err = await resp.json();
                throw new Error(err.detail || '영상 업로드 실패');
            }
            window._userLineStatuses[i] = 'ready';
            window._userLineSources[i] = 'clip';
            renderUserLines();
        } catch (err) {
            window._userLineStatuses[i] = 'failed';
            renderUserLines();
            alert('업로드 실패: ' + err.message);
        }
    };
    input.click();
}

async function batchGenerateMissingImages() {
    if (!window._draftJobId) return;
    const missing = window._userLineSources.map((s, i) => (s === 'ai' && window._userLineStatuses[i] !== 'ready') ? i : -1).filter(i => i >= 0);
    if (missing.length === 0) {
        alert('비어 있는 줄이 없습니다.');
        return;
    }
    try {
        const resp = await authFetch(`/api/jobs/${window._draftJobId}/generate-missing-images`, {
            method: 'POST',
        });
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.detail || '일괄 생성 실패');
        }
        const data = await resp.json();
        (data.queued || []).forEach(i => {
            window._userLineStatuses[i] = 'pending';
            pollUserLineStatus(i);
        });
        renderUserLines();
    } catch (e) {
        showFriendlyError(e.message);
    }
}

async function pollUserLineStatus(i) {
    // 이미지 파일 존재로 줄별 상태를 단순 폴링한다.
    if (!window._draftJobId) return;
    const jobId = window._draftJobId;
    const maxTries = 60;  // ~120초
    for (let n = 0; n < maxTries; n++) {
        await new Promise(r => setTimeout(r, 2000));
        // 진행 중 다른 모드로 빠졌으면 중단
        if (window._draftJobId !== jobId) return;
        try {
            const r = await authFetch(`/api/jobs/${jobId}`);
            if (!r.ok) continue;
            const job = await r.json();
            // 상태 자체보다는 이미지 URL HEAD로 확인
            const head = await fetch(`/api/jobs/${jobId}/images/${i}?t=${Date.now()}`, { method: 'HEAD' });
            if (head.ok) {
                window._userLineStatuses[i] = 'ready';
                window._userLineSources[i] = 'ai';
                renderUserLines();
                return;
            }
        } catch (e) { /* 폴링 실패는 무시 */ }
    }
    // 타임아웃: 한 줄 실패로 처리
    window._userLineStatuses[i] = 'failed';
    renderUserLines();
}

async function proceedToTtsFromUserLines() {
    // 모든 줄이 준비됐는지 검증
    const lines = window._splitLines || [];
    const statuses = window._userLineStatuses || [];
    const missing = lines.map((_, i) => statuses[i] !== 'ready' ? i : -1).filter(i => i >= 0);
    if (missing.length > 0) {
        alert(`${missing.length}개 줄의 자산이 아직 준비되지 않았습니다. (예: ${missing[0] + 1}번)`);
        return;
    }
    // 카드 A의 validateBeforeCreate 통과를 위해 narration 흐름 채움
    window._approvedNarrationLines = lines.slice();
    selectedTitle = '';
    titleLine1 = '';
    titleLine2 = '';
    advanceToStep(2);  // STEPS_USER_ASSETS의 2번 = step-tts
}

// ──────────────────────────────────
// 페이지 로드 초기화
// ──────────────────────────────────
loadBgmList();
loadUserProducts();
toggleCategoryFields();  // 카테고리 + 영상 목적 UI 초기 상태 세팅
// 모드 선택 화면이 진입 화면. STEPS 타임라인은 모드 선택 후 렌더링된다.
