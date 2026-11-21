const audio = document.querySelector(".reader audio");
const audioStatus = document.getElementById("audio-status");
const search = document.getElementById("transcript-search");
const searchStatus = document.getElementById("search-status");
const transcript = document.getElementById("transcript");

function normalize(text) {
  return text.normalize("NFC").toLocaleLowerCase("ko").replace(/\s+/g, " ").trim();
}

function timedElement(element) {
  return {element, start: Number(element.dataset.start), end: Number(element.dataset.end)};
}

const searchable = Array.from(transcript.querySelectorAll(".segment"), (element) => ({
  element, text: normalize(element.querySelector(".segment-text").textContent),
}));
let latestEnd = 0;
const rows = Array.from(transcript.querySelectorAll(".segment[data-start]"), (element) => {
  const row = timedElement(element);
  latestEnd = Math.max(latestEnd, row.end);
  row.latestEnd = latestEnd;
  row.words = Array.from(element.querySelectorAll(".segment-text > [data-start]"), timedElement);
  return row;
});
search.closest(".search").hidden = searchable.length === 0;

search.addEventListener("input", () => {
  const query = normalize(search.value);
  let matches = 0;
  for (const row of searchable) {
    row.element.hidden = !row.text.includes(query);
    if (!row.element.hidden) matches++;
  }
  searchStatus.textContent = matches ? `일치하는 구간 ${matches}개` : "일치하는 구간이 없습니다.";
  searchStatus.hidden = !query;
});

let currentRow;
let currentWord;

function syncPlayback() {
  // The browser can report a seek time rounded down to the nearest microsecond.
  const seconds = audio.currentTime + 0.000001;
  let low = 0;
  let high = rows.length;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (rows[middle].start <= seconds) low = middle + 1;
    else high = middle;
  }
  let nextRow;
  for (let index = low - 1; index >= 0 && rows[index].latestEnd > seconds; index--) {
    if (rows[index].end > seconds) {
      nextRow = rows[index];
      break;
    }
  }
  const nextWord = nextRow?.words.findLast((word) => word.start <= seconds && seconds < word.end);
  if (currentRow !== nextRow) {
    currentRow?.element.classList.remove("is-current");
    nextRow?.element.classList.add("is-current");
    currentRow = nextRow;
  }
  if (currentWord !== nextWord) {
    currentWord?.element.classList.remove("is-current");
    nextWord?.element.classList.add("is-current");
    currentWord = nextWord;
  }
}

transcript.addEventListener("click", (event) => {
  const button = event.target.closest("button.seek");
  if (button && !audio.error) {
    audio.currentTime = Number(button.closest(".segment").dataset.start);
    syncPlayback();
  }
});

for (const event of ["timeupdate", "seeked", "loadedmetadata"]) {
  audio.addEventListener(event, syncPlayback);
}
function reportAudioError() {
  audioStatus.textContent = "원본 녹음을 재생할 수 없습니다.";
  audioStatus.hidden = false;
}
audio.addEventListener("error", reportAudioError);
if (audio.error) reportAudioError();
