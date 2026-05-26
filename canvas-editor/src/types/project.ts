// ─── 캔버스 콘텐츠 데이터 스키마 (서버 ds_contents.meta JSON에 저장) ───

export type ElementType = 'text' | 'image' | 'video' | 'shape' | 'widget';
export type ShapeKind = 'rect' | 'circle' | 'line';
export type WidgetKind = 'clock' | 'qrcode';
export type TransitionType = 'none' | 'fade' | 'slide' | 'zoom';
export type ImageFit = 'cover' | 'contain' | 'stretch';

export interface BaseElement {
  id: string;
  type: ElementType;
  x: number; y: number; width: number; height: number;
  rotation: number; opacity: number; zIndex: number;
  locked?: boolean; hidden?: boolean;
}

export interface TextElement extends BaseElement {
  type: 'text';
  props: {
    content: string;
    fontSize: number;
    fontFamily?: string;
    fontWeight?: number;
    fontStyle?: 'normal' | 'italic';
    color: string;
    backgroundColor?: string;
    textAlign?: 'left' | 'center' | 'right';
    lineHeight?: number;
    letterSpacing?: number;
    padding?: number;
    borderRadius?: number;
  };
}

export interface ImageElement extends BaseElement {
  type: 'image';
  props: { src: string; fit: ImageFit; borderRadius?: number };
}

export interface VideoElement extends BaseElement {
  type: 'video';
  props: {
    src: string;
    autoplay: boolean; loop: boolean; muted: boolean;
    fit: ImageFit; duration?: number;
  };
}

export interface ShapeElement extends BaseElement {
  type: 'shape';
  props: {
    kind: ShapeKind; fill: string;
    stroke?: string; strokeWidth?: number; borderRadius?: number;
  };
}

export interface WidgetElement extends BaseElement {
  type: 'widget';
  props: { kind: WidgetKind; config: Record<string, any> };
}

export type AnyElement = TextElement | ImageElement | VideoElement | ShapeElement | WidgetElement;

export interface Page {
  id: string;
  name?: string;
  duration: number;
  transition: TransitionType;
  background?: string;
  elements: AnyElement[];
}

export interface DisplayPreset {
  id: string; name: string; width: number; height: number;
}

export const DISPLAY_PRESETS: DisplayPreset[] = [
  { id: 'fullhd-landscape', name: 'Full HD 가로 (1920×1080)', width: 1920, height: 1080 },
  { id: 'fullhd-portrait', name: 'Full HD 세로 (1080×1920)', width: 1080, height: 1920 },
  { id: 'square', name: '정사각형 (1080×1080)', width: 1080, height: 1080 },
  { id: 'uhd-landscape', name: '4K 가로 (3840×2160)', width: 3840, height: 2160 },
  { id: 'wide', name: '와이드 전광판 (3840×1080)', width: 3840, height: 1080 },
];

export interface Project {
  id: string; name: string;
  width: number; height: number;
  pages: Page[];
  createdAt: string; updatedAt: string;
}

export function createEmptyProject(): Project {
  const now = new Date().toISOString();
  return {
    id: `proj_${Date.now()}`,
    name: '새 콘텐츠',
    width: 1920, height: 1080,
    pages: [createEmptyPage()],
    createdAt: now, updatedAt: now,
  };
}

export function createEmptyPage(): Page {
  return {
    id: `page_${Date.now()}_${Math.floor(Math.random() * 1000)}`,
    name: '페이지 1',
    duration: 8, transition: 'fade',
    background: '#ffffff', elements: [],
  };
}

// ─── 사이니지 설정 (서버 메타) ───
export interface SignageSettings {
  duration_sec: number;
  priority: number;
  status: 'active' | 'draft' | 'paused';
  bg_color: string;
  start_at?: string | null;
  end_at?: string | null;
  target_displays: number[];
  tags: string[];
  add_to_default: boolean;
}

export interface DisplayOption { id: number; name: string; location?: string; }

export interface InitialData {
  mode: 'new' | 'edit';
  content_id: number | null;
  content: {
    id: number; title: string;
    project?: Project;
    duration_sec: number; priority: number; status: string;
    bg_color: string;
    start_at?: string | null; end_at?: string | null;
    target_displays: number[]; tags: string[];
  } | null;
  displays: DisplayOption[];
  all_tags: string[];
}
