const form = document.getElementById("upload-form");
const recording = document.getElementById("recording");
const uploadButton = document.getElementById("upload-button");
const uploadStatus = document.getElementById("upload-status");
const progress = document.getElementById("upload-progress");
const refreshButton = document.getElementById("refresh");
const queueStatus = document.getElementById("queue-status");
const jobs = document.getElementById("jobs");

const uploadErrors = {
  invalid_audio: "읽을 수 없는 녹음입니다. M4A(AAC) 또는 WAV(PCM) 파일을 확인해 주세요.",
  audio_too_long: "2시간을 초과하는 녹음은 업로드할 수 없습니다.",
  upload_busy: "다른 파일을 접수 중입니다. 잠시 후 다시 시도해 주세요.",
  media_tool_unavailable: "서버에 ffmpeg와 ffprobe가 필요합니다.",
  insufficient_storage: "PC의 여유 공간이 부족합니다.",
};

function upload(file) {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open("POST", "/api/jobs");
    request.responseType = "json";
    request.setRequestHeader("Content-Type", "application/octet-stream");
    request.setRequestHeader("X-Filename", encodeURIComponent(file.name));
    request.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) progress.value = event.loaded / event.total;
    });
    request.upload.addEventListener("load", () => {
      progress.hidden = true;
      uploadStatus.textContent = "녹음 파일을 검사하고 있습니다.";
    });
    request.addEventListener("load", () => {
      if (request.status === 201) {
        resolve();
      } else {
        reject(new Error(uploadErrors[request.response?.detail] || "녹음을 접수하지 못했습니다."));
      }
    });
    request.addEventListener("error", () => {
      reject(new Error("연결이 끊겼습니다. 다시 업로드하기 전에 작업 목록을 확인해 주세요."));
    });
    request.send(file);
  });
}

const jobStates = {
  queued: "대기",
  preparing: "오디오 준비 중",
  transcribing: "전사 중",
  aligning: "단어 시간 정렬 중",
  completed: "처리 완료",
  failed: "처리 실패",
  interrupted: "처리 중단",
};
const stageErrors = {
  external_execution_unknown: "외부 실행 종료 확인 필요",
  inference_unreachable: "InferSwap에 연결할 수 없음",
  inference_rejected: "추론 요청 거절",
  invalid_inference_response: "추론 응답 검증 실패",
  media_tool_unavailable: "ffmpeg 설치 필요",
  audio_preparation_failed: "오디오 준비 실패",
  insufficient_storage: "디스크 여유 공간 부족",
  storage_error: "결과 저장 오류",
};

function renderJob(job, inferenceEnabled, inferenceHeld) {
  const item = document.createElement("li");
  const info = document.createElement("div");
  const name = document.createElement("strong");
  const details = document.createElement("p");
  const seconds = Math.ceil(job.duration_seconds);
  const time = `${Math.floor(seconds / 60)}분 ${seconds % 60}초`;
  name.textContent = job.name;
  const terminal = ["completed", "failed", "interrupted"].includes(job.status);
  const held = job.external_pending && terminal;
  const active = ["preparing", "transcribing", "aligning"].includes(job.status);
  const state = job.run_requested ? "실행 대기" : jobStates[job.status];
  const segment = active && job.progress?.total > 1
    ? ` ${job.progress.part}/${job.progress.total}` : "";
  const issue = held ? stageErrors.external_execution_unknown : stageErrors[job.error?.code];
  details.textContent = [state + segment, time, issue].filter(Boolean).join(" · ");
  info.append(name, details);
  item.append(info);

  const actions = document.createElement("div");
  actions.className = "job-actions";
  if (inferenceEnabled && !inferenceHeld && !job.run_requested && !held &&
      ["queued", "failed", "interrupted"].includes(job.status)) {
    const run = document.createElement("button");
    run.type = "button";
    run.textContent = job.status === "queued" ? "전사 시작" : "재시도";
    run.setAttribute("aria-label", `${job.name} ${run.textContent}`);
    run.addEventListener("click", async () => {
      run.disabled = true;
      try {
        const response = await fetch(`/api/jobs/${job.id}/run`, {
          method: "POST", headers: {"Content-Type": "application/json"}, body: "{}",
        });
        if (!response.ok) throw new Error();
        await loadJobs();
        refreshButton.focus();
      } catch {
        queueStatus.textContent = "실행 요청을 확인하지 못했습니다. 작업 목록을 새로고침해 주세요.";
        run.disabled = false;
      }
    });
    actions.append(run);
  }

  if (job.status === "completed") {
    const result = document.createElement("a");
    result.className = "result-link";
    result.href = `/jobs/${job.id}`;
    result.textContent = "결과 보기";
    result.setAttribute("aria-label", `${job.name} ${result.textContent}`);
    actions.append(result);
  }

  if (active || job.run_requested || job.external_pending) {
    item.append(actions);
    return item;
  }
  const remove = document.createElement("button");
  remove.type = "button";
  remove.textContent = "삭제";
  remove.setAttribute("aria-label", `${job.name} 삭제`);
  remove.addEventListener("click", async () => {
    if (!window.confirm(`"${job.name}" 원본과 관련 결과를 삭제할까요?`)) return;
    remove.disabled = true;
    try {
      const response = await fetch(`/api/jobs/${job.id}`, { method: "DELETE" });
      if (!response.ok && response.status !== 404) throw new Error();
      await loadJobs();
      refreshButton.focus();
    } catch {
      queueStatus.textContent = "삭제를 확인하지 못했습니다. 작업 목록을 새로고침해 주세요.";
      remove.disabled = false;
    }
  });
  actions.append(remove);
  item.append(actions);
  return item;
}

let listRequest;
let listSnapshot;
let observing = false;

async function loadJobs() {
  listRequest?.abort();
  const controller = new AbortController();
  listRequest = controller;
  refreshButton.disabled = true;
  try {
    const response = await fetch("/api/jobs", { signal: controller.signal, cache: "no-store" });
    if (!response.ok) throw new Error();
    const data = await response.json();
    const snapshot = JSON.stringify(data);
    if (snapshot !== listSnapshot) {
      const focusLabel = jobs.contains(document.activeElement)
        ? document.activeElement.getAttribute("aria-label") : null;
      jobs.replaceChildren(...data.jobs.map((job) => renderJob(
        job, data.inference_enabled, data.inference_held,
      )));
      if (focusLabel) {
        const restored = Array.from(jobs.querySelectorAll("button, a")).find(
          (button) => button.getAttribute("aria-label") === focusLabel,
        );
        (restored || refreshButton).focus();
      }
      listSnapshot = snapshot;
    }
    observing = data.inference_enabled;
    const queueSummary = data.jobs.length ? `작업 ${data.jobs.length}개` : "저장된 작업이 없습니다.";
    const message = data.inference_held ? "외부 실행 종료가 확인될 때까지 전사를 보류합니다."
      : !data.inference_enabled ? "서버의 전사 실행기가 비활성 상태입니다." : "";
    const status = [queueSummary, message].filter(Boolean).join(" · ");
    if (queueStatus.textContent !== status) queueStatus.textContent = status;
  } catch (error) {
    if (error.name !== "AbortError") queueStatus.textContent = "작업 목록을 불러오지 못했습니다.";
  } finally {
    if (listRequest === controller) refreshButton.disabled = false;
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = recording.files[0];
  if (!file || uploadButton.disabled) return;
  recording.disabled = true;
  uploadButton.disabled = true;
  progress.removeAttribute("value");
  progress.hidden = false;
  uploadStatus.textContent = "녹음 파일을 업로드하고 있습니다.";
  try {
    await upload(file);
    form.reset();
    uploadStatus.textContent = "녹음을 접수했습니다.";
    await loadJobs();
  } catch (error) {
    uploadStatus.textContent = error.message;
  } finally {
    progress.hidden = true;
    recording.disabled = false;
    uploadButton.disabled = false;
  }
});

uploadButton.disabled = false;
refreshButton.addEventListener("click", loadJobs);
setInterval(() => {
  if (observing && !document.hidden && !refreshButton.disabled) loadJobs();
}, 2500);
loadJobs();
