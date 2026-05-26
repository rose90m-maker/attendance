import { useEditorStore, useCurrentPage } from '@/stores/editorStore';
import type { AnyElement, TextElement, ImageElement, VideoElement, ShapeElement } from '@/types/project';
import { DISPLAY_PRESETS } from '@/types/project';

export default function PropertiesPanel() {
  const selectedIds = useEditorStore(s => s.selectedIds);
  const updateElement = useEditorStore(s => s.updateElement);
  const updatePage = useEditorStore(s => s.updatePage);
  const page = useCurrentPage();
  const project = useEditorStore(s => s.project);
  const setProjectMeta = useEditorStore(s => s.setProjectMeta);
  const selected = selectedIds.length === 1 ? page.elements.find(e => e.id === selectedIds[0]) : null;

  return (
    <aside className="w-72 bg-white border-l border-sig-border overflow-y-auto flex-shrink-0">
      <div className="p-3 space-y-4">
        {!selected && (
          <>
            <section>
              <h6 className="panel-head !p-0 !border-0">캔버스</h6>
              <div className="mt-2 space-y-2">
                <select className="input" value={`${project.width}x${project.height}`}
                  onChange={e => {
                    const preset = DISPLAY_PRESETS.find(p => `${p.width}x${p.height}` === e.target.value);
                    if (preset) setProjectMeta({ width: preset.width, height: preset.height });
                  }}>
                  {DISPLAY_PRESETS.map(p => <option key={p.id} value={`${p.width}x${p.height}`}>{p.name}</option>)}
                </select>
                <div className="grid grid-cols-2 gap-2">
                  <div><label className="label-sm">가로</label>
                    <input type="number" className="input" value={project.width}
                      onChange={e => setProjectMeta({ width: Number(e.target.value) || 1920 })} /></div>
                  <div><label className="label-sm">세로</label>
                    <input type="number" className="input" value={project.height}
                      onChange={e => setProjectMeta({ height: Number(e.target.value) || 1080 })} /></div>
                </div>
              </div>
            </section>
            <section>
              <h6 className="panel-head !p-0 !border-0">페이지</h6>
              <div className="space-y-2 mt-2">
                <div><label className="label-sm">이름</label>
                  <input type="text" className="input" value={page.name || ''}
                    onChange={e => updatePage(page.id, { name: e.target.value })} /></div>
                <div className="grid grid-cols-2 gap-2">
                  <div><label className="label-sm">표시 시간(초)</label>
                    <input type="number" className="input" value={page.duration} min={1} max={600}
                      onChange={e => updatePage(page.id, { duration: Number(e.target.value) || 8 })} /></div>
                  <div><label className="label-sm">전환 효과</label>
                    <select className="input" value={page.transition}
                      onChange={e => updatePage(page.id, { transition: e.target.value as any })}>
                      <option value="none">없음</option><option value="fade">페이드</option>
                      <option value="slide">슬라이드</option><option value="zoom">줌</option>
                    </select></div>
                </div>
                <div><label className="label-sm">배경색</label>
                  <input type="color" className="input h-9" value={page.background || '#ffffff'}
                    onChange={e => updatePage(page.id, { background: e.target.value })} /></div>
              </div>
            </section>
          </>
        )}
        {selected && (
          <>
            <CommonProps element={selected} onChange={(patch) => updateElement(selected.id, patch as any)} />
            {selected.type === 'text' && <TextProps element={selected} />}
            {selected.type === 'image' && <ImageProps element={selected} />}
            {selected.type === 'video' && <VideoProps element={selected} />}
            {selected.type === 'shape' && <ShapeProps element={selected} />}
            {selected.type === 'widget' && <WidgetProps element={selected as any} />}
          </>
        )}
        {selectedIds.length > 1 && (
          <div className="text-sm text-sig-muted text-center py-8">
            {selectedIds.length}개 요소 선택됨<br/>
            <span className="text-xs">(다중 편집은 Phase 2에서)</span>
          </div>
        )}
      </div>
    </aside>
  );
}

function CommonProps({ element, onChange }: { element: AnyElement; onChange: (p: Partial<AnyElement>) => void }) {
  return (
    <section>
      <h6 className="panel-head !p-0 !border-0">위치 · 크기</h6>
      <div className="grid grid-cols-2 gap-2 mt-2">
        <div><label className="label-sm">X</label><input type="number" className="input" value={Math.round(element.x)} onChange={e => onChange({ x: Number(e.target.value) || 0 })} /></div>
        <div><label className="label-sm">Y</label><input type="number" className="input" value={Math.round(element.y)} onChange={e => onChange({ y: Number(e.target.value) || 0 })} /></div>
        <div><label className="label-sm">가로</label><input type="number" className="input" value={Math.round(element.width)} onChange={e => onChange({ width: Number(e.target.value) || 1 })} /></div>
        <div><label className="label-sm">세로</label><input type="number" className="input" value={Math.round(element.height)} onChange={e => onChange({ height: Number(e.target.value) || 1 })} /></div>
        <div><label className="label-sm">회전(°)</label><input type="number" className="input" value={Math.round(element.rotation)} onChange={e => onChange({ rotation: Number(e.target.value) || 0 })} /></div>
        <div><label className="label-sm">투명도</label><input type="range" min={0} max={1} step={0.05} value={element.opacity} onChange={e => onChange({ opacity: Number(e.target.value) })} className="w-full" /></div>
      </div>
    </section>
  );
}
function TextProps({ element }: { element: TextElement }) {
  const update = useEditorStore(s => s.updateElement);
  const p = element.props;
  const setP = (patch: Partial<typeof p>) => update(element.id, { props: { ...p, ...patch } } as any);
  return (
    <section>
      <h6 className="panel-head !p-0 !border-0 mt-4">텍스트</h6>
      <textarea className="input mt-2" rows={4} value={p.content} onChange={e => setP({ content: e.target.value })} />
      <div className="grid grid-cols-2 gap-2 mt-2">
        <div><label className="label-sm">크기(px)</label><input type="number" className="input" value={p.fontSize} onChange={e => setP({ fontSize: Number(e.target.value) || 16 })} /></div>
        <div><label className="label-sm">두께</label><select className="input" value={p.fontWeight || 400} onChange={e => setP({ fontWeight: Number(e.target.value) })}>
          <option value={300}>가늘게</option><option value={400}>보통</option><option value={600}>중간</option><option value={700}>굵게</option><option value={900}>매우굵게</option>
        </select></div>
        <div><label className="label-sm">색상</label><input type="color" className="input h-9" value={p.color} onChange={e => setP({ color: e.target.value })} /></div>
        <div><label className="label-sm">배경색</label><input type="color" className="input h-9" value={p.backgroundColor || '#ffffff'} onChange={e => setP({ backgroundColor: e.target.value })} /></div>
        <div><label className="label-sm">정렬</label><select className="input" value={p.textAlign || 'left'} onChange={e => setP({ textAlign: e.target.value as any })}><option value="left">왼쪽</option><option value="center">가운데</option><option value="right">오른쪽</option></select></div>
        <div><label className="label-sm">줄간격</label><input type="number" className="input" step={0.1} value={p.lineHeight || 1.4} onChange={e => setP({ lineHeight: Number(e.target.value) })} /></div>
      </div>
    </section>
  );
}
function ImageProps({ element }: { element: ImageElement }) {
  const update = useEditorStore(s => s.updateElement);
  const p = element.props; const setP = (patch: any) => update(element.id, { props: { ...p, ...patch } } as any);
  return (
    <section><h6 className="panel-head !p-0 !border-0 mt-4">이미지</h6>
      <div className="text-xs text-sig-muted mt-1 truncate" title={p.src}>{p.src}</div>
      <div className="grid grid-cols-2 gap-2 mt-2">
        <div><label className="label-sm">맞춤</label><select className="input" value={p.fit} onChange={e => setP({ fit: e.target.value })}>
          <option value="cover">채우기</option><option value="contain">맞춤</option><option value="stretch">늘이기</option></select></div>
        <div><label className="label-sm">모서리(px)</label><input type="number" className="input" value={p.borderRadius || 0} onChange={e => setP({ borderRadius: Number(e.target.value) })} /></div>
      </div>
    </section>
  );
}
function VideoProps({ element }: { element: VideoElement }) {
  const update = useEditorStore(s => s.updateElement);
  const p = element.props; const setP = (patch: any) => update(element.id, { props: { ...p, ...patch } } as any);
  return (
    <section><h6 className="panel-head !p-0 !border-0 mt-4">동영상</h6>
      <div className="text-xs text-sig-muted mt-1 truncate" title={p.src}>{p.src}</div>
      <div className="grid grid-cols-2 gap-2 mt-2">
        <div><label className="label-sm">맞춤</label><select className="input" value={p.fit} onChange={e => setP({ fit: e.target.value })}>
          <option value="cover">채우기</option><option value="contain">맞춤</option><option value="stretch">늘이기</option></select></div>
      </div>
      <div className="space-y-1 mt-2 text-sm">
        <label className="flex items-center gap-2"><input type="checkbox" checked={p.autoplay} onChange={e => setP({ autoplay: e.target.checked })} /> 자동재생</label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={p.loop} onChange={e => setP({ loop: e.target.checked })} /> 반복</label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={p.muted} onChange={e => setP({ muted: e.target.checked })} /> 음소거</label>
      </div>
    </section>
  );
}
function ShapeProps({ element }: { element: ShapeElement }) {
  const update = useEditorStore(s => s.updateElement);
  const p = element.props; const setP = (patch: any) => update(element.id, { props: { ...p, ...patch } } as any);
  return (
    <section><h6 className="panel-head !p-0 !border-0 mt-4">도형</h6>
      <div className="grid grid-cols-2 gap-2 mt-2">
        <div><label className="label-sm">종류</label><select className="input" value={p.kind} onChange={e => setP({ kind: e.target.value })}>
          <option value="rect">사각형</option><option value="circle">원</option><option value="line">선</option></select></div>
        <div><label className="label-sm">채우기</label><input type="color" className="input h-9" value={p.fill} onChange={e => setP({ fill: e.target.value })} /></div>
        <div><label className="label-sm">테두리</label><input type="color" className="input h-9" value={p.stroke || '#000000'} onChange={e => setP({ stroke: e.target.value })} /></div>
        <div><label className="label-sm">테두리두께</label><input type="number" className="input" value={p.strokeWidth || 0} onChange={e => setP({ strokeWidth: Number(e.target.value) })} /></div>
        <div className="col-span-2"><label className="label-sm">모서리(px)</label><input type="number" className="input" value={p.borderRadius || 0} onChange={e => setP({ borderRadius: Number(e.target.value) })} /></div>
      </div>
    </section>
  );
}
function WidgetProps({ element }: { element: AnyElement & { props: { kind: string; config: any } } }) {
  const update = useEditorStore(s => s.updateElement);
  const p = element.props;
  const setCfg = (k: string, v: any) => update(element.id, { props: { ...p, config: { ...p.config, [k]: v } } } as any);
  return (
    <section>
      <h6 className="panel-head !p-0 !border-0 mt-4">위젯 — {p.kind}</h6>
      {p.kind === 'clock' && (
        <label className="flex items-center gap-2 mt-2 text-sm">
          <input type="checkbox" checked={p.config?.showDate !== false} onChange={e => setCfg('showDate', e.target.checked)} /> 날짜 표시
        </label>
      )}
      {p.kind === 'qrcode' && (
        <div className="mt-2"><label className="label-sm">URL</label>
          <input type="url" className="input" value={p.config?.url || ''} onChange={e => setCfg('url', e.target.value)} /></div>
      )}
    </section>
  );
}
