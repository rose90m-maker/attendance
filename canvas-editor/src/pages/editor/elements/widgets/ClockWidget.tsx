import { useEffect, useState } from 'react';
import type { WidgetElement } from '@/types/project';

export default function ClockWidget({ element }: { element: WidgetElement }) {
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  const cfg = element.props.config || {};
  const hh = String(now.getHours()).padStart(2, '0');
  const mm = String(now.getMinutes()).padStart(2, '0');
  const ss = String(now.getSeconds()).padStart(2, '0');
  const date = `${now.getFullYear()}.${String(now.getMonth() + 1).padStart(2, '0')}.${String(now.getDate()).padStart(2, '0')}`;
  const weeks = ['일','월','화','수','목','금','토'];
  return (
    <div style={{
      width: '100%', height: '100%',
      display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center',
      color: '#fff', textShadow: '0 2px 6px rgba(0,0,0,0.4)',
      pointerEvents: 'none',
    }}>
      <div style={{ fontSize: '46%', fontWeight: 900, letterSpacing: '0.05em', lineHeight: 1 }}>
        {hh}:{mm}<span style={{ opacity: 0.6 }}>:{ss}</span>
      </div>
      {cfg.showDate !== false && (
        <div style={{ fontSize: '12%', opacity: 0.85, marginTop: '4%' }}>
          {date} ({weeks[now.getDay()]})
        </div>
      )}
    </div>
  );
}
