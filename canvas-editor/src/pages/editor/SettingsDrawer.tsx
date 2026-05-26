import type { SignageSettings, DisplayOption } from '@/types/project';

interface Props {
  open: boolean; onClose: () => void;
  settings: SignageSettings;
  onChange: (patch: Partial<SignageSettings>) => void;
  displays: DisplayOption[];
  allTags: string[];
}
export default function SettingsDrawer({ open, onClose, settings, onChange, displays, allTags }: Props) {
  return (
    <>
      <div className={`fixed inset-0 bg-black/20 z-40 transition-opacity ${open ? 'opacity-100 pointer-events-auto' : 'opacity-0 pointer-events-none'}`}
        onClick={onClose} />
      <aside className={`fixed right-0 top-0 bottom-0 w-80 bg-white border-l border-sig-border shadow-2xl z-50 transition-transform ${open ? 'translate-x-0' : 'translate-x-full'} flex flex-col`}>
        <div className="px-4 py-3 border-b border-sig-border flex items-center justify-between">
          <h6 className="font-bold">⚙️ 사이니지 설정</h6>
          <button onClick={onClose} className="text-xl text-sig-muted hover:text-black">✕</button>
        </div>
        <div className="flex-1 overflow-y-auto p-4 space-y-5">
          <section>
            <h6 className="panel-head !p-0 !border-0 mb-2">표시</h6>
            <div className="grid grid-cols-2 gap-2">
              <div><label className="label-sm">재생 시간(초)</label>
                <input type="number" className="input" value={settings.duration_sec} min={3} max={600}
                  onChange={e => onChange({ duration_sec: Number(e.target.value) || 15 })} /></div>
              <div><label className="label-sm">우선순위</label>
                <input type="number" className="input" value={settings.priority} min={-100} max={100}
                  onChange={e => onChange({ priority: Number(e.target.value) || 0 })} /></div>
              <div><label className="label-sm">상태</label>
                <select className="input" value={settings.status} onChange={e => onChange({ status: e.target.value as any })}>
                  <option value="active">사용</option><option value="draft">임시</option><option value="paused">중지</option>
                </select></div>
              <div><label className="label-sm">송출 배경색</label>
                <input type="color" className="input h-9" value={settings.bg_color} onChange={e => onChange({ bg_color: e.target.value })} /></div>
            </div>
          </section>
          <section>
            <h6 className="panel-head !p-0 !border-0 mb-2">노출 기간</h6>
            <div className="space-y-2">
              <div><label className="label-sm">시작일시</label>
                <input type="datetime-local" className="input" value={settings.start_at || ''}
                  onChange={e => onChange({ start_at: e.target.value || null })} /></div>
              <div><label className="label-sm">종료일시</label>
                <input type="datetime-local" className="input" value={settings.end_at || ''}
                  onChange={e => onChange({ end_at: e.target.value || null })} /></div>
            </div>
          </section>
          <section>
            <h6 className="panel-head !p-0 !border-0 mb-2">대상 디스플레이</h6>
            <div className="max-h-40 overflow-y-auto border border-sig-border rounded p-2 space-y-1">
              {displays.length === 0 && <div className="text-xs text-sig-muted">등록된 디스플레이 없음</div>}
              {displays.map(d => (
                <label key={d.id} className="flex items-center gap-2 text-sm">
                  <input type="checkbox" checked={settings.target_displays.includes(d.id)}
                    onChange={() => onChange({
                      target_displays: settings.target_displays.includes(d.id)
                        ? settings.target_displays.filter(x => x !== d.id)
                        : [...settings.target_displays, d.id]
                    })} />
                  {d.name}{d.location && <span className="text-sig-muted text-xs"> ({d.location})</span>}
                </label>
              ))}
            </div>
            <div className="text-xs text-sig-muted mt-1">⚠️ 실제 송출은 플레이리스트/편성표가 결정</div>
          </section>
          <section>
            <h6 className="panel-head !p-0 !border-0 mb-2">태그</h6>
            <input type="text" className="input" placeholder="쉼표(,)로 구분"
              value={settings.tags.join(', ')}
              onChange={e => onChange({ tags: e.target.value.split(',').map(t => t.trim()).filter(Boolean) })} />
            {allTags.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-1">
                {allTags.slice(0, 20).map(t => (
                  <span key={t} className="text-xs px-2 py-0.5 bg-sig-accent-soft text-sig-accent rounded-full cursor-pointer hover:bg-sig-accent hover:text-white"
                    onClick={() => { if (!settings.tags.includes(t)) onChange({ tags: [...settings.tags, t] }); }}>{t}</span>
                ))}
              </div>
            )}
          </section>
          <section>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={settings.add_to_default}
                onChange={e => onChange({ add_to_default: e.target.checked })} />
              기본 플레이리스트에 자동 추가
            </label>
          </section>
        </div>
      </aside>
    </>
  );
}
