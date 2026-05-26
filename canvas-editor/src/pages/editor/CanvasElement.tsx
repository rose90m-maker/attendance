import { useEditorStore } from '@/stores/editorStore';
import type { AnyElement } from '@/types/project';
import { useDragResize, type ResizeHandle } from '@/hooks/useDragResize';
import clsx from 'clsx';
import TextEl from './elements/TextElement';
import ImageEl from './elements/ImageElement';
import VideoEl from './elements/VideoElement';
import ShapeEl from './elements/ShapeElement';
import ClockWidget from './elements/widgets/ClockWidget';
import QRWidget from './elements/widgets/QRWidget';

const HANDLES: ResizeHandle[] = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'];

export default function CanvasElement({ element }: { element: AnyElement }) {
  const selected = useEditorStore(s => s.selectedIds.includes(element.id));
  const selectIds = useEditorStore(s => s.selectIds);
  const toggleSelect = useEditorStore(s => s.toggleSelect);
  const dr = useDragResize(element);

  const onClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (e.shiftKey || e.metaKey || e.ctrlKey) toggleSelect(element.id);
    else selectIds([element.id]);
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
      onPointerDown={dr.onPointerDownMove}
      onPointerMove={dr.onPointerMove}
      onPointerUp={dr.onPointerUp}
    >
      {element.type === 'text' && <TextEl element={element} />}
      {element.type === 'image' && <ImageEl element={element} />}
      {element.type === 'video' && <VideoEl element={element} />}
      {element.type === 'shape' && <ShapeEl element={element} />}
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
