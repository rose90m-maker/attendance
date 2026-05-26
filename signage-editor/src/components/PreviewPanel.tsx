import { useState } from 'react';
import type { SignageSettings } from '../types';

interface Props {
  html: string;
  bg: string;
  setBg: (c: string) => void;
  blockCount: number;
  onFullscreen: () => void;
}

export default function PreviewPanel({ html, bg, setBg, blockCount, onFullscreen }: Props) {
  return (
    <div className="preview-panel">
      <div className="preview-meta">
        <span>📺 4K 송출 미리보기 (16:9)</span>
        <div className="pv-bg-toggle">
          <button
            type="button"
            className={isDark(bg) ? 'active' : ''}
            onClick={() => setBg('#0f172a')}
            title="어두운 배경"
          >🌙</button>
          <button
            type="button"
            className={isDark(bg) ? '' : 'active'}
            onClick={() => setBg('#ffffff')}
            title="밝은 배경"
          >☀️</button>
          <button type="button" onClick={onFullscreen} title="전체화면">⛶</button>
        </div>
      </div>

      <div className="preview-canvas" style={{ background: bg }}>
        <div
          className="pv-content"
          style={{ color: isDark(bg) ? '#fff' : '#0f172a' }}
          dangerouslySetInnerHTML={{ __html: html || '<p style="opacity:0.4;text-align:center;">본문을 입력하면 실시간으로 표시됩니다</p>' }}
        />
      </div>

      <div className="preview-meta">
        <span>📐 3840×2160 (4K UHD)</span>
        <span>{blockCount}개 블록</span>
      </div>
    </div>
  );
}

function isDark(hex: string): boolean {
  if (!hex || hex[0] !== '#') return true;
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return (0.299 * r + 0.587 * g + 0.114 * b) < 160;
}
