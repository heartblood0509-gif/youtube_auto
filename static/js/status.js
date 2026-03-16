/**
 * 진행 상태 페이지 - SSE로 실시간 업데이트
 */

const params = new URLSearchParams(window.location.search);
const jobId = params.get('job');
const phase = params.get('phase') || 'render';

function connectSSE() {
    if (!jobId) return;

    const source = new EventSource(`/api/jobs/${jobId}/stream`);

    source.onmessage = function(event) {
        const data = JSON.parse(event.data);

        // 에러 확인
        if (data.error && !data.status) {
            source.close();
            return;
        }

        // 진행률 업데이트
        const percent = Math.round(data.progress * 100);
        document.getElementById('progress-fill').style.width = percent + '%';
        document.getElementById('progress-percent').textContent = percent + '%';
        document.getElementById('progress-step').textContent = data.current_step || '';

        // 이미지 생성 완료 → 미리보기 페이지로
        if (data.status === 'preview_ready' && phase === 'images') {
            source.close();
            window.location.href = `/static/preview.html?job=${jobId}`;
            return;
        }

        // 영상 완성
        if (data.status === 'completed') {
            source.close();
            showCompleted(data.video_url);
            return;
        }

        // 실패
        if (data.status === 'failed') {
            source.close();
            showError(data.error || '알 수 없는 에러');
            return;
        }
    };

    source.onerror = function() {
        source.close();
        // 폴링으로 폴백
        startPolling();
    };
}

function startPolling() {
    const interval = setInterval(async () => {
        try {
            const resp = await fetch(`/api/jobs/${jobId}`);
            const data = await resp.json();

            const percent = Math.round(data.progress * 100);
            document.getElementById('progress-fill').style.width = percent + '%';
            document.getElementById('progress-percent').textContent = percent + '%';
            document.getElementById('progress-step').textContent = data.current_step || '';

            if (data.status === 'preview_ready' && phase === 'images') {
                clearInterval(interval);
                window.location.href = `/static/preview.html?job=${jobId}`;
            } else if (data.status === 'completed') {
                clearInterval(interval);
                showCompleted(data.video_url);
            } else if (data.status === 'failed') {
                clearInterval(interval);
                showError(data.error || '알 수 없는 에러');
            }
        } catch (e) {
            // 네트워크 에러 시 계속 재시도
        }
    }, 2000);
}

function showCompleted(videoUrl) {
    document.getElementById('completed-section').classList.remove('hidden');
    if (videoUrl) {
        document.getElementById('download-link').href = videoUrl;
        document.getElementById('video-preview').src = videoUrl;
    }
}

function showError(message) {
    document.getElementById('error-section').classList.remove('hidden');
    document.getElementById('error-message').textContent = message;
}

connectSSE();
