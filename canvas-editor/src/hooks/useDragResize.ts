import { useCallback, useRef } from 'react';
import { useEditorStore } from '@/stores/editorStore';
import type { AnyElement } from '@/types/project';
import { snap } from '@/utils/id';

export type ResizeHandle = 'nw' | 'n' | 'ne' | 'e' | 'se' | 's' | 'sw' | 'w';

interface DragState {
  type: 'move' | 'resize' | 'rotate';
  handle?: ResizeHandle;
  startX: number; startY: number;
  origX: number; origY: number; origW: number; origH: number; origRot: number;
  zoom: number;
}

export function useDragResize(element: AnyElement) {
  const stateRef = useRef<DragState | null>(null);

  const onPointerDownMove = useCallback((e: React.PointerEvent) => {
    if (element.locked) return;
    e.stopPropagation();
    const { zoom } = useEditorStore.getState();
    stateRef.current = {
      type: 'move',
      startX: e.clientX, startY: e.clientY,
      origX: element.x, origY: element.y,
      origW: element.width, origH: element.height, origRot: element.rotation,
      zoom,
    };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    const ed = useEditorStore.getState();
    if (!ed.selectedIds.includes(element.id)) ed.selectIds([element.id]);
  }, [element]);

  const onPointerDownResize = useCallback((handle: ResizeHandle) => (e: React.PointerEvent) => {
    if (element.locked) return;
    e.stopPropagation();
    const { zoom } = useEditorStore.getState();
    stateRef.current = {
      type: 'resize', handle,
      startX: e.clientX, startY: e.clientY,
      origX: element.x, origY: element.y,
      origW: element.width, origH: element.height, origRot: element.rotation,
      zoom,
    };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  }, [element]);

  const onPointerDownRotate = useCallback((e: React.PointerEvent) => {
    if (element.locked) return;
    e.stopPropagation();
    const { zoom } = useEditorStore.getState();
    stateRef.current = {
      type: 'rotate',
      startX: e.clientX, startY: e.clientY,
      origX: element.x, origY: element.y,
      origW: element.width, origH: element.height, origRot: element.rotation,
      zoom,
    };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  }, [element]);

  const onPointerMove = useCallback((e: React.PointerEvent) => {
    const st = stateRef.current; if (!st) return;
    const dx = (e.clientX - st.startX) / st.zoom;
    const dy = (e.clientY - st.startY) / st.zoom;
    const snapEnabled = useEditorStore.getState().snapEnabled;
    const G = 10;
    const update = useEditorStore.getState().updateElement;

    if (st.type === 'move') {
      const nx = snapEnabled ? snap(st.origX + dx, G) : st.origX + dx;
      const ny = snapEnabled ? snap(st.origY + dy, G) : st.origY + dy;
      update(element.id, { x: nx, y: ny });
    } else if (st.type === 'resize') {
      let nx = st.origX, ny = st.origY, nw = st.origW, nh = st.origH;
      const h = st.handle!;
      if (h.includes('e')) nw = Math.max(20, st.origW + dx);
      if (h.includes('s')) nh = Math.max(20, st.origH + dy);
      if (h.includes('w')) { nw = Math.max(20, st.origW - dx); nx = st.origX + (st.origW - nw); }
      if (h.includes('n')) { nh = Math.max(20, st.origH - dy); ny = st.origY + (st.origH - nh); }
      if (snapEnabled) { nx = snap(nx, G); ny = snap(ny, G); nw = snap(nw, G); nh = snap(nh, G); }
      update(element.id, { x: nx, y: ny, width: nw, height: nh });
    } else if (st.type === 'rotate') {
      update(element.id, { rotation: st.origRot + dx * 0.5 });
    }
  }, [element.id]);

  const onPointerUp = useCallback((e: React.PointerEvent) => {
    if (stateRef.current) {
      (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
      stateRef.current = null;
    }
  }, []);

  return { onPointerDownMove, onPointerDownResize, onPointerDownRotate, onPointerMove, onPointerUp };
}
