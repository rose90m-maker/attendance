import { useEffect, useState } from 'react';
import { useEditorStore } from '@/stores/editorStore';
import { useKeyboardShortcuts } from '@/hooks/useKeyboardShortcuts';
import Toolbar from '@/pages/editor/Toolbar';
import PageNavigator from '@/pages/editor/PageNavigator';
import Canvas from '@/pages/editor/Canvas';
import LayersPanel from '@/pages/editor/LayersPanel';
import PropertiesPanel from '@/pages/editor/PropertiesPanel';
import SettingsDrawer from '@/pages/editor/SettingsDrawer';
import PreviewModal from '@/pages/editor/PreviewModal';
import { saveCanvas } from '@/api/client';
import type { InitialData, SignageSettings } from '@/types/project';

function readInitial(): InitialData {
  const el = document.getElementById('__initial_data__');
  if (!el) return { mode: 'new', content_id: null, content: null, displays: [], all_tags: [] };
  try { return JSON.parse(el.textContent || '{}'); }
  catch { return { mode: 'new', content_id: null, content: null, displays: [], all_tags: [] }; }
}

export default function App() {
  useKeyboardShortcuts();
  const initial = readInitial();
  const c = initial.content;
  const project = useEditorStore(s => s.project);
  const setProject = useEditorStore(s => s.setProject);
  const setProjectMeta = useEditorStore(s => s.setProjectMeta);

  const [settingsOpen, setSettingsOpen] = useState(false);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ msg: string; type: 'info' | 'error' | 'success' } | null>(null);

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

  // 편집 모드 → 초기 project 로드
  useEffect(() => {
    if (c && c.project) setProject(c.project);
    if (c && c.title) setProjectMeta({ name: c.title });
  }, []);

  const showToast = (msg: string, type: 'info' | 'error' | 'success' = 'info') => {
    setToast({ msg, type });
    setTimeout(() => setToast(null), 3500);
  };

  const onSave = async (asDraft = false) => {
    if (!project.name?.trim()) { showToast('제목을 입력하세요', 'error'); return; }
    setSaving(true);
    try {
      const r = await saveCanvas(initial.content_id, {
        title: project.name,
        project,
        signage: { ...settings, status: asDraft ? 'draft' : settings.status },
      });
      if (r.ok) {
        showToast('저장 완료', 'success');
        setTimeout(() => { window.location.href = '/signage/contents'; }, 700);
      } else {
        showToast('저장 실패: ' + (r.msg || '알 수 없음'), 'error');
      }
    } catch (e: any) {
      showToast('저장 오류: ' + e.message, 'error');
    } finally { setSaving(false); }
  };

  return (
    <div className="h-full flex flex-col">
      <Toolbar
        onSave={onSave}
        onOpenSettings={() => setSettingsOpen(true)}
        onPreview={() => setPreviewOpen(true)}
        saving={saving}
      />
      <PageNavigator />
      <div className="flex-1 flex overflow-hidden">
        <LayersPanel />
        <Canvas />
        <PropertiesPanel />
      </div>

      <SettingsDrawer
        open={settingsOpen} onClose={() => setSettingsOpen(false)}
        settings={settings}
        onChange={(patch) => setSettings(s => ({ ...s, ...patch }))}
        displays={initial.displays}
        allTags={initial.all_tags}
      />

      {previewOpen && <PreviewModal project={project} onClose={() => setPreviewOpen(false)} />}

      {toast && (
        <div className={`fixed bottom-6 left-1/2 -translate-x-1/2 px-5 py-3 rounded-lg text-white shadow-xl z-[10000] ${
          toast.type === 'error' ? 'bg-red-600' : toast.type === 'success' ? 'bg-green-600' : 'bg-slate-800'
        }`}>{toast.msg}</div>
      )}
    </div>
  );
}
