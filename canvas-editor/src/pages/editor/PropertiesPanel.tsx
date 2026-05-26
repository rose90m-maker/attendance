import { useEditorStore, useCurrentPage } from '@/stores/editorStore';
import type { AnyElement, TextElement, ImageElement, VideoElement, ShapeElement, TableElement } from '@/types/project';
import { DISPLAY_PRESETS } from '@/types/project';
import { uploadFile } from '@/api/client';

// ─── 정렬·분배 액션 (다중 선택 시) ───
function alignSelected(mode: 'left' | 'center' | 'right' | 'top' | 'middle' | 'bottom') {
  const s = useEditorStore.getState();
  const page = s.project.pages.find(p => p.id === s.currentPageId);
  if (!page) return;
  const sel = page.elements.filter(e => s.selectedIds.includes(e.id));
  if (sel.length === 0) return;
  // 다중이면 그들의 bounding box 기준, 단일이면 캔버스 기준
  const bbox = sel.length > 1
    ? { x: Math.min(...sel.map(e => e.x)), y: Math.min(...sel.map(e => e.y)),
        x2: Math.max(...sel.map(e => e.x + e.width)), y2: Math.max(...sel.map(e => e.y + e.height)) }
    : { x: 0, y: 0, x2: s.project.width, y2: s.project.height };
  for (const e of sel) {
    let { x, y } = e;
    if (mode === 'left') x = bbox.x;
    else if (mode === 'center') x = (bbox.x + bbox.x2) / 2 - e.width / 2;
    else if (mode === 'right') x = bbox.x2 - e.width;
    else if (mode === 'top') y = bbox.y;
    else if (mode === 'middle') y = (bbox.y + bbox.y2) / 2 - e.height / 2;
    else if (mode === 'bottom') y = bbox.y2 - e.height;
    s.updateElement(e.id, { x, y });
  }
}

function distribute(axis: 'h' | 'v') {
  const s = useEditorStore.getState();
  const page = s.project.pages.find(p => p.id === s.currentPageId);
  if (!page) return;
  const sel = page.elements.filter(e => s.selectedIds.includes(e.id));
  if (sel.length < 3) { alert('3개 이상 선택해야 균등 분배할 수 있습니다.'); return; }
  const sorted = [...sel].sort((a, b) => axis === 'h' ? a.x - b.x : a.y - b.y);
  const first = sorted[0], last = sorted[sorted.length - 1];
  if (axis === 'h') {
    const totalRange = (last.x + last.width / 2) - (first.x + first.width / 2);
    const step = totalRange / (sorted.length - 1);
    sorted.forEach((e, i) => { if (i > 0 && i < sorted.length - 1) {
      const cx = (first.x + first.width / 2) + step * i;
      s.updateElement(e.id, { x: cx - e.width / 2 });
    }});
  } else {
    const totalRange = (last.y + last.height / 2) - (first.y + first.height / 2);
    const step = totalRange / (sorted.length - 1);
    sorted.forEach((e, i) => { if (i > 0 && i < sorted.length - 1) {
      const cy = (first.y + first.height / 2) + step * i;
      s.updateElement(e.id, { y: cy - e.height / 2 });
    }});
  }
}

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
                <div>
                  <label className="label-sm">배경 이미지</label>
                  <div className="flex gap-1">
                    <button className="btn flex-1" onClick={async () => {
                      const inp = document.createElement('input');
                      inp.type = 'file'; inp.accept = 'image/*';
                      inp.onchange = async () => {
                        const f = inp.files?.[0]; if (!f) return;
                        try {
                          const r = await uploadFile(f);
                          updatePage(page.id, { backgroundImage: r.url, backgroundFit: 'cover' });
                        } catch (e: any) { alert('업로드 실패: ' + e.message); }
                      };
                      inp.click();
                    }}>📁 선택</button>
                    {page.backgroundImage && (
                      <button className="btn" onClick={() => updatePage(page.id, { backgroundImage: undefined })}>✕</button>
                    )}
                  </div>
                  {page.backgroundImage && (
                    <select className="input mt-1" value={page.backgroundFit || 'cover'}
                      onChange={e => updatePage(page.id, { backgroundFit: e.target.value as any })}>
                      <option value="cover">채우기</option>
                      <option value="contain">맞춤</option>
                    </select>
                  )}
                </div>
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
            {selected.type === 'table' && <TableProps element={selected as TableElement} />}
            <AlignSection />
          </>
        )}
        {selectedIds.length > 1 && (
          <>
            <div className="text-sm text-sig-muted text-center pt-2">
              {selectedIds.length}개 요소 선택됨
            </div>
            <AlignSection multi />
          </>
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
function AlignSection({ multi = false }: { multi?: boolean }) {
  return (
    <section className="mt-4 pt-3 border-t border-sig-border">
      <h6 className="panel-head !p-0 !border-0">정렬{multi && ' · 분배'}</h6>
      <div className="text-xs text-sig-muted mt-1 mb-2">
        {multi ? '선택된 요소들의 경계 기준' : '캔버스 기준'}
      </div>
      <div className="grid grid-cols-3 gap-1">
        <button className="btn !p-1.5 !text-xs" onClick={() => alignSelected('left')} title="왼쪽">⇤ 좌</button>
        <button className="btn !p-1.5 !text-xs" onClick={() => alignSelected('center')} title="가운데(가로)">⊟ 중</button>
        <button className="btn !p-1.5 !text-xs" onClick={() => alignSelected('right')} title="오른쪽">⇥ 우</button>
        <button className="btn !p-1.5 !text-xs" onClick={() => alignSelected('top')} title="위">⇡ 상</button>
        <button className="btn !p-1.5 !text-xs" onClick={() => alignSelected('middle')} title="가운데(세로)">⊟ 중</button>
        <button className="btn !p-1.5 !text-xs" onClick={() => alignSelected('bottom')} title="아래">⇣ 하</button>
      </div>
      {multi && (
        <div className="grid grid-cols-2 gap-1 mt-2">
          <button className="btn !p-1.5 !text-xs" onClick={() => distribute('h')} title="가로 균등 분배">↔ 분배</button>
          <button className="btn !p-1.5 !text-xs" onClick={() => distribute('v')} title="세로 균등 분배">↕ 분배</button>
        </div>
      )}
    </section>
  );
}

function TableProps({ element }: { element: TableElement }) {
  const update = useEditorStore(s => s.updateElement);
  const p = element.props;
  const setP = (patch: any) => update(element.id, { props: { ...p, ...patch } } as any);
  const cells = p.cells || [[]];
  const rows = cells.length;
  const cols = Math.max(...cells.map(r => r.length));

  const addRow = () => setP({ cells: [...cells, Array(cols).fill(null).map(() => ({ text: '' }))] });
  const addCol = () => setP({ cells: cells.map(r => [...r, { text: '' }]) });
  const delRow = () => rows > 1 && setP({ cells: cells.slice(0, -1) });
  const delCol = () => cols > 1 && setP({ cells: cells.map(r => r.slice(0, -1)) });

  return (
    <section>
      <h6 className="panel-head !p-0 !border-0 mt-4">표 ({rows}행 × {cols}열)</h6>
      <div className="grid grid-cols-2 gap-1 mt-2">
        <button className="btn !p-1.5 !text-xs" onClick={addRow}>＋ 행</button>
        <button className="btn !p-1.5 !text-xs" onClick={addCol}>＋ 열</button>
        <button className="btn !p-1.5 !text-xs" onClick={delRow}>－ 행</button>
        <button className="btn !p-1.5 !text-xs" onClick={delCol}>－ 열</button>
      </div>
      <div className="grid grid-cols-2 gap-2 mt-3">
        <div><label className="label-sm">글자 크기</label>
          <input type="number" className="input" value={p.fontSize}
            onChange={e => setP({ fontSize: Number(e.target.value) || 24 })} /></div>
        <div><label className="label-sm">글자색</label>
          <input type="color" className="input h-9" value={p.color}
            onChange={e => setP({ color: e.target.value })} /></div>
        <div><label className="label-sm">테두리색</label>
          <input type="color" className="input h-9" value={p.borderColor}
            onChange={e => setP({ borderColor: e.target.value })} /></div>
        <div><label className="label-sm">테두리(px)</label>
          <input type="number" className="input" value={p.borderWidth}
            onChange={e => setP({ borderWidth: Number(e.target.value) || 1 })} /></div>
        <div className="col-span-2"><label className="label-sm">헤더 스타일</label>
          <select className="input" value={p.headerStyle}
            onChange={e => setP({ headerStyle: e.target.value })}>
            <option value="none">없음</option>
            <option value="first-row">첫 행</option>
            <option value="first-col">첫 열</option>
            <option value="first-both">첫 행 + 첫 열</option>
          </select></div>
        <div><label className="label-sm">헤더 배경</label>
          <input type="color" className="input h-9" value={p.headerBg || '#1e40af'}
            onChange={e => setP({ headerBg: e.target.value })} /></div>
        <div><label className="label-sm">헤더 글자</label>
          <input type="color" className="input h-9" value={p.headerColor || '#ffffff'}
            onChange={e => setP({ headerColor: e.target.value })} /></div>
      </div>
      <div className="text-xs text-sig-muted mt-2">
        💡 표 클릭 후 셀에 직접 입력
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
