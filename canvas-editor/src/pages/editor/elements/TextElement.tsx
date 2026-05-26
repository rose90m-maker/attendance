import type { TextElement } from '@/types/project';

export default function TextElement({ element }: { element: TextElement }) {
  const p = element.props;
  return (
    <div
      style={{
        width: '100%', height: '100%',
        fontSize: p.fontSize, color: p.color,
        background: p.backgroundColor || 'transparent',
        fontWeight: p.fontWeight || 400,
        fontStyle: p.fontStyle || 'normal',
        fontFamily: p.fontFamily,
        textAlign: p.textAlign || 'left',
        lineHeight: p.lineHeight || 1.4,
        letterSpacing: p.letterSpacing,
        padding: p.padding ?? 8,
        borderRadius: p.borderRadius || 0,
        display: 'flex', alignItems: 'center',
        justifyContent: p.textAlign === 'right' ? 'flex-end' : p.textAlign === 'center' ? 'center' : 'flex-start',
        wordBreak: 'keep-all', whiteSpace: 'pre-wrap',
        overflow: 'hidden', boxSizing: 'border-box',
      }}
    >{p.content}</div>
  );
}
