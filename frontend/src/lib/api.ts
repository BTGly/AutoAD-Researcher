const API_BASE = 'http://localhost:8000';

export async function sendChat(userInput: string, runId: string): Promise<{ reply: string; reply_kind: string }> {
  const res = await fetch(`${API_BASE}/api/chat/send`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user_input: userInput, run_id: runId }),
  });
  if (!res.ok) throw new Error(`Chat API error: ${res.status}`);
  return res.json();
}

export async function getRuns(): Promise<Array<{ run_id: string; created_at: string; sources_count: number }>> {
  const res = await fetch(`${API_BASE}/api/runs`);
  if (!res.ok) throw new Error(`Runs API error: ${res.status}`);
  return res.json();
}

export async function createRun(): Promise<{ run_id: string }> {
  const res = await fetch(`${API_BASE}/api/runs`, { method: 'POST' });
  if (!res.ok) throw new Error(`Create run error: ${res.status}`);
  return res.json();
}

export async function getSources(runId: string): Promise<any[]> {
  const res = await fetch(`${API_BASE}/api/runs/${runId}/sources`);
  if (!res.ok) return [];
  return res.json();
}

export async function getJobs(runId: string): Promise<any[]> {
  const res = await fetch(`${API_BASE}/api/runs/${runId}/jobs`);
  if (!res.ok) return [];
  return res.json();
}

export async function getArtifact(path: string): Promise<{ path: string; content: string }> {
  const res = await fetch(`${API_BASE}/api/artifacts/${path}`);
  if (!res.ok) throw new Error(`Artifact not found: ${path}`);
  return res.json();
}

export function wsUrl(runId: string): string {
  return `${API_BASE.replace('http', 'ws')}/api/runs/${runId}/ws`;
}
