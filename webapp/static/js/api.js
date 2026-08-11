// Thin fetch layer. Slow endpoints return a job id, so `job()` submits and
// polls, reporting progress along the way -- a 35 second sync should look like
// progress, not a hang.

export class ApiError extends Error {
  constructor(message, detail) {
    super(message);
    this.detail = detail;
  }
}

async function request(path, options = {}) {
  let res;
  try {
    res = await fetch(path, {
      headers: { 'Content-Type': 'application/json' },
      ...options,
    });
  } catch (err) {
    throw new ApiError('Cannot reach the server. Is it still running?', String(err));
  }
  let payload;
  try {
    payload = await res.json();
  } catch {
    throw new ApiError(`Server returned a non-JSON response (${res.status}).`);
  }
  if (!res.ok || payload?.error) {
    throw new ApiError(payload?.error || `Request failed (${res.status})`, payload);
  }
  return payload;
}

export const get = (path, params = {}) => {
  const qs = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
  ).toString();
  return request(qs ? `${path}?${qs}` : path);
};

export const post = (path, body = {}) =>
  request(path, { method: 'POST', body: JSON.stringify(body) });

/**
 * Submit a background job and poll until it settles.
 * onProgress receives the human-readable status line from the server.
 */
export async function job(path, body = {}, onProgress = () => {}, { interval = 450, timeout = 300000 } = {}) {
  const { job: started } = await post(path, body);
  onProgress(started.progress, 0);
  const deadline = Date.now() + timeout;

  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, interval));
    const state = await get('/api/jobs', { id: started.id });
    if (state.status === 'gone') throw new ApiError('The job expired before it could be read.');
    onProgress(state.progress, state.elapsed);
    if (state.status === 'done') return state.result;
    if (state.status === 'error') throw new ApiError(state.error || 'Job failed');
  }
  throw new ApiError('Job timed out.');
}

/** Server-sent events for the live draft. Reconnects on drop. */
export function draftStream(slot, handlers = {}) {
  let source = null;
  let closed = false;
  let retry = null;

  const connect = () => {
    if (closed) return;
    const qs = slot ? `?slot=${slot}` : '';
    source = new EventSource(`/api/events/draft${qs}`);
    source.onmessage = (evt) => {
      let data;
      try { data = JSON.parse(evt.data); } catch { return; }
      if (data.type === 'draft') handlers.onDraft?.(data.state);
      else if (data.type === 'error') handlers.onError?.(data.message);
      handlers.onAny?.(data);
    };
    source.onopen = () => handlers.onOpen?.();
    source.onerror = () => {
      source?.close();
      handlers.onDisconnect?.();
      if (!closed) retry = setTimeout(connect, 3000);
    };
  };

  connect();
  return {
    close() {
      closed = true;
      clearTimeout(retry);
      source?.close();
    },
  };
}
