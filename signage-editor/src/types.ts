// 사이니지 메타데이터 (에디터와 분리 저장)
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

export interface DisplayOption {
  id: number;
  name: string;
  location?: string;
}

export interface ContentDoc {
  id?: number;
  title: string;
  editor_json: any;      // BlockNote 블록 배열
  body_html: string;     // 미리보기/송출용 HTML
  signage: SignageSettings;
}

export interface InitialData {
  mode: 'new' | 'edit';
  content_id: number | null;
  content: {
    id: number;
    title: string;
    editor_json?: any;
    body_html?: string;
    duration_sec: number;
    priority: number;
    status: string;
    bg_color: string;
    start_at?: string | null;
    end_at?: string | null;
    target_displays: number[];
    tags: string[];
  } | null;
  displays: DisplayOption[];
  all_tags: string[];
  csrf?: string;
}
