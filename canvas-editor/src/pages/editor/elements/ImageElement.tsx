import type { ImageElement } from '@/types/project';

export default function ImageElement({ element }: { element: ImageElement }) {
  const p = element.props;
  const fit: 'cover' | 'contain' | 'fill' =
    p.fit === 'cover' ? 'cover' : p.fit === 'stretch' ? 'fill' : 'contain';
  return (
    <img src={p.src} alt="" draggable={false}
      style={{ width: '100%', height: '100%', objectFit: fit, borderRadius: p.borderRadius || 0, display: 'block', pointerEvents: 'none' }} />
  );
}
