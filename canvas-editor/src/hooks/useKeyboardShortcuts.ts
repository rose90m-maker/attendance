import { useEffect } from 'react';
import { useEditorStore } from '@/stores/editorStore';
import { uid } from '@/utils/id';
import type { AnyElement } from '@/types/project';

// 메모리 클립보드 (요소 복사용)
let _clipboard: AnyElement[] | null = null;

export function useKeyboardShortcuts() {
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
      const s = useEditorStore.getState();
      const mod = e.ctrlKey || e.metaKey;

      if ((e.key === 'Delete' || e.key === 'Backspace') && s.selectedIds.length > 0) {
        s.removeElements(s.selectedIds); e.preventDefault(); return;
      }
      if (mod && e.key === 'd') { s.duplicateElements(s.selectedIds); e.preventDefault(); return; }
      if (mod && e.key === 'a') { s.selectAll(); e.preventDefault(); return; }
      // Copy
      if (mod && e.key === 'c' && s.selectedIds.length > 0) {
        const page = s.project.pages.find(p => p.id === s.currentPageId);
        if (page) {
          _clipboard = page.elements.filter(el => s.selectedIds.includes(el.id))
            .map(el => JSON.parse(JSON.stringify(el)));
        }
        e.preventDefault(); return;
      }
      // Paste
      if (mod && e.key === 'v' && _clipboard && _clipboard.length > 0) {
        const newIds: string[] = [];
        _clipboard.forEach(el => {
          const copy = JSON.parse(JSON.stringify(el)) as AnyElement;
          delete (copy as any).id;
          copy.x = (copy.x || 0) + 30;
          copy.y = (copy.y || 0) + 30;
          const id = s.addElement(copy as any);
          newIds.push(id);
        });
        if (newIds.length > 0) s.selectIds(newIds);
        e.preventDefault(); return;
      }
      // Cut
      if (mod && e.key === 'x' && s.selectedIds.length > 0) {
        const page = s.project.pages.find(p => p.id === s.currentPageId);
        if (page) {
          _clipboard = page.elements.filter(el => s.selectedIds.includes(el.id))
            .map(el => JSON.parse(JSON.stringify(el)));
          s.removeElements(s.selectedIds);
        }
        e.preventDefault(); return;
      }
      if (mod && !e.shiftKey && e.key === 'z') { (useEditorStore as any).temporal.getState().undo(); e.preventDefault(); return; }
      if (mod && ((e.shiftKey && e.key === 'Z') || e.key === 'y')) { (useEditorStore as any).temporal.getState().redo(); e.preventDefault(); return; }
      if (s.selectedIds.length > 0 && e.key.startsWith('Arrow')) {
        const step = e.shiftKey ? 10 : 1;
        const dx = e.key === 'ArrowLeft' ? -step : e.key === 'ArrowRight' ? step : 0;
        const dy = e.key === 'ArrowUp' ? -step : e.key === 'ArrowDown' ? step : 0;
        s.selectedIds.forEach(id => {
          const page = s.project.pages.find(p => p.id === s.currentPageId);
          const el = page?.elements.find(x => x.id === id);
          if (el) s.updateElement(id, { x: el.x + dx, y: el.y + dy });
        });
        e.preventDefault(); return;
      }
      if (e.key === 'Escape') { s.clearSelection(); e.preventDefault(); }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, []);
}
