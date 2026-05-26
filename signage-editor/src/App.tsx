import { useCallback, useEffect, useState } from 'react';
import ActionBar from './components/ActionBar';
import ContentEditor from './components/ContentEditor';
import PreviewPanel from './components/PreviewPanel';
import SettingsDrawer from './components/SettingsDrawer';
import { saveContent } from './api/client';
import type { ContentDoc, SignageSettings, DisplayOption, InitialData } from './types';

// 서버에서 hydration 데이터 읽기
function readInitial(): InitialData {
  const el = document.getElementById('__initial_data__');
  if (!el) return { mode: 'new', content_id: null, content: null, displays: [], all_tags: [] };
  try {
    return JSON.parse(el.textContent || '{}');
  } catch {
    return { mode: 'new', content_id: null, content: null, displays: [], all_tags: [] };
  }
}

export default function App() {
  const initial = readInitial();
  const c = initial.content;

  const [title, setTitle] = useState<string>(c?.title || '');
  const [editorJson, setEditorJson] = useState<any[]>(c?.editor_json || []);
  const [bodyHtml, setBodyHtml] = useState<string>(c?.body_html || '');
  const [mode, setMode] = useState<'write' | 'split' | 'preview'>('write');
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [fsOpen, setFsOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: 'info' | 'success' | 'error' } | null>(null);

  const [settings, setSettings] = useState<SignageSettings>({
    duration_sec: c?.duration_sec ?? 15,
    priority: c?.priority ?? 0,
    status: (c?.status as any) || 'active',
    bg_color: c?.bg_color || '#0f172a',
    start_at: c?.start_at || null,
    end_at: c?.end_at || null,
    target_displays: c?.target_displays || [],
    tags: c?.tags || [],
    add_to_default: false,
  });

  const onEditorChange = useCallback((json: any[], html: string) => {
    setEditorJson(json);
    setBodyHtml(html);
  }, []);

  // ESC로 전체화면 닫기
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (fsOpen) setFsOpen(false);
        else if (settingsOpen) setSettingsOpen(false);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [fsOpen, settingsOpen]);

  const showToast = (msg: string, type: 'info' | 'success' | 'error' = 'info') => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 3500);
  };

  const doSave = async (asDraft = false) => {
    if (!title.trim()) {
      showToast('제목을 입력하세요', 'error');
      return;
    }
    setSaving(true);
    try {
      const doc: ContentDoc = {
        title: title.trim(),
        editor_json: editorJson,
        body_html: bodyHtml,
        signage: {
          ...settings,
          status: asDraft ? 'draft' : settings.status,
        },
      };
      const j = await saveContent(initial.content_id, doc);
      if (j.ok) {
        showToast('저장 완료', 'success');
        setTimeout(() => {
          window.location.href = '/signage/contents';
        }, 800);
      } else {
        showToast(`저장 실패: ${j.msg || '알 수 없음'}`, 'error');
      }
    } catch (e: any) {
      showToast(`저장 오류: ${e.message}`, 'error');
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <ActionBar
        title={title} setTitle={setTitle}
        mode={mode} setMode={setMode}
        onFullscreen={() => setFsOpen(true)}
        onSettings={() => setSettingsOpen(true)}
        onSaveDraft={() => doSave(true)}
        onSave={() => doSave(false)}
        saving={saving}
      />

      <div className={`workspace mode-${mode}`}>
        <div className="doc-area">
          <div className="doc-paper">
            <ContentEditor
              initialContent={c?.editor_json && c.editor_json.length > 0 ? c.editor_json : undefined}
              onChange={onEditorChange}
            />
          </div>
        </div>

        {(mode === 'split' || mode === 'preview') && (
          <PreviewPanel
            html={bodyHtml}
            bg={settings.bg_color}
            setBg={(c) => setSettings(s => ({ ...s, bg_color: c }))}
            blockCount={editorJson.length}
            onFullscreen={() => setFsOpen(true)}
          />
        )}
      </div>

      <SettingsDrawer
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        settings={settings}
        onChange={(patch) => setSettings(s => ({ ...s, ...patch }))}
        displays={initial.displays}
        allTags={initial.all_tags}
      />

      {fsOpen && (
        <div className="fs-overlay" onClick={() => setFsOpen(false)}>
          <button className="fs-close" onClick={(e) => { e.stopPropagation(); setFsOpen(false); }}>
            ✕ 닫기 (ESC)
          </button>
          <div className="fs-canvas" style={{ background: settings.bg_color }} onClick={e => e.stopPropagation()}>
            <div
              className="pv-content"
              dangerouslySetInnerHTML={{ __html: bodyHtml }}
            />
          </div>
        </div>
      )}

      {toast && <div className={`toast ${toast.type}`}>{toast.msg}</div>}
    </>
  );
}
