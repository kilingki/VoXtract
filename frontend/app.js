// Development liveness check only; no upload or inference behavior.
async function checkHealth() {
  const status = document.getElementById("health");
  try {
    const response = await fetch("/api/health", {
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) throw new Error("Health request failed");
    const data = await response.json();
    if (data.status !== "ok") throw new Error("Unexpected health response");
    status.textContent = "서버 연결 정상 · 추론 서비스 연동 전";
  } catch {
    status.textContent = "서버 연결을 확인하지 못했습니다. 새로고침해 주세요.";
  }
}
checkHealth();
