interface Props {
  title: string;
  setTitle: (t: string) => void;
  mode: 'write' | 'split' | 'preview';
  setMode: (m: 'write' | 'split' | 'preview') => void;
  onFullscreen: () => void;
  onSettings: () => void;
  onSaveDraft: () => void;
  onSave: () => void;
  saving: boolean;
}

export default function ActionBar({ title, setTitle, mode, setMode, onFullscreen, onSettings, onSaveDraft, onSave, saving }: Props) {
  return (
    <div className="writer-actionbar">
      <a href="/signage/contents" className="back-btn" title="닫고 콘텐츠 목록으로">
        <span style={{ fontSize: '1.1rem' }}>✕</span>
      </a>
      <div className="brand-mini">
        <img src="/static/LOGO.GIF" alt="" />
        <span className="brand-label">📺 콘텐츠 작성</span>
      </div>
      <input
        type="text"
        className="doc-title"
        placeholder="제목을 입력하세요"
        value={title}
        onChange={e => setTitle(e.target.value)}
      />

      <div className="mode-seg">
        <button type="button" className={mode === 'write' ? 'active' : ''} onClick={() => setMode('write')}>✏️ 작성</button>
        <button type="button" className={mode === 'split' ? 'active' : ''} onClick={() => setMode('split')}>📄 분할</button>
        <button type="button" className={mode === 'preview' ? 'active' : ''} onClick={() => setMode('preview')}>👁 미리보기</button>
      </div>

      <div className="ab-right">
        <button type="button" className="ab-icon-btn" onClick={onFullscreen}>⛶ 전체</button>
        <button type="button" className="ab-icon-btn" onClick={onSettings}>⚙️ 설정</button>
        <button type="button" className="ab-icon-btn" onClick={onSaveDraft} disabled={saving}>📝 임시저장</button>
        <button type="button" className="ab-icon-btn ab-primary" onClick={onSave} disabled={saving}>
          {saving ? '저장 중…' : '💾 저장'}
        </button>
      </div>
    </div>
  );
}
