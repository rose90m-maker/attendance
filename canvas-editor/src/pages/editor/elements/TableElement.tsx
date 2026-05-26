import { useEditorStore } from '@/stores/editorStore';
import type { TableElement } from '@/types/project';

export default function TableElement({ element }: { element: TableElement }) {
  const updateElement = useEditorStore(s => s.updateElement);
  const selected = useEditorStore(s => s.selectedIds.includes(element.id));
  const p = element.props;
  const cells = p.cells || [[{ text: '' }]];
  const rows = cells.length;
  const cols = Math.max(...cells.map(r => r.length));

  const updateCell = (r: number, c: number, patch: Partial<typeof cells[0][0]>) => {
    const newCells = cells.map((row, ri) =>
      row.map((cell, ci) => (ri === r && ci === c) ? { ...cell, ...patch } : cell)
    );
    updateElement(element.id, { props: { ...p, cells: newCells } } as any);
  };

  return (
    <div style={{
      width: '100%', height: '100%',
      display: 'flex', alignItems: 'stretch', justifyContent: 'stretch',
    }}>
      <table style={{
        width: '100%', height: '100%',
        borderCollapse: 'collapse',
        tableLayout: 'fixed',
        color: p.color || '#1f2937',
        fontSize: p.fontSize || 24,
      }}>
        <tbody>
          {cells.map((row, ri) => (
            <tr key={ri}>
              {row.map((cell, ci) => {
                if (cell._covered) return null;
                const isHeader = (
                  (p.headerStyle === 'first-row' && ri === 0) ||
                  (p.headerStyle === 'first-col' && ci === 0) ||
                  (p.headerStyle === 'first-both' && (ri === 0 || ci === 0))
                );
                return (
                  <td
                    key={ci}
                    colSpan={cell.colspan || 1}
                    rowSpan={cell.rowspan || 1}
                    style={{
                      border: `${p.borderWidth || 1}px solid ${p.borderColor || '#d1d5db'}`,
                      padding: p.padding ?? 8,
                      textAlign: cell.align || 'center',
                      fontWeight: cell.bold || isHeader ? 700 : 400,
                      fontStyle: cell.italic ? 'italic' : 'normal',
                      background: cell.bg || (isHeader ? (p.headerBg || '#1e40af') : 'transparent'),
                      color: cell.color || (isHeader ? (p.headerColor || '#ffffff') : (p.color || '#1f2937')),
                      verticalAlign: 'middle',
                      overflow: 'hidden',
                    }}
                  >
                    {selected ? (
                      <input
                        type="text"
                        value={cell.text || ''}
                        onChange={(e) => updateCell(ri, ci, { text: e.target.value })}
                        onClick={(e) => e.stopPropagation()}
                        onPointerDown={(e) => e.stopPropagation()}
                        style={{
                          width: '100%', height: '100%',
                          border: 'none', outline: 'none', background: 'transparent',
                          textAlign: 'inherit', fontSize: 'inherit', fontWeight: 'inherit',
                          color: 'inherit', fontStyle: 'inherit',
                        }}
                      />
                    ) : (cell.text || '')}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
