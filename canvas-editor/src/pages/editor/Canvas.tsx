import { useEffect, useRef } from 'react';
import { useEditorStore, useCurrentPage } from '@/stores/editorStore';
import CanvasElement from './CanvasElement';

export default function Canvas() {
  const project = useEditorStore(s => s.project);
  const zoom = useEditorStore(s => s.zoom);
  const showGrid = useEditorStore(s => s.showGrid);
  const setZoom = useEditorStore(s => s.setZoom);
  const clearSelection = useEditorStore(s => s.clearSelection);
  const page = useCurrentPage();
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = wrapRef.current; if (!el) return;
    const handler = (e: WheelEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      e.preventDefault();
      const factor = e.deltaY > 0 ? 0.9 : 1.1;
      setZoom(useEditorStore.getState().zoom * factor);
    };
    el.addEventListener('wheel', handler, { passive: false });
    return () => el.removeEventListener('wheel', handler);
  }, [setZoom]);

  return (
    <div
      ref={wrapRef}
      className="flex-1 overflow-auto bg-sig-bg flex items-center justify-center p-8"
      onClick={(e) => { if (e.target === e.currentTarget) clearSelection(); }}
    >
      <div
        className="relative bg-white shadow-2xl"
        style={{
          width: project.width, height: project.height,
          transform: `scale(${zoom})`,
          transformOrigin: 'center center',
          background: page.background || '#ffffff',
          backgroundImage: page.backgroundImage ? `url(${page.backgroundImage})` : undefined,
          backgroundSize: page.backgroundFit === 'contain' ? 'contain' : 'cover',
          backgroundPosition: 'center',
          backgroundRepeat: 'no-repeat',
          flexShrink: 0,
        }}
        onClick={(e) => { if (e.target === e.currentTarget) clearSelection(); }}
      >
        {showGrid && <div className="absolute inset-0 pointer-events-none grid-bg opacity-50" />}
        {[...page.elements].sort((a, b) => a.zIndex - b.zIndex).map(el => (
          <CanvasElement key={el.id} element={el} />
        ))}
      </div>
    </div>
  );
}
