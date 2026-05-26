import type { ShapeElement } from '@/types/project';

export default function ShapeElement({ element }: { element: ShapeElement }) {
  const p = element.props;
  if (p.kind === 'circle') {
    return <div style={{ width: '100%', height: '100%', background: p.fill, borderRadius: '50%',
      border: p.stroke ? `${p.strokeWidth || 1}px solid ${p.stroke}` : undefined }} />;
  }
  if (p.kind === 'line') {
    return <div style={{ width: '100%', height: 0,
      borderTop: `${p.strokeWidth || 2}px solid ${p.stroke || p.fill}`, marginTop: '50%' }} />;
  }
  return <div style={{ width: '100%', height: '100%', background: p.fill,
    borderRadius: p.borderRadius || 0,
    border: p.stroke ? `${p.strokeWidth || 1}px solid ${p.stroke}` : undefined }} />;
}
