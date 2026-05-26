import { create } from 'zustand';
import { temporal } from 'zundo';
import type { Project, Page, AnyElement } from '@/types/project';
import { createEmptyProject, createEmptyPage } from '@/types/project';
import { uid } from '@/utils/id';

interface EditorState {
  project: Project;
  currentPageId: string;
  selectedIds: string[];
  zoom: number;
  showGrid: boolean;
  snapEnabled: boolean;
}

interface EditorActions {
  setProject(p: Project): void;
  setProjectMeta(patch: Partial<Pick<Project, 'name' | 'width' | 'height'>>): void;

  addPage(): string;
  removePage(id: string): void;
  setCurrentPage(id: string): void;
  updatePage(id: string, patch: Partial<Page>): void;
  duplicatePage(id: string): void;

  addElement(el: Omit<AnyElement, 'id' | 'zIndex'>): string;
  updateElement(id: string, patch: Partial<AnyElement>): void;
  removeElements(ids: string[]): void;
  duplicateElements(ids: string[]): void;

  selectIds(ids: string[]): void;
  toggleSelect(id: string): void;
  selectAll(): void;
  clearSelection(): void;

  bringToFront(id: string): void;
  sendToBack(id: string): void;
  bringForward(id: string): void;
  sendBackward(id: string): void;

  setZoom(z: number): void;
  toggleGrid(): void;
  toggleSnap(): void;
}

type Store = EditorState & EditorActions;

function updateCurrentPage(state: EditorState, fn: (page: Page) => Page): Partial<EditorState> {
  return {
    project: {
      ...state.project,
      updatedAt: new Date().toISOString(),
      pages: state.project.pages.map(p => p.id === state.currentPageId ? fn(p) : p),
    },
  };
}

export const useEditorStore = create<Store>()(
  temporal(
    (set, get) => {
      const init = createEmptyProject();
      return {
        project: init,
        currentPageId: init.pages[0].id,
        selectedIds: [],
        zoom: 0.4,
        showGrid: true,
        snapEnabled: true,

        setProject(p) {
          set({ project: p, currentPageId: p.pages[0]?.id ?? '', selectedIds: [] });
        },
        setProjectMeta(patch) {
          set(s => ({ project: { ...s.project, ...patch, updatedAt: new Date().toISOString() } }));
        },

        addPage() {
          const newPage = createEmptyPage();
          newPage.name = `페이지 ${get().project.pages.length + 1}`;
          set(s => ({
            project: { ...s.project, pages: [...s.project.pages, newPage], updatedAt: new Date().toISOString() },
            currentPageId: newPage.id, selectedIds: [],
          }));
          return newPage.id;
        },
        removePage(id) {
          set(s => {
            const remain = s.project.pages.filter(p => p.id !== id);
            if (remain.length === 0) remain.push(createEmptyPage());
            return {
              project: { ...s.project, pages: remain, updatedAt: new Date().toISOString() },
              currentPageId: id === s.currentPageId ? remain[0].id : s.currentPageId,
              selectedIds: [],
            };
          });
        },
        setCurrentPage(id) { set({ currentPageId: id, selectedIds: [] }); },
        updatePage(id, patch) {
          set(s => ({
            project: {
              ...s.project, updatedAt: new Date().toISOString(),
              pages: s.project.pages.map(p => p.id === id ? { ...p, ...patch } : p),
            },
          }));
        },
        duplicatePage(id) {
          set(s => {
            const src = s.project.pages.find(p => p.id === id);
            if (!src) return s;
            const copy: Page = { ...JSON.parse(JSON.stringify(src)), id: uid('page'), name: (src.name || '페이지') + ' 복사본' };
            const idx = s.project.pages.findIndex(p => p.id === id);
            const pages = [...s.project.pages]; pages.splice(idx + 1, 0, copy);
            return { project: { ...s.project, pages, updatedAt: new Date().toISOString() }, currentPageId: copy.id };
          });
        },

        addElement(el) {
          const state = get();
          const page = state.project.pages.find(p => p.id === state.currentPageId);
          if (!page) return '';
          const maxZ = page.elements.reduce((m, e) => Math.max(m, e.zIndex), 0);
          const id = uid('el');
          const newEl = { ...el, id, zIndex: maxZ + 1 } as AnyElement;
          set(s => updateCurrentPage(s, p => ({ ...p, elements: [...p.elements, newEl] })));
          set({ selectedIds: [id] });
          return id;
        },
        updateElement(id, patch) {
          set(s => updateCurrentPage(s, p => ({
            ...p, elements: p.elements.map(e => e.id === id ? { ...e, ...patch } as AnyElement : e),
          })));
        },
        removeElements(ids) {
          const idSet = new Set(ids);
          set(s => updateCurrentPage(s, p => ({ ...p, elements: p.elements.filter(e => !idSet.has(e.id)) })));
          set(s => ({ selectedIds: s.selectedIds.filter(id => !idSet.has(id)) }));
        },
        duplicateElements(ids) {
          const idSet = new Set(ids);
          const newIds: string[] = [];
          set(s => updateCurrentPage(s, p => {
            const copies = p.elements.filter(e => idSet.has(e.id)).map(e => {
              const newId = uid('el'); newIds.push(newId);
              return { ...JSON.parse(JSON.stringify(e)), id: newId, x: e.x + 30, y: e.y + 30, zIndex: 0 } as AnyElement;
            });
            const maxZ = Math.max(0, ...p.elements.map(e => e.zIndex));
            copies.forEach((c, i) => { c.zIndex = maxZ + 1 + i; });
            return { ...p, elements: [...p.elements, ...copies] };
          }));
          set({ selectedIds: newIds });
        },

        selectIds(ids) { set({ selectedIds: ids }); },
        toggleSelect(id) {
          set(s => ({
            selectedIds: s.selectedIds.includes(id) ? s.selectedIds.filter(i => i !== id) : [...s.selectedIds, id],
          }));
        },
        selectAll() {
          const p = get().project.pages.find(p => p.id === get().currentPageId);
          set({ selectedIds: p ? p.elements.map(e => e.id) : [] });
        },
        clearSelection() { set({ selectedIds: [] }); },

        bringToFront(id) {
          set(s => updateCurrentPage(s, p => {
            const maxZ = Math.max(0, ...p.elements.map(e => e.zIndex));
            return { ...p, elements: p.elements.map(e => e.id === id ? { ...e, zIndex: maxZ + 1 } : e) };
          }));
        },
        sendToBack(id) {
          set(s => updateCurrentPage(s, p => {
            const minZ = Math.min(0, ...p.elements.map(e => e.zIndex));
            return { ...p, elements: p.elements.map(e => e.id === id ? { ...e, zIndex: minZ - 1 } : e) };
          }));
        },
        bringForward(id) {
          set(s => updateCurrentPage(s, p => ({ ...p, elements: p.elements.map(e => e.id === id ? { ...e, zIndex: e.zIndex + 1 } : e) })));
        },
        sendBackward(id) {
          set(s => updateCurrentPage(s, p => ({ ...p, elements: p.elements.map(e => e.id === id ? { ...e, zIndex: e.zIndex - 1 } : e) })));
        },

        setZoom(z) { set({ zoom: Math.max(0.05, Math.min(z, 4)) }); },
        toggleGrid() { set(s => ({ showGrid: !s.showGrid })); },
        toggleSnap() { set(s => ({ snapEnabled: !s.snapEnabled })); },
      };
    },
    {
      partialize: (state) => ({
        project: state.project,
        currentPageId: state.currentPageId,
      }) as any,
      limit: 50,
    }
  )
);

export const useCurrentPage = () => useEditorStore(s =>
  s.project.pages.find(p => p.id === s.currentPageId) ?? s.project.pages[0]
);
