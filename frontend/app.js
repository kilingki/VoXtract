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

function renderJob(job) {
  const item = document.createElement("li");
  const info = document.createElement("div");
  const name = document.createElement("strong");
  const details = document.createElement("p");
  const seconds = Math.ceil(job.duration_seconds);
  const time = `${Math.floor(seconds / 60)}분 ${seconds % 60}초`;
  name.textContent = job.name;
  details.textContent = `대기 · ${time}`;
  info.append(name, details);

  const remove = document.createElement("button");
  remove.type = "button";
  remove.textContent = "삭제";
  remove.setAttribute("aria-label", `${job.name} 삭제`);
  remove.addEventListener("click", async () => {
    if (!window.confirm(`"${job.name}" 원본과 대기 작업을 삭제할까요?`)) return;
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
  item.append(info, remove);
  return item;
}

let listRequest;

async function loadJobs() {
  listRequest?.abort();
  const controller = new AbortController();
  listRequest = controller;
  refreshButton.disabled = true;
  try {
    const response = await fetch("/api/jobs", { signal: controller.signal, cache: "no-store" });
    if (!response.ok) throw new Error();
    const data = await response.json();
    jobs.replaceChildren(...data.jobs.map(renderJob));
    queueStatus.textContent = data.jobs.length ? `대기 작업 ${data.jobs.length}개` : "대기 중인 작업이 없습니다.";
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
loadJobs();
