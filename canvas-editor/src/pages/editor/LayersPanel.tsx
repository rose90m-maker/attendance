import { useEditorStore, useCurrentPage } from '@/stores/editorStore';
import clsx from 'clsx';

const ICONS: Record<string, string> = {
  text: 'T', image: '🖼', video: '🎬', shape: '⬛', widget: '🧩',
};

export default function LayersPanel() {
  const page = useCurrentPage();
  const { selectedIds, selectIds, updateElement, removeElements, bringForward, sendBackward } = useEditorStore();
  const sorted = [...page.elements].sort((a, b) => b.zIndex - a.zIndex);
  return (
    <aside className="w-56 bg-white border-r border-sig-border overflow-y-auto flex-shrink-0">
      <div className="panel-head">레이어 ({sorted.length})</div>
      {sorted.length === 0 && (
        <div className="text-xs text-sig-muted text-center p-6">요소를 추가하면<br />여기에 표시됩니다.</div>
      )}
      <ul className="text-sm">
        {sorted.map(el => {
          const selected = selectedIds.includes(el.id);
          const label = el.type === 'text' ? ((el.props as any).content || '').slice(0, 20) || '텍스트'
            : el.type === 'image' ? '이미지'
            : el.type === 'video' ? '동영상'
            : el.type === 'shape' ? '도형'
            : `위젯(${(el.props as any).kind})`;
          return (
            <li key={el.id}
              className={clsx('flex items-center gap-2 px-3 py-1.5 cursor-pointer border-l-2 group',
                selected ? 'bg-sig-accent-soft border-sig-accent' : 'border-transparent hover:bg-sig-bg')}
              onClick={() => selectIds([el.id])}>
              <span className="w-5 text-center text-sm">{ICONS[el.type]}</span>
              <span className="flex-1 truncate text-xs">{label}</span>
              <div className="opacity-0 group-hover:opacity-100 flex gap-0.5">
                <button onClick={(e) => { e.stopPropagation(); bringForward(el.id); }} className="text-xs hover:text-sig-accent">↑</button>
                <button onClick={(e) => { e.stopPropagation(); sendBackward(el.id); }} className="text-xs hover:text-sig-accent">↓</button>
                <button onClick={(e) => { e.stopPropagation(); updateElement(el.id, { hidden: !el.hidden }); }} className="text-xs">{el.hidden ? '🙈' : '👁'}</button>
                <button onClick={(e) => { e.stopPropagation(); updateElement(el.id, { locked: !el.locked }); }} className="text-xs">{el.locked ? '🔒' : '🔓'}</button>
                <button onClick={(e) => { e.stopPropagation(); removeElements([el.id]); }} className="text-xs hover:text-red-600">✕</button>
              </div>
            </li>
          );
        })}
      </ul>
    </aside>
  );
}
