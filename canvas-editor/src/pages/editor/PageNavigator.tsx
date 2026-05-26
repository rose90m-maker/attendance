import { useEditorStore } from '@/stores/editorStore';
import clsx from 'clsx';

export default function PageNavigator() {
  const { project, currentPageId, setCurrentPage, addPage, removePage, duplicatePage, updatePage } = useEditorStore();
  return (
    <div className="h-9 bg-white border-b border-sig-border flex items-center px-3 gap-1 overflow-x-auto flex-shrink-0">
      {project.pages.map((p, i) => (
        <div
          key={p.id}
          className={clsx('group flex items-center gap-1 px-2.5 py-1 rounded text-xs cursor-pointer border',
            p.id === currentPageId ? 'bg-sig-accent-soft border-sig-accent text-sig-accent font-medium'
              : 'border-transparent text-sig-muted hover:bg-sig-bg')}
          onClick={() => setCurrentPage(p.id)}
          onDoubleClick={() => {
            const name = prompt('페이지 이름', p.name || `페이지 ${i + 1}`);
            if (name) updatePage(p.id, { name });
          }}
        >
          <span>{p.name || `페이지 ${i + 1}`}</span>
          <span className="text-sig-muted">· {p.duration}s</span>
          {p.id === currentPageId && (
            <>
              <button className="opacity-0 group-hover:opacity-100 hover:text-sig-accent ml-1"
                onClick={(e) => { e.stopPropagation(); duplicatePage(p.id); }}>⎘</button>
              {project.pages.length > 1 && (
                <button className="opacity-0 group-hover:opacity-100 hover:text-red-600 ml-0.5"
                  onClick={(e) => { e.stopPropagation(); if (confirm('이 페이지를 삭제할까요?')) removePage(p.id); }}>✕</button>
              )}
            </>
          )}
        </div>
      ))}
      <button className="ml-1 w-7 h-7 rounded text-sig-muted hover:bg-sig-bg hover:text-sig-accent"
        onClick={addPage}>＋</button>
    </div>
  );
}
