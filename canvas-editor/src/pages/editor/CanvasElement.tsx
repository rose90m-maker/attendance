import { useEditorStore } from '@/stores/editorStore';
import type { AnyElement } from '@/types/project';
import { useDragResize, type ResizeHandle } from '@/hooks/useDragResize';
import clsx from 'clsx';
import { useState } from 'react';
import TextEl from './elements/TextElement';
import ImageEl from './elements/ImageElement';
import VideoEl from './elements/VideoElement';
import ShapeEl from './elements/ShapeElement';
import TableEl from './elements/TableElement';
import ClockWidget from './elements/widgets/ClockWidget';
import QRWidget from './elements/widgets/QRWidget';

const HANDLES: ResizeHandle[] = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'];

export default function CanvasElement({ element }: { element: AnyElement }) {
  const selected = useEditorStore(s => s.selectedIds.includes(element.id));
  const selectIds = useEditorStore(s => s.selectIds);
  const toggleSelect = useEditorStore(s => s.toggleSelect);
  const updateElement = useEditorStore(s => s.updateElement);
  const dr = useDragResize(element);
  const [editingText, setEditingText] = useState(false);

  const onClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (e.shiftKey || e.metaKey || e.ctrlKey) toggleSelect(element.id);
    else selectIds([element.id]);
  };

  // 더블클릭 → 인라인 텍스트 편집 (text 요소만)
  const onDoubleClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (element.type === 'text' && !element.locked) {
      setEditingText(true);
    }
  };

  return (
    <div
      className={clsx('element-wrap', selected && 'selected', element.locked && 'locked', element.hidden && 'hidden-el')}
      style={{
        left: element.x, top: element.y,
        width: element.width, height: element.height,
        opacity: element.opacity, zIndex: element.zIndex,
        transform: `rotate(${element.rotation}deg)`,
        transformOrigin: 'center center',
      }}
      onClick={onClick}
      onDoubleClick={onDoubleClick}
      onPointerDown={editingText ? undefined : dr.onPointerDownMove}
      onPointerMove={editingText ? undefined : dr.onPointerMove}
      onPointerUp={editingText ? undefined : dr.onPointerUp}
    >
      {element.type === 'text' && editingText && (
        <textarea
          autoFocus
          defaultValue={element.props.content}
          onBlur={(e) => {
            updateElement(element.id, { props: { ...element.props, content: e.target.value } } as any);
            setEditingText(false);
          }}
          onKeyDown={(e) => { if (e.key === 'Escape') setEditingText(false); }}
          onClick={(e) => e.stopPropagation()}
          onPointerDown={(e) => e.stopPropagation()}
          style={{
            width: '100%', height: '100%',
            fontSize: element.props.fontSize,
            color: element.props.color,
            background: element.props.backgroundColor || 'transparent',
            fontWeight: element.props.fontWeight || 400,
            fontStyle: element.props.fontStyle || 'normal',
            textAlign: element.props.textAlign || 'left',
            lineHeight: element.props.lineHeight || 1.4,
            padding: element.props.padding ?? 8,
            border: '2px dashed #1e40af', outline: 'none',
            resize: 'none', boxSizing: 'border-box',
            wordBreak: 'keep-all',
          }}
        />
      )}
      {element.type === 'text' && !editingText && <TextEl element={element} />}
      {element.type === 'image' && <ImageEl element={element} />}
      {element.type === 'video' && <VideoEl element={element} />}
      {element.type === 'shape' && <ShapeEl element={element} />}
      {element.type === 'table' && <TableEl element={element} />}
      {element.type === 'widget' && element.props.kind === 'clock' && <ClockWidget element={element} />}
      {element.type === 'widget' && element.props.kind === 'qrcode' && <QRWidget element={element} />}

      {selected && !element.locked && (
        <>
          {HANDLES.map(h => (
            <div key={h} className={`resize-handle ${h}`}
              onPointerDown={dr.onPointerDownResize(h)}
              onPointerMove={dr.onPointerMove}
              onPointerUp={dr.onPointerUp}
            />
          ))}
          <div
            className="absolute w-3 h-3 bg-white border-2 border-sig-accent rounded-full cursor-grab"
            style={{ left: '50%', top: -24, transform: 'translate(-50%, 0)' }}
            onPointerDown={dr.onPointerDownRotate}
            onPointerMove={dr.onPointerMove}
            onPointerUp={dr.onPointerUp}
            title="회전"
          />
        </>
      )}
    </div>
  );
}
