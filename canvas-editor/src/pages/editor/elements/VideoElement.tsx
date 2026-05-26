import type { VideoElement } from '@/types/project';

export default function VideoElement({ element }: { element: VideoElement }) {
  const p = element.props;
  const fit: 'cover' | 'contain' | 'fill' =
    p.fit === 'cover' ? 'cover' : p.fit === 'stretch' ? 'fill' : 'contain';
  return (
    <video src={p.src} autoPlay={p.autoplay} loop={p.loop} muted={p.muted} playsInline
      style={{ width: '100%', height: '100%', objectFit: fit, display: 'block', pointerEvents: 'none' }} />
  );
}
