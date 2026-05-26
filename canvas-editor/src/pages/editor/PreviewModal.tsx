import { useEffect, useRef, useState } from 'react';
import type { Project, Page, AnyElement } from '@/types/project';
import TextEl from './elements/TextElement';
import ImageEl from './elements/ImageElement';
import VideoEl from './elements/VideoElement';
import ShapeEl from './elements/ShapeElement';
import TableEl from './elements/TableElement';
import ClockWidget from './elements/widgets/ClockWidget';
import QRWidget from './elements/widgets/QRWidget';

interface Props { project: Project; onClose: () => void; }

export default function PreviewModal({ project, onClose }: Props) {
  const [pageIdx, setPageIdx] = useState(0);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(0.4);

  // 페이지 자동 전환
  useEffect(() => {
    if (project.pages.length < 2) return;
    const current = project.pages[pageIdx];
    const t = setTimeout(() => setPageIdx((pageIdx + 1) % project.pages.length), (current?.duration || 8) * 1000);
    return () => clearTimeout(t);
  }, [pageIdx, project.pages]);

  // 캔버스 스케일 자동 (창에 맞춤)
  useEffect(() => {
    const calc = () => {
      const el = wrapRef.current; if (!el) return;
      const w = el.clientWidth - 40, h = el.clientHeight - 40;
      const s = Math.min(w / project.width, h / project.height);
      setScale(s);
    };
    calc();
    const ro = new ResizeObserver(calc);
    if (wrapRef.current) ro.observe(wrapRef.current);
    return () => ro.disconnect();
  }, [project.width, project.height]);

  // ESC로 닫기
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);

  const page = project.pages[pageIdx];
  if (!page) return null;
  return (
    <div className="fixed inset-0 bg-black z-[9999] flex items-center justify-center" ref={wrapRef}>
      <button onClick={onClose} className="absolute top-4 right-4 z-10 px-4 py-2 bg-white text-black rounded font-semibold">
        ✕ 닫기 (ESC)
      </button>
      <div className="absolute top-4 left-4 z-10 px-3 py-1 bg-black/60 text-white text-xs rounded">
        {pageIdx + 1} / {project.pages.length} · {page.name || `페이지 ${pageIdx + 1}`} · {page.duration}s
      </div>
      <div
        className="relative shadow-2xl"
        style={{
          width: project.width, height: project.height,
          background: page.background || '#ffffff',
          backgroundImage: page.backgroundImage ? `url(${page.backgroundImage})` : undefined,
          backgroundSize: page.backgroundFit === 'contain' ? 'contain' : 'cover',
          backgroundPosition: 'center', backgroundRepeat: 'no-repeat',
          transform: `scale(${scale})`, transformOrigin: 'center center',
          flexShrink: 0,
        }}
      >
        {[...page.elements].sort((a, b) => a.zIndex - b.zIndex).map(el => (
          <PreviewElement key={el.id} element={el} />
        ))}
      </div>
    </div>
  );
}

function PreviewElement({ element }: { element: AnyElement }) {
  if (element.hidden) return null;
  const style: React.CSSProperties = {
    position: 'absolute',
    left: element.x, top: element.y, width: element.width, height: element.height,
    opacity: element.opacity, zIndex: element.zIndex,
    transform: `rotate(${element.rotation}deg)`, transformOrigin: 'center center',
  };
  return (
    <div style={style}>
      {element.type === 'text' && <TextEl element={element} />}
      {element.type === 'image' && <ImageEl element={element} />}
      {element.type === 'video' && <VideoEl element={element} />}
      {element.type === 'shape' && <ShapeEl element={element} />}
      {element.type === 'table' && <TableEl element={element} />}
      {element.type === 'widget' && element.props.kind === 'clock' && <ClockWidget element={element} />}
      {element.type === 'widget' && element.props.kind === 'qrcode' && <QRWidget element={element} />}
    </div>
  );
}
