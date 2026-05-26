import { useEffect, useRef, useState } from 'react';

interface Props {
  html: string;
  bg: string;
  setBg: (c: string) => void;
  blockCount: number;
  onFullscreen: () => void;
}

// 4K 캔버스를 컨테이너 너비에 맞춰 축소 스케일링하는 컴포넌트
function ScaledPreview({ html, bg, isDark }: { html: string; bg: string; isDark: boolean }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(0.15);

  useEffect(() => {
    const update = () => {
      const el = wrapRef.current;
      if (!el) return;
      const w = el.clientWidth;
      // 3840px 폭을 컨테이너 폭에 맞춤
      setScale(w / 3840);
    };
    update();
    const ro = new ResizeObserver(update);
    if (wrapRef.current) ro.observe(wrapRef.current);
    return () => ro.disconnect();
  }, []);

  return (
    <div ref={wrapRef} className="preview-canvas" style={{ background: bg }}>
      <div
        className="pv-4k"
        style={{
          position: 'absolute',
          top: 0, left: 0,
          width: 3840,
          height: 2160,
          transformOrigin: 'top left',
          transform: `scale(${scale})`,
          color: isDark ? '#fff' : '#0f172a',
          padding: '80px 120px',
          fontSize: 56,
          lineHeight: 1.5,
          fontFamily: "'Noto Sans KR', sans-serif",
          overflow: 'hidden',
          boxSizing: 'border-box',
        }}
        dangerouslySetInnerHTML={{
          __html: html ||
            '<p style="opacity:0.4;text-align:center;font-size:48px;">본문을 입력하면 실시간으로 표시됩니다</p>',
        }}
      />
    </div>
  );
}

export default function PreviewPanel({ html, bg, setBg, blockCount, onFullscreen }: Props) {
  const isDark = checkDark(bg);
  return (
    <div className="preview-panel">
      <div className="preview-meta">
        <span>📺 4K 송출 미리보기 (16:9)</span>
        <div className="pv-bg-toggle">
          <button type="button" className={isDark ? 'active' : ''} onClick={() => setBg('#0f172a')} title="어두운 배경">🌙</button>
          <button type="button" className={isDark ? '' : 'active'} onClick={() => setBg('#ffffff')} title="밝은 배경">☀️</button>
          <button type="button" onClick={onFullscreen} title="전체화면">⛶</button>
        </div>
      </div>

      <ScaledPreview html={html} bg={bg} isDark={isDark} />

      <div className="preview-meta">
        <span>📐 3840×2160 (4K UHD)</span>
        <span>{blockCount}개 블록</span>
      </div>
    </div>
  );
}

function checkDark(hex: string): boolean {
  if (!hex || hex[0] !== '#') return true;
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return (0.299 * r + 0.587 * g + 0.114 * b) < 160;
}

// 외부에서 전체화면 미리보기에서도 동일 컴포넌트 재사용 가능하도록 export
export { ScaledPreview };
