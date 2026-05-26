import type { Project, SignageSettings } from '@/types/project';

export async function uploadFile(file: File): Promise<{ url: string; kind: string }> {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch('/signage/api/slide/upload', {
    method: 'POST', body: fd, credentials: 'same-origin',
  });
  const j = await res.json();
  if (!j.ok) throw new Error(j.msg || '업로드 실패');
  return { url: j.url, kind: j.kind };
}

export interface SaveDoc {
  title: string;
  project: Project;
  signage: SignageSettings;
}

export async function saveCanvas(cid: number | null, doc: SaveDoc): Promise<{ ok: boolean; id?: number; msg?: string }> {
  const url = cid ? `/signage/api/canvas/${cid}/save` : '/signage/api/canvas/save';
  const res = await fetch(url, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(doc),
  });
  return res.json();
}
