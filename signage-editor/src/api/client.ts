// Flask 백엔드 클라이언트
import type { ContentDoc } from '../types';

export async function uploadFile(file: File): Promise<string> {
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch('/signage/api/slide/upload', {
    method: 'POST',
    body: fd,
    credentials: 'same-origin',
  });
  const j = await res.json();
  if (!j.ok) throw new Error(j.msg || '업로드 실패');
  return j.url as string;
}

export async function saveContent(cid: number | null, doc: ContentDoc): Promise<{ ok: boolean; id?: number; msg?: string }> {
  const url = cid ? `/signage/api/editor/${cid}/save` : '/signage/api/editor/save';
  const res = await fetch(url, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(doc),
  });
  return res.json();
}
