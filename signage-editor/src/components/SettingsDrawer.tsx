import type { SignageSettings, DisplayOption } from '../types';

interface Props {
  open: boolean;
  onClose: () => void;
  settings: SignageSettings;
  onChange: (patch: Partial<SignageSettings>) => void;
  displays: DisplayOption[];
  allTags: string[];
}

export default function SettingsDrawer({ open, onClose, settings, onChange, displays, allTags }: Props) {
  const toggleDisplay = (id: number) => {
    const next = settings.target_displays.includes(id)
      ? settings.target_displays.filter(x => x !== id)
      : [...settings.target_displays, id];
    onChange({ target_displays: next });
  };

  const addTag = (name: string) => {
    if (settings.tags.includes(name)) return;
    onChange({ tags: [...settings.tags, name] });
  };

  return (
    <>
      <div className={`drawer-overlay ${open ? 'show' : ''}`} onClick={onClose} />
      <aside className={`settings-drawer ${open ? 'open' : ''}`}>
        <div className="drawer-head">
          ⚙️ 사이니지 설정
          <button type="button" className="close-btn" onClick={onClose}>✕</button>
        </div>
        <div className="drawer-body">
          <div className="drawer-section">
            <h6>표시</h6>
            <div className="drawer-grid">
              <div>
                <label className="form-label-sm">재생 시간(초)</label>
                <input
                  type="number" min={3} max={600}
                  value={settings.duration_sec}
                  onChange={e => onChange({ duration_sec: parseInt(e.target.value) || 15 })}
                />
              </div>
              <div>
                <label className="form-label-sm">우선순위</label>
                <input
                  type="number" min={-100} max={100}
                  value={settings.priority}
                  onChange={e => onChange({ priority: parseInt(e.target.value) || 0 })}
                />
              </div>
              <div>
                <label className="form-label-sm">상태</label>
                <select
                  value={settings.status}
                  onChange={e => onChange({ status: e.target.value as any })}
                >
                  <option value="active">사용</option>
                  <option value="draft">임시</option>
                  <option value="paused">중지</option>
                </select>
              </div>
              <div>
                <label className="form-label-sm">송출 배경색</label>
                <input
                  type="color"
                  value={settings.bg_color}
                  onChange={e => onChange({ bg_color: e.target.value })}
                  style={{ height: 34, padding: 2 }}
                />
              </div>
            </div>
          </div>

          <div className="drawer-section">
            <h6>노출 기간</h6>
            <div className="drawer-grid">
              <div className="full">
                <label className="form-label-sm">시작일시</label>
                <input
                  type="datetime-local"
                  value={settings.start_at || ''}
                  onChange={e => onChange({ start_at: e.target.value || null })}
                />
              </div>
              <div className="full">
                <label className="form-label-sm">종료일시</label>
                <input
                  type="datetime-local"
                  value={settings.end_at || ''}
                  onChange={e => onChange({ end_at: e.target.value || null })}
                />
              </div>
            </div>
          </div>

          <div className="drawer-section">
            <h6>대상 디스플레이 (참고용)</h6>
            <div className="target-list">
              {displays.length === 0 && (
                <div style={{ color: '#9ca3af', fontSize: '0.85rem' }}>등록된 디스플레이 없음</div>
              )}
              {displays.map(d => (
                <label key={d.id}>
                  <input
                    type="checkbox"
                    checked={settings.target_displays.includes(d.id)}
                    onChange={() => toggleDisplay(d.id)}
                  />
                  &nbsp;{d.name}
                  {d.location && <span style={{ color: '#9ca3af' }}> ({d.location})</span>}
                </label>
              ))}
            </div>
            <div style={{ fontSize: '0.72rem', color: '#9ca3af', marginTop: 6 }}>
              ⚠️ 실제 송출은 플레이리스트/편성표가 결정합니다.
            </div>
          </div>

          <div className="drawer-section">
            <h6>태그</h6>
            <input
              type="text"
              placeholder="쉼표(,) 또는 줄바꿈으로 구분"
              value={settings.tags.join(', ')}
              onChange={e =>
                onChange({
                  tags: e.target.value
                    .replace(/,/g, '\n')
                    .split('\n')
                    .map(t => t.trim())
                    .filter(Boolean),
                })
              }
            />
            {allTags.length > 0 && (
              <div style={{ marginTop: 8 }}>
                {allTags.slice(0, 20).map(t => (
                  <span key={t} className="tag-chip" onClick={() => addTag(t)}>{t}</span>
                ))}
              </div>
            )}
          </div>

          <div className="drawer-section">
            <div className="check-row">
              <input
                type="checkbox"
                id="cb_default"
                checked={settings.add_to_default}
                onChange={e => onChange({ add_to_default: e.target.checked })}
              />
              <label htmlFor="cb_default">기본 플레이리스트에 자동 추가</label>
            </div>
          </div>
        </div>
      </aside>
    </>
  );
}
