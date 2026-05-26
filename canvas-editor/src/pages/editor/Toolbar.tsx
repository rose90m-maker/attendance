import { useRef } from 'react';
import { useEditorStore } from '@/stores/editorStore';
import { uploadFile } from '@/api/client';

export default function Toolbar({ onSave, onOpenSettings, onPreview, saving }: {
  onSave: (draft?: boolean) => void;
  onOpenSettings: () => void;
  onPreview: () => void;
  saving: boolean;
}) {
  const { project, zoom, showGrid, addElement, setZoom, toggleGrid } = useEditorStore();
  const imgRef = useRef<HTMLInputElement>(null);
  const vidRef = useRef<HTMLInputElement>(null);

  const addText = () => {
    addElement({
      type: 'text', x: project.width / 2 - 200, y: project.height / 2 - 50,
      width: 400, height: 100, rotation: 0, opacity: 1,
      props: { content: '텍스트 입력', fontSize: 48, color: '#0f172a', textAlign: 'center', fontWeight: 700 },
    } as any);
  };
  const addShape = () => {
    addElement({
      type: 'shape', x: project.width / 2 - 100, y: project.height / 2 - 100,
      width: 200, height: 200, rotation: 0, opacity: 1,
      props: { kind: 'rect', fill: '#1e40af', borderRadius: 12 },
    } as any);
  };
  const addClock = () => {
    addElement({
      type: 'widget', x: project.width / 2 - 200, y: project.height / 2 - 100,
      width: 400, height: 200, rotation: 0, opacity: 1,
      props: { kind: 'clock', config: { showDate: true } },
    } as any);
  };
  const addQR = () => {
    addElement({
      type: 'widget', x: project.width / 2 - 100, y: project.height / 2 - 100,
      width: 200, height: 200, rotation: 0, opacity: 1,
      props: { kind: 'qrcode', config: { url: 'https://taein.biz' } },
    } as any);
  };

  const onPickImage = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]; if (!f) return;
    try {
      const r = await uploadFile(f);
      addElement({
        type: 'image', x: project.width / 2 - 200, y: project.height / 2 - 150,
        width: 400, height: 300, rotation: 0, opacity: 1,
        props: { src: r.url, fit: 'cover' },
      } as any);
    } catch (err: any) { alert('업로드 실패: ' + err.message); }
    e.target.value = '';
  };
  const onPickVideo = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]; if (!f) return;
    try {
      const r = await uploadFile(f);
      addElement({
        type: 'video', x: project.width / 2 - 320, y: project.height / 2 - 180,
        width: 640, height: 360, rotation: 0, opacity: 1,
        props: { src: r.url, autoplay: true, loop: true, muted: true, fit: 'cover' },
      } as any);
    } catch (err: any) { alert('업로드 실패: ' + err.message); }
    e.target.value = '';
  };

  return (
    <header className="h-12 flex items-center px-4 gap-2 bg-white border-b border-sig-border flex-shrink-0">
      <a href="/signage/contents" className="btn">✕ 닫기</a>
      <input type="text" value={project.name}
        onChange={e => useEditorStore.getState().setProjectMeta({ name: e.target.value })}
        className="px-3 py-1 font-bold text-base bg-transparent outline-none focus:bg-sig-bg rounded"
        placeholder="콘텐츠 제목" />
      <div className="w-px h-6 bg-sig-border mx-1" />
      <button className="btn" onClick={addText}><span>T</span> 텍스트</button>
      <button className="btn" onClick={() => imgRef.current?.click()}>🖼 이미지</button>
      <button className="btn" onClick={() => vidRef.current?.click()}>🎬 영상</button>
      <button className="btn" onClick={addShape}>⬛ 도형</button>
      <button className="btn" onClick={addClock} title="시계 위젯">⏰ 시계</button>
      <button className="btn" onClick={addQR} title="QR코드 위젯">⚡ QR</button>

      <div className="flex-1" />

      <button className={`btn ${showGrid ? 'bg-sig-accent-soft' : ''}`} onClick={toggleGrid} title="그리드">⊞</button>
      <button className="btn-icon" onClick={() => setZoom(zoom / 1.2)}>−</button>
      <span className="text-sm text-sig-muted w-14 text-center">{Math.round(zoom * 100)}%</span>
      <button className="btn-icon" onClick={() => setZoom(zoom * 1.2)}>+</button>
      <button className="btn" onClick={() => setZoom(0.4)}>맞춤</button>

      <div className="w-px h-6 bg-sig-border mx-1" />
      <button className="btn" onClick={onPreview}>⛶ 미리보기</button>
      <button className="btn" onClick={onOpenSettings}>⚙️ 설정</button>
      <button className="btn" onClick={() => onSave(true)} disabled={saving}>📝 임시저장</button>
      <button className="btn btn-primary" onClick={() => onSave(false)} disabled={saving}>
        {saving ? '저장중...' : '💾 저장'}
      </button>

      <input ref={imgRef} type="file" accept="image/*" hidden onChange={onPickImage} />
      <input ref={vidRef} type="file" accept="video/*" hidden onChange={onPickVideo} />
    </header>
  );
}
