import { useEffect, useRef } from 'react';
import QRCode from 'qrcode';
import type { WidgetElement } from '@/types/project';

export default function QRWidget({ element }: { element: WidgetElement }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const url = (element.props.config?.url as string) || 'https://taein.biz';
  useEffect(() => {
    if (!ref.current) return;
    QRCode.toCanvas(ref.current, url, { width: 600, margin: 1 }, () => {});
  }, [url]);
  return (
    <div style={{
      width: '100%', height: '100%', background: '#fff',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      padding: '5%', pointerEvents: 'none',
    }}>
      <canvas ref={ref} style={{ width: '100%', height: '100%', maxWidth: '100%', maxHeight: '100%' }} />
    </div>
  );
}
